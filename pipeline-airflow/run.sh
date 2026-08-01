#!/usr/bin/env bash
set -e

# Airflow (and the providers) are installed in the airflow user's site under
# /home/airflow/.local. We run as root so we can write /data and /share, so
# point HOME there for Python's user-site to resolve those packages.
export HOME=/home/airflow

OPTIONS=/data/options.json
opt() { jq -r "$1" "$OPTIONS"; }

ADMIN_USER="$(opt '.admin_user')"
ADMIN_PASSWORD="$(opt '.admin_password')"
PG_HOST="$(opt '.postgres_host // "172.30.32.1"')"
PG_PORT="$(opt '.postgres_port // 5432')"
AIRFLOW_DB_PASSWORD="$(opt '.airflow_db_password')"
SPARK_MASTER="$(opt '.spark_master // "172.30.32.1"')"
SPARK_PORT="$(opt '.spark_port // 7077')"
export MINIO_ENDPOINT="$(opt '.minio_endpoint // "http://172.30.32.1:9000"')"
export MINIO_KEY="$(opt '.minio_access_key')"
export MINIO_SECRET="$(opt '.minio_secret_key')"
PIPELINE_PG_USER="$(opt '.pipeline_pg_user // "pipeline"')"
PIPELINE_PG_PASSWORD="$(opt '.pipeline_pg_password // ""')"
PIPELINE_PG_DB="$(opt '.pipeline_pg_db // "pipeline"')"

# --- Core Airflow config ---------------------------------------------------
export AIRFLOW__CORE__EXECUTOR=LocalExecutor
export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN="postgresql+psycopg2://airflow:${AIRFLOW_DB_PASSWORD}@${PG_HOST}:${PG_PORT}/airflow"
export AIRFLOW__CORE__LOAD_EXAMPLES=False
export AIRFLOW__CORE__DAGS_FOLDER=/share/pipeline-airflow/dags

# Persist the Fernet key (encrypts connection secrets) across restarts.
FERNET_FILE=/data/fernet.key
if [ ! -f "$FERNET_FILE" ]; then
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > "$FERNET_FILE"
fi
export AIRFLOW__CORE__FERNET_KEY="$(cat "$FERNET_FILE")"

# Persist the session secret too, or every restart logs everyone out.
SECRET_FILE=/data/session.key
if [ ! -f "$SECRET_FILE" ]; then
  python -c "import secrets; print(secrets.token_urlsafe(32))" > "$SECRET_FILE"
fi
export AIRFLOW__API__SECRET_KEY="$(cat "$SECRET_FILE")"

# Use the FAB auth manager, so the admin_user/admin_password options below are
# actually honoured. Airflow 3 otherwise defaults to SimpleAuthManager, whose
# generated password is printed once to the log and stored in plaintext.
export AIRFLOW__CORE__AUTH_MANAGER="airflow.providers.fab.auth_manager.fab_auth_manager.FabAuthManager"

# Seed the example DAG into the shared, user-editable folder (once) — yours to
# edit, so it is never overwritten.
mkdir -p /share/pipeline-airflow/dags
cp -n /opt/airflow/project-dags/example_pipeline.py /share/pipeline-airflow/dags/ 2>/dev/null || true

# The tracker DAGs ship with the add-on and are replaced on every start.
# With cp -n a fix to them could never reach an installation that already had
# the old copy. Edit them in the repository, not here.
cp -f /opt/airflow/project-dags/trackers_ingest.py /share/pipeline-airflow/dags/ 2>/dev/null || true

# --- Database and admin user ------------------------------------------------
# Done here rather than through the entrypoint's _AIRFLOW_WWW_USER_CREATE,
# because FAB keeps its tables in a separate migration that has to run first,
# and because that path only ever creates a user: it silently does nothing if
# one exists, so changing admin_password later would never take effect.

# --- Pre-wired connections (via env, always available) ---------------------
export AIRFLOW_CONN_SPARK_DEFAULT="spark://${SPARK_MASTER}:${SPARK_PORT}"
export AIRFLOW_CONN_PIPELINE_PG="postgres://${PIPELINE_PG_USER}:${PIPELINE_PG_PASSWORD}@${PG_HOST}:${PG_PORT}/${PIPELINE_PG_DB}"
# S3/MinIO connection with endpoint override (values URL-encoded safely).
AIRFLOW_CONN_MINIO_DEFAULT="$(python - <<'PYEOF'
import os, urllib.parse
q = lambda v: urllib.parse.quote(v, safe="")
print(f"aws://{q(os.environ['MINIO_KEY'])}:{q(os.environ['MINIO_SECRET'])}@/"
      f"?region_name=us-east-1&endpoint_url={q(os.environ['MINIO_ENDPOINT'])}")
PYEOF
)"
export AIRFLOW_CONN_MINIO_DEFAULT

# --- Values the example DAG reads (Airflow Variables via env) --------------
export AIRFLOW_VAR_PG_HOST="$PG_HOST"
export AIRFLOW_VAR_PG_PORT="$PG_PORT"
export AIRFLOW_VAR_PIPELINE_PG_DB="$PIPELINE_PG_DB"
export AIRFLOW_VAR_PIPELINE_PG_USER="$PIPELINE_PG_USER"
export AIRFLOW_VAR_PIPELINE_PG_PASSWORD="$PIPELINE_PG_PASSWORD"
export AIRFLOW_VAR_SPARK_JOB_PATH="/opt/pipeline/jobs/example_job.py"

