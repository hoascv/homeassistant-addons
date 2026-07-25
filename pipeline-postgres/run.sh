#!/usr/bin/env bash
set -e

OPTIONS=/data/options.json

export POSTGRES_USER="$(jq -r '.postgres_user' "$OPTIONS")"
export POSTGRES_PASSWORD="$(jq -r '.postgres_password' "$OPTIONS")"
export POSTGRES_DB="$(jq -r '.postgres_db' "$OPTIONS")"
# Consumed by the initdb hook (10-init-airflow-db.sh) on first init only.
export AIRFLOW_DB_PASSWORD="$(jq -r '.airflow_db_password' "$OPTIONS")"

# Persist data across restarts/updates in the add-on's own data volume.
export PGDATA=/data/pgdata
mkdir -p "$PGDATA"

echo "[Pipeline Postgres] starting PostgreSQL 16 (PGDATA=$PGDATA, db=$POSTGRES_DB)"

# Hand off to the stock Postgres entrypoint, which (running as root) fixes
# ownership on PGDATA, runs any first-boot init scripts, then drops to the
# postgres user.
exec docker-entrypoint.sh postgres
