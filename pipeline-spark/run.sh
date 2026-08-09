#!/usr/bin/env bash
set -eo pipefail

OPTIONS=/data/options.json
: "${SPARK_HOME:=/opt/spark}"

WORKER_MEMORY="$(jq -r '.worker_memory // "2G"' "$OPTIONS")"
WORKER_CORES="$(jq -r '.worker_cores // 2' "$OPTIONS")"
MINIO_ENDPOINT="$(jq -r '.minio_endpoint // "http://172.30.32.1:9000"' "$OPTIONS")"
MINIO_KEY="$(jq -r '.minio_access_key' "$OPTIONS")"
MINIO_SECRET="$(jq -r '.minio_secret_key' "$OPTIONS")"
METASTORE_URIS="$(jq -r '.metastore_uris // ""' "$OPTIONS")"
METASTORE_JARS="$(jq -r '.metastore_jars // "path"' "$OPTIONS")"

# The image has no writable $SPARK_HOME/conf, so keep our config (and the
# worker scratch dir + Ivy cache) under /data and point Spark at it.
export SPARK_CONF_DIR=/data/spark/conf
export SPARK_WORKER_DIR=/data/spark/work
export HOME=/data/spark
mkdir -p "$SPARK_CONF_DIR" "$SPARK_WORKER_DIR" /data/spark/.ivy2
# Bind on all interfaces so the host-published ports reach master/worker.
export SPARK_LOCAL_IP=0.0.0.0

# ...but 0.0.0.0 is a bind address, not a reachable one, and Spark advertises
# whatever it binds to. The master's UI builds its "Workers" link from what the
# worker registered with, so it produced http://0.0.0.0:8081 — a dead link on
# every install. SPARK_PUBLIC_DNS is the knob Spark provides for exactly this:
# it changes only what the web UIs put in links, never what anything binds to
# or how master, worker and Connect find each other.
#
# It cannot be derived here: the browser is on the LAN and this container is on
# the Supervisor's bridge network, so nothing visible from inside is routable
# from outside, and the gateway address (172.30.32.1) reaches this add-on only
# from a sibling add-on.
#
# The default is the host's mDNS name rather than its IP, deliberately. Home
# Assistant OS runs avahi and advertises <hostname>.local, which survives a DHCP
# lease change — an IP written into a config file does not, and would work until
# the router handed out a different one and then fail in a way that looks like
# the add-on broke. A name also stays correct across a rebuild.
PUBLIC_HOST="$(jq -r '.public_host // ""' "$OPTIONS")"
if [ -n "$PUBLIC_HOST" ]; then
    export SPARK_PUBLIC_DNS="$PUBLIC_HOST"
    echo "[Pipeline Spark] web UI links point at ${PUBLIC_HOST} (master UI:" \
         "http://${PUBLIC_HOST}:8082, worker UI: http://${PUBLIC_HOST}:8083)."
    echo "[Pipeline Spark] if those do not open, public_host does not match how" \
         "you reach this machine — set it to your host's name or address."
else
    echo "[Pipeline Spark] public_host is empty — the master UI's Workers link" \
         "will read 0.0.0.0 and not resolve. Set it to this host's name."
fi

# Compose spark-defaults: baked packages/REST settings + runtime S3A (MinIO).
CONF="$SPARK_CONF_DIR/spark-defaults.conf"
cp /opt/pipeline/base-defaults.conf "$CONF"
cat >> "$CONF" <<EOF
spark.jars.ivy /data/spark/.ivy2
spark.hadoop.fs.s3a.endpoint ${MINIO_ENDPOINT}
spark.hadoop.fs.s3a.access.key ${MINIO_KEY}
spark.hadoop.fs.s3a.secret.key ${MINIO_SECRET}
spark.hadoop.fs.s3a.path.style.access true
spark.hadoop.fs.s3a.connection.ssl.enabled false
spark.hadoop.fs.s3a.aws.credentials.provider org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider
EOF

