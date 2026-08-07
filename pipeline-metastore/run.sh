#!/usr/bin/env bash
set -eo pipefail

OPTIONS=/data/options.json
: "${METASTORE_HOME:=/opt/hive-metastore}"
: "${HADOOP_HOME:=/opt/hadoop}"

PG_HOST="$(jq -r '.postgres_host // "172.30.32.1"' "$OPTIONS")"
PG_PORT="$(jq -r '.postgres_port // 5432' "$OPTIONS")"
PG_ADMIN_USER="$(jq -r '.postgres_admin_user' "$OPTIONS")"
PG_ADMIN_PASSWORD="$(jq -r '.postgres_admin_password' "$OPTIONS")"
PG_ADMIN_DB="$(jq -r '.postgres_admin_db // "pipeline"' "$OPTIONS")"
DB_NAME="$(jq -r '.metastore_db // "metastore"' "$OPTIONS")"
DB_USER="$(jq -r '.metastore_db_user // "hive"' "$OPTIONS")"
DB_PASSWORD="$(jq -r '.metastore_db_password' "$OPTIONS")"
WAREHOUSE_DIR="$(jq -r '.warehouse_dir // "s3a://lakehouse/warehouse"' "$OPTIONS")"
MINIO_ENDPOINT="$(jq -r '.minio_endpoint // "http://172.30.32.1:9000"' "$OPTIONS")"
MINIO_KEY="$(jq -r '.minio_access_key' "$OPTIONS")"
MINIO_SECRET="$(jq -r '.minio_secret_key' "$OPTIONS")"
HEAP_MB="$(jq -r '.heap_mb // 512' "$OPTIONS")"

# Config carries the database password, so it is generated per start into the
# add-on's own volume rather than baked into the image.
export METASTORE_CONF_DIR=/data/metastore/conf
export HADOOP_CONF_DIR=/data/metastore/hadoop-conf
export HADOOP_HEAPSIZE="$HEAP_MB"
export HOME=/data/metastore
mkdir -p "$METASTORE_CONF_DIR" "$HADOOP_CONF_DIR"
chmod 700 /data/metastore

# Both conf dirs start as a copy of the shipped ones, because generating a
# directory that holds *only* our own file silently drops the distribution's
# logging config — the conf dir is the whole of the classpath prefix, so
# -Dlog4j.configurationFile=metastore-log4j2.properties then resolves to
# nothing. Hadoop at least complains ("log4j.properties is not found"); the
# metastore just fails to configure log4j2 and runs near-silently, ~20 lines a
# boot with no audit trail, which is the worst way to debug an add-on.
cp -a "${HADOOP_HOME}/etc/hadoop/." "$HADOOP_CONF_DIR/"
cp -a "${METASTORE_HOME}/conf/." "$METASTORE_CONF_DIR/"

# That shipped config points its root logger at a rolling *file* under
# java.io.tmpdir, which in an add-on is a log nobody will ever read — the
# Supervisor log pane shows stdout/stderr. The appender is chosen by a system
# property, so console is one -D away and upstream's per-logger levels
# (DataNucleus, JPOX and friends, all pinned to ERROR) survive intact.
export HADOOP_CLIENT_OPTS="-Dmetastore.root.logger=console ${HADOOP_CLIENT_OPTS:-}"

# Option values land inside XML text nodes; a password containing & or < would
# otherwise produce a config file the metastore cannot parse.
xml_escape() {
    printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

prop() {
    printf '  <property><name>%s</name><value>%s</value></property>\n' \
        "$1" "$(xml_escape "$2")"
}

# --- 1. the metastore's own database -----------------------------------------
#
# The Postgres add-on provisions Airflow's database from an initdb hook, which
# only ever runs on a first-time data directory — useless for an add-on added
# later, so the metastore creates its own role and database instead. Both
# statements are guarded, so this is a no-op from the second start onwards.
# %I / %L quote the identifiers and the literal, so an awkward name or password
# cannot break out of the statement.
echo "[Pipeline Metastore] ensuring role '${DB_USER}' and database '${DB_NAME}' on ${PG_HOST}:${PG_PORT}"
PGPASSWORD="$PG_ADMIN_PASSWORD" psql -q -v ON_ERROR_STOP=1 \
    -h "$PG_HOST" -p "$PG_PORT" -U "$PG_ADMIN_USER" -d "$PG_ADMIN_DB" \
    -v db="$DB_NAME" -v duser="$DB_USER" -v dpw="$DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'duser', :'dpw')
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'duser')\gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'duser')
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'db')\gexec
SQL

# --- 2. configuration ---------------------------------------------------------
CONF="$METASTORE_CONF_DIR/metastore-site.xml"
{
    echo '<?xml version="1.0" encoding="UTF-8"?>'
    echo '<configuration>'
    prop metastore.thrift.port 9083
    prop metastore.warehouse.dir "$WAREHOUSE_DIR"
    prop javax.jdo.option.ConnectionURL \
         "jdbc:postgresql://${PG_HOST}:${PG_PORT}/${DB_NAME}"
    prop javax.jdo.option.ConnectionDriverName org.postgresql.Driver
    prop javax.jdo.option.ConnectionUserName "$DB_USER"
    prop javax.jdo.option.ConnectionPassword "$DB_PASSWORD"
    # The schema is owned by schematool below; DataNucleus must not quietly
    # invent tables of its own, and a version mismatch should be loud.
    prop datanucleus.schema.autoCreateAll false
    prop metastore.schema.verification true
    # Partition-expression filtering lives in hive-exec, which a standalone
    # metastore does not ship. The default proxy is the one that works without
    # it — naming it explicitly keeps a Hive default change from breaking start.
    prop metastore.expression.proxy \
         org.apache.hadoop.hive.metastore.DefaultPartitionExpressionProxy
    echo '</configuration>'
} > "$CONF"
chmod 600 "$CONF"

# fs.* belongs in core-site.xml: it is the Hadoop FileSystem layer, not the
# metastore, that resolves s3a:// when a table directory is created.
CORE="$HADOOP_CONF_DIR/core-site.xml"
{
    echo '<?xml version="1.0" encoding="UTF-8"?>'
    echo '<configuration>'
    prop fs.s3a.endpoint "$MINIO_ENDPOINT"
    prop fs.s3a.access.key "$MINIO_KEY"
    prop fs.s3a.secret.key "$MINIO_SECRET"
    prop fs.s3a.path.style.access true
    prop fs.s3a.connection.ssl.enabled false
    prop fs.s3a.aws.credentials.provider \
         org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider
    echo '</configuration>'
} > "$CORE"
chmod 600 "$CORE"

# --- 3. schema ----------------------------------------------------------------
#
# -info succeeds only against an initialised schema, which makes it the test for
# whether this is a first start. Kept separate from -initSchema so an existing
# schema is never touched: -initSchema on a populated database fails loudly, and
# a metastore schema is not something to re-create by accident.
if "${METASTORE_HOME}/bin/schematool" -dbType postgres -info >/dev/null 2>&1; then
    echo "[Pipeline Metastore] metastore schema present"
else
    echo "[Pipeline Metastore] initialising the metastore schema (first start)"
    "${METASTORE_HOME}/bin/schematool" -dbType postgres -initSchema
fi

# --- 4. serve -----------------------------------------------------------------
echo "[Pipeline Metastore] starting Hive metastore 4.1.0 (thrift :9083, warehouse ${WAREHOUSE_DIR})"
exec "${METASTORE_HOME}/bin/start-metastore"
