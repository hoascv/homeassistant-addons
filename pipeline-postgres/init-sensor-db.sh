#!/usr/bin/env bash
# Runs once, during the very first database initialization (empty PGDATA).
# Creates a dedicated role + database + schema for an external (non-Home-
# Assistant) timeseries consumer, the same pattern as the airflow role above.
# TimescaleDB is enabled in this database too, since it exists for
# timeseries data.
set -e

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     --set=sensor_pw="$SENSOR_DB_PASSWORD" <<-'EOSQL'
    CREATE ROLE sensor_test WITH LOGIN PASSWORD :'sensor_pw';
    CREATE DATABASE sensor_db OWNER sensor_test;
    GRANT ALL PRIVILEGES ON DATABASE sensor_db TO sensor_test;
EOSQL

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname sensor_db <<-'EOSQL'
    CREATE EXTENSION IF NOT EXISTS timescaledb;
    CREATE SCHEMA IF NOT EXISTS sensor AUTHORIZATION sensor_test;
    ALTER ROLE sensor_test SET search_path TO sensor, public;
EOSQL

echo "[Pipeline Postgres] provisioned the 'sensor_test' role, 'sensor_db' database, and 'sensor' schema"