# Empty (the default) leaves Spark on its session-local in-memory catalog, which
# is what every version before 1.4.0 did: tables are addressed by path only.
# Point this at the Pipeline Metastore add-on and table names become real.
#
# The version has to be declared because Spark's built-in Hive client is 2.3.10
# and cannot speak to a 4.1.0 metastore, so a matching client is loaded into an
# isolated classloader.
#
# `path` uses the jars baked into the image at build time. `maven` resolves them
# over the network instead — a ~270-module Ivy resolution on *every* Connect
# server start, which measured over four minutes on a real install and repeats
# with a warm cache, because the metadata lookup still goes to Maven Central.
# It is kept only as an escape hatch if the baked jars ever prove wrong.
if [ -n "$METASTORE_URIS" ]; then
    cat >> "$CONF" <<EOF
spark.sql.catalogImplementation hive
spark.hadoop.hive.metastore.uris ${METASTORE_URIS}
spark.sql.hive.metastore.version 4.1.0
EOF
    if [ "$METASTORE_JARS" = "maven" ]; then
        echo "spark.sql.hive.metastore.jars maven" >> "$CONF"
        echo "[Pipeline Spark] Hive catalog -> ${METASTORE_URIS} (client from Maven," \
             "expect a slow first query after every restart)"
    else
        cat >> "$CONF" <<EOF
spark.sql.hive.metastore.jars path
spark.sql.hive.metastore.jars.path file:///opt/pipeline/hive-jars/*
EOF
        echo "[Pipeline Spark] Hive catalog -> ${METASTORE_URIS}" \
             "(client: $(ls /opt/pipeline/hive-jars/*.jar 2>/dev/null | wc -l) jars from the image)"
    fi
fi

echo "[Pipeline Spark] uid=$(id -u); wrote $CONF"
echo "[Pipeline Spark] starting master (RPC :7077, REST :6066, UI :8080)"
"${SPARK_HOME}/bin/spark-class" org.apache.spark.deploy.master.Master \
    --port 7077 --webui-port 8080 &
MASTER_PID=$!

# Give the master a moment to bind before the worker registers.
sleep 5
# 8083 inside the container, not 8081, so it matches the host port it is
# published on. The master builds the Workers link from the worker's own view of
# itself, which knows nothing of Home Assistant's port remapping — so a
# container port that differs from its host port produces a link to a port
# nothing is listening on. Aligning them is the fix; the master stays on 8080
# because the Add-on Watchdog probes it there.
echo "[Pipeline Spark] starting worker (${WORKER_CORES} cores, ${WORKER_MEMORY}) -> UI :8083"
"${SPARK_HOME}/bin/spark-class" org.apache.spark.deploy.worker.Worker \
    "spark://localhost:7077" \
    --cores "$WORKER_CORES" --memory "$WORKER_MEMORY" --webui-port 8083 &
WORKER_PID=$!

# Spark Connect: the driver for Airflow's jobs, running here rather than in the
# Airflow add-on. Standalone can't run a PySpark driver in cluster mode, so the
# alternative was a driver inside Airflow — sharing that container's memory with
# the scheduler, and needing the executors to call back across the add-on
# boundary. With Connect the whole conversation is gRPC on :15002 and everything
# else stays inside this container.
#
# Started with spark-submit rather than sbin/start-connect-server.sh: that
# script hands off to spark-daemon.sh, which backgrounds the process and returns
# success immediately, so a server that died on startup would look like a
# healthy one. This form stays in the foreground and can be supervised.
sleep 3
echo "[Pipeline Spark] starting Connect server (gRPC :15002)"
"${SPARK_HOME}/bin/spark-submit" \
    --class org.apache.spark.sql.connect.service.SparkConnectServer \
    --name "Spark Connect server" \
    --master "spark://localhost:7077" \
    --conf spark.connect.grpc.binding.address=0.0.0.0 \
    --conf spark.connect.grpc.binding.port=15002 \
    --conf spark.ui.port=4040 \
    --conf spark.ui.enabled=true \
    &
CONNECT_PID=$!

# If any of them exits, stop the container so Supervisor restarts it.
wait -n "$MASTER_PID" "$WORKER_PID" "$CONNECT_PID"
