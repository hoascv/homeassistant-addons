#!/usr/bin/env bash
# Runs once, during the very first database initialization (empty PGDATA).
# The init-time server is already started with shared_preload_libraries=
# timescaledb (run.sh passes it through), so the extension can be created here.
# Only the pipeline database gets it — Airflow's metadata DB has no use for it.
set -e

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     -c 'CREATE EXTENSION IF NOT EXISTS timescaledb'

echo "[Pipeline Postgres] enabled the TimescaleDB extension in $POSTGRES_DB"
