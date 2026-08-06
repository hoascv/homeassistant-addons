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

# TimescaleDB has to be preloaded by the server itself; the extension is then
# created per database (20-init-timescaledb.sh on first init, or below on an
# already-initialised data directory). Telemetry is off — this is a home host.
PG_OPTS=(-c shared_preload_libraries=timescaledb -c timescaledb.telemetry_level=off)

if [ -s "$PGDATA/PG_VERSION" ]; then
    # Existing data directory: the initdb hooks never run again, so add the
    # extension once the server is accepting connections. Idempotent, so it is
    # a no-op on every start after the first upgraded one.
    (
        for _ in $(seq 1 90); do
            if pg_isready -q -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"; then
                if PGPASSWORD="$POSTGRES_PASSWORD" psql -q -v ON_ERROR_STOP=1 \
                        -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
                        -c 'CREATE EXTENSION IF NOT EXISTS timescaledb'; then
                    echo "[Pipeline Postgres] TimescaleDB extension ready in $POSTGRES_DB"
                else
                    echo "[Pipeline Postgres] WARNING: could not enable TimescaleDB in $POSTGRES_DB"
                fi
                exit 0
            fi
            sleep 2
        done
        echo "[Pipeline Postgres] WARNING: Postgres never became ready; TimescaleDB not enabled"
    ) &
fi

echo "[Pipeline Postgres] starting PostgreSQL 16 + TimescaleDB (PGDATA=$PGDATA, db=$POSTGRES_DB)"

# Hand off to the stock Postgres entrypoint, which (running as root) fixes
# ownership on PGDATA, runs any first-boot init scripts, then drops to the
# postgres user.
exec docker-entrypoint.sh postgres "${PG_OPTS[@]}"
