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
SPARK_CONNECT_PORT="$(opt '.spark_connect_port // 15002')"
MANAGE_BUNDLED_DAGS="$(opt '.manage_bundled_dags // true')"
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

# The tracker DAGs normally ship with the add-on and are replaced on every start:
# with cp -n a fix to them could never reach an installation that already had the
# old copy. Set manage_bundled_dags to false to take ownership instead — useful
# when editing them from JupyterLab, at the cost of no longer receiving fixes.
if [ "$MANAGE_BUNDLED_DAGS" = "false" ]; then
  # Still seeded when absent, so a fresh install in dev mode isn't empty.
  cp -n /opt/airflow/project-dags/trackers_ingest.py /share/pipeline-airflow/dags/ 2>/dev/null || true
  echo "[Pipeline Airflow] dev mode: leaving /share/pipeline-airflow/dags/trackers_ingest.py alone"
  echo "[Pipeline Airflow]   (you own it now; add-on updates will not change it)"
else
  cp -f /opt/airflow/project-dags/trackers_ingest.py /share/pipeline-airflow/dags/ 2>/dev/null || true
fi

# Publish the importable modules so anything else on the machine — a JupyterLab
# add-on, say — can run the same code the scheduler runs. Refreshed on every
# start regardless of dev mode, so a notebook and the DAG can never disagree
# about what `merge_batch` does.
mkdir -p /share/pipeline-airflow/lib
cp -f /opt/pipeline/jobs/*.py /share/pipeline-airflow/lib/ 2>/dev/null || true
# Shell tools too — iobench.sh is meant to be copied off this machine and run on
# a server that may have no Python at all, so it has to be somewhere reachable.
cp -f /opt/pipeline/jobs/*.sh /share/pipeline-airflow/lib/ 2>/dev/null || true
chmod +x /share/pipeline-airflow/lib/*.sh 2>/dev/null || true

# A starting point for using them, seeded once and then yours.
mkdir -p /share/pipeline-airflow/notebooks
cp -n /opt/pipeline/notebooks/*.ipynb /share/pipeline-airflow/notebooks/ 2>/dev/null || true

# --- Database and admin user ------------------------------------------------
# Done here rather than through the entrypoint's _AIRFLOW_WWW_USER_CREATE,
# because FAB keeps its tables in a separate migration that has to run first,
# and because that path only ever creates a user: it silently does nothing if
# one exists, so changing admin_password later would never take effect.

# --- Pre-wired connections (via env, always available) ---------------------
# No spark_default connection: there is no spark-submit to configure. The DAGs
# read the Connect endpoint as a Variable, which this supplies from the add-on
# options through the environment-variables secrets backend — so it is
# configured in one place, the add-on's own settings.
export AIRFLOW_VAR_SPARK_CONNECT_URL="sc://${SPARK_MASTER}:${SPARK_CONNECT_PORT}"
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

# --- Tracker credentials (Airflow Variables via env) -----------------------
# Set here rather than only in the web UI, so the whole stack is configured in
# one place. Airflow checks the environment secrets backend before the
# metastore, so these win over a UI Variable of the same name — which is the
# point: a Variable typed into the UI with a stray character in its *key* is
# invisible in the list and impossible to look up, and that failure has cost
# real time. Anything set here cannot suffer that.
#
# Exported only when non-empty. An empty AIRFLOW_VAR_* would resolve ahead of
# the metastore and shadow a UI Variable that was perfectly fine.
trim() { printf '%s' "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'; }

# Iterated by full source name rather than by a prefix with "_tracker_" glued
# on. The names have to match the DAG's SOURCES keys exactly — it looks up
# Variable "<source>_api_token" — and a source that is not a tracker
# (detection_hub) has no place to put that infix.
for source in gym_tracker coop_tracker detection_hub; do
  url="$(trim "$(opt ".${source}_base_url // \"\"")")"
  # Trimmed before export so the length reported below is the length actually
  # presented to the source: the DAG strips it anyway, and a token that looks
  # one character longer than the source's would send us hunting a difference
  # that isn't there.
  tok="$(trim "$(opt ".${source}_api_token // \"\"")")"
  upper="$(echo "$source" | tr '[:lower:]' '[:upper:]')"
  [ -n "$url" ] && export "AIRFLOW_VAR_${upper}_BASE_URL=$url"
  if [ -n "$tok" ]; then
    export "AIRFLOW_VAR_${upper}_API_TOKEN=$tok"
    # The length, so it can be compared with the source's own startup line
    # without either value being written down. A 401 or 403 means the two
    # didn't match, and two numbers settle that in one glance.
    echo "[Pipeline Airflow] ${source} token supplied from add-on options (${#tok} characters)"
  fi
done

# No Spark configuration is written here any more. Jobs run over Spark Connect,
# so the driver lives in the Pipeline Spark add-on and reads *its* config: the
# S3A credentials and Delta jars are its business, and a Connect client cannot
# override server-side settings anyway. The MinIO secret consequently never
# reaches this container's disk or any command line.

# An example_pipeline.py left over from an older install still calls
# SparkSubmitOperator, and there is no spark-submit here to answer it. It is
# yours to edit, so it is never overwritten — but say so rather than let it fail
# obscurely.
EXAMPLE=/share/pipeline-airflow/dags/example_pipeline.py
if [ -f "$EXAMPLE" ] && grep -q SparkSubmitOperator "$EXAMPLE" 2>/dev/null; then
  echo "[Pipeline Airflow] NOTE: $EXAMPLE is the old spark-submit version and will"
  echo "[Pipeline Airflow]       fail. Delete it and restart to get the Spark Connect one."
fi

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

# A stop asked for by Supervisor is a success, so exit 0 rather than falling
# through to the loop below, which would see the components gone and report a
# crash. Every normal stop was being logged as "exited with non-zero exit
# code 1", which makes the one that matters impossible to spot.
on_signal() {
  echo "[Pipeline Airflow] stop requested — shutting down"
  shutdown
  exit 0
}
trap on_signal TERM INT

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
