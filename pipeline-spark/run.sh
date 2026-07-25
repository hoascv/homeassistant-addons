#!/usr/bin/env bash
set -eo pipefail

OPTIONS=/data/options.json
: "${SPARK_HOME:=/opt/spark}"

WORKER_MEMORY="$(jq -r '.worker_memory // "2G"' "$OPTIONS")"
WORKER_CORES="$(jq -r '.worker_cores // 2' "$OPTIONS")"
MINIO_ENDPOINT="$(jq -r '.minio_endpoint // "http://172.30.32.1:9000"' "$OPTIONS")"
MINIO_KEY="$(jq -r '.minio_access_key' "$OPTIONS")"
MINIO_SECRET="$(jq -r '.minio_secret_key' "$OPTIONS")"

# The image has no writable $SPARK_HOME/conf, so keep our config (and the
# worker scratch dir + Ivy cache) under /data and point Spark at it.
export SPARK_CONF_DIR=/data/spark/conf
export SPARK_WORKER_DIR=/data/spark/work
export HOME=/data/spark
mkdir -p "$SPARK_CONF_DIR" "$SPARK_WORKER_DIR" /data/spark/.ivy2
# Bind on all interfaces so the host-published ports reach master/worker.
export SPARK_LOCAL_IP=0.0.0.0

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

echo "[Pipeline Spark] uid=$(id -u); wrote $CONF"
echo "[Pipeline Spark] starting master (RPC :7077, REST :6066, UI :8080)"
"${SPARK_HOME}/bin/spark-class" org.apache.spark.deploy.master.Master \
    --port 7077 --webui-port 8080 &
MASTER_PID=$!

# Give the master a moment to bind before the worker registers.
sleep 5
echo "[Pipeline Spark] starting worker (${WORKER_CORES} cores, ${WORKER_MEMORY}) -> UI :8081"
"${SPARK_HOME}/bin/spark-class" org.apache.spark.deploy.worker.Worker \
    "spark://localhost:7077" \
    --cores "$WORKER_CORES" --memory "$WORKER_MEMORY" --webui-port 8081 &
WORKER_PID=$!

# If either process exits, stop the container so Supervisor restarts it.
wait -n "$MASTER_PID" "$WORKER_PID"
