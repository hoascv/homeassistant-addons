#!/usr/bin/env bash
# Runs once, during the very first database initialization (empty PGDATA).
# Creates a dedicated role + database for Airflow's metadata store so a single
# Postgres instance serves both the pipeline and the orchestrator.
set -e

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     --set=airflow_pw="$AIRFLOW_DB_PASSWORD" <<-'EOSQL'
    CREATE ROLE airflow WITH LOGIN PASSWORD :'airflow_pw';
    CREATE DATABASE airflow OWNER airflow;
    GRANT ALL PRIVILEGES ON DATABASE airflow TO airflow;
EOSQL

echo "[Pipeline Postgres] provisioned the 'airflow' role and database"
