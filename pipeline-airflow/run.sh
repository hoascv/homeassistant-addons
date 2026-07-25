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

# Seed the example DAG into the shared, user-editable folder (once).
mkdir -p /share/pipeline-airflow/dags
cp -n /opt/airflow/project-dags/*.py /share/pipeline-airflow/dags/ 2>/dev/null || true

# --- Admin user (handled by the upstream image entrypoint) -----------------
export _AIRFLOW_DB_MIGRATE=true
export _AIRFLOW_WWW_USER_CREATE=true
export _AIRFLOW_WWW_USER_USERNAME="$ADMIN_USER"
export _AIRFLOW_WWW_USER_PASSWORD="$ADMIN_PASSWORD"

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

echo "[Pipeline Airflow] starting (LocalExecutor, metadata DB @ ${PG_HOST}:${PG_PORT}, UI :8080)"
# The upstream entrypoint waits for the DB, migrates, and creates the admin user.
exec /entrypoint airflow standalone