# --- Spark client configuration --------------------------------------------
# Jobs are submitted in client mode, because Spark standalone supports cluster
# deploy mode only for JVM applications and rejects a .py one outright. That
# means the *driver* runs in this container, so this container's spark-defaults
# is the driver's configuration: without it the driver has no S3A credentials
# and no Delta jars, however well configured the workers are. Written to a file
# rather than passed per-task so the MinIO secret never reaches a command line
# — a failed spark-submit echoes its whole invocation into the task log.
SPARK_CONF_DIR="${SPARK_HOME}/conf"
mkdir -p "$SPARK_CONF_DIR"

# hadoop-aws has to match the Hadoop this client was built against, or its AWS
# SDK dependency arrives at a version S3AFileSystem cannot use. Read from the
# jars rather than hardcoded, so a Spark bump can't silently desync it.
HADOOP_VER="$(ls "${SPARK_HOME}"/jars/hadoop-client-api-*.jar 2>/dev/null \
  | sed -E 's#.*/hadoop-client-api-([0-9.]+)\.jar#\1#' | head -1)"

case "$MINIO_ENDPOINT" in
  https://*) S3A_SSL=true ;;
  *)         S3A_SSL=false ;;
esac

{
  printf 'spark.jars.packages org.apache.hadoop:hadoop-aws:%s,io.delta:delta-spark_4.1_2.13:4.3.1\n' "${HADOOP_VER:-3.4.2}"
  printf 'spark.sql.extensions io.delta.sql.DeltaSparkSessionExtension\n'
  printf 'spark.sql.catalog.spark_catalog org.apache.spark.sql.delta.catalog.DeltaCatalog\n'
  printf 'spark.hadoop.fs.s3a.endpoint %s\n' "$MINIO_ENDPOINT"
  printf 'spark.hadoop.fs.s3a.access.key %s\n' "$MINIO_KEY"
  printf 'spark.hadoop.fs.s3a.secret.key %s\n' "$MINIO_SECRET"
  printf 'spark.hadoop.fs.s3a.path.style.access true\n'
  printf 'spark.hadoop.fs.s3a.connection.ssl.enabled %s\n' "$S3A_SSL"
  # In client mode the executors open connections back to the driver, so it has
  # to advertise an address they can reach — the container's address on the
  # Supervisor network, not its loopback. Bind wide, advertise precisely.
  DRIVER_IP="$(hostname -i 2>/dev/null | awk '{print $1}')"
  case "$DRIVER_IP" in
    ""|127.*) : ;;  # nothing usable — let Spark work it out for itself
    *) printf 'spark.driver.host %s\nspark.driver.bindAddress 0.0.0.0\n' "$DRIVER_IP" ;;
  esac
} > "$SPARK_CONF_DIR/spark-defaults.conf"
chmod 600 "$SPARK_CONF_DIR/spark-defaults.conf"

echo "[Pipeline Airflow] preparing database and admin user"
# One pass through the entrypoint (it waits for Postgres and migrates the core
# schema) to add FAB's own tables and settle the admin account.
# The credentials go through the environment rather than being interpolated
# into the script below — a password containing a quote would otherwise break
# it, and this file must not care what characters are in it.
export ADMIN_USER ADMIN_PASSWORD
_AIRFLOW_DB_MIGRATE=true /entrypoint bash -c '
  set -e
  airflow fab-db migrate
  # create fails when the account already exists; stderr is suppressed because
  # that case is normal, and the reset below then makes the configured
  # password authoritative.
  if airflow users create \
       --username "$ADMIN_USER" \
       --firstname Airflow --lastname Admin \
       --role Admin --email airflowadmin@example.com \
       --password "$ADMIN_PASSWORD" 2>/dev/null; then
    echo "[Pipeline Airflow] created admin user \"$ADMIN_USER\""
  else
    airflow users reset-password --username "$ADMIN_USER" --password "$ADMIN_PASSWORD"
    echo "[Pipeline Airflow] admin password for \"$ADMIN_USER\" set from options"
  fi
'

echo "[Pipeline Airflow] starting (LocalExecutor, metadata DB @ ${PG_HOST}:${PG_PORT}, UI :8080)"

# Not `airflow standalone`. It overrides the auth manager on the way up —
#
#     if conf.get("core", "auth_manager") != simple_auth_manager_classpath:
#         self.print_output("standalone", "Forcing auth manager to SimpleAuthManager")
#
# — so the admin account created above would exist while the running server
# ignored it, which is exactly the "changeme doesn't work" symptom. standalone
# is only a convenience wrapper around these four components anyway; the
# entrypoint waits for the database for each of them.
COMPONENTS=(api-server scheduler dag-processor triggerer)
pids=()

shutdown() {
  trap - TERM INT
  kill "${pids[@]}" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap shutdown TERM INT

for component in "${COMPONENTS[@]}"; do
  /entrypoint airflow "$component" &
  pids+=("$!")
  echo "[Pipeline Airflow] started $component (pid $!)"
done

# If any one of them dies the add-on is broken, so stop the rest and let Home
# Assistant restart the lot rather than limping on half-running. Polled rather
# than `wait -n`, which needs bash 4.3 and so can't be exercised on every
# machine this is developed from; a few seconds' notice is ample here. The
# sleep runs in the background and is waited on so a stop signal is acted on
# immediately instead of after the current sleep.
while true; do
  for pid in "${pids[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "[Pipeline Airflow] a component exited — shutting the rest down"
      shutdown
      exit 1
    fi
  done
  sleep 5 &
  wait "$!" || true
done
