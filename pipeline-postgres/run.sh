#!/usr/bin/env bash
set -e

OPTIONS=/data/options.json

export POSTGRES_USER="$(jq -r '.postgres_user' "$OPTIONS")"
export POSTGRES_PASSWORD="$(jq -r '.postgres_password' "$OPTIONS")"
export POSTGRES_DB="$(jq -r '.postgres_db' "$OPTIONS")"
# Consumed by the initdb hook (10-init-airflow-db.sh) on first init only.
export AIRFLOW_DB_PASSWORD="$(jq -r '.airflow_db_password' "$OPTIONS")"
# Consumed by the initdb hook (30-init-sensor-db.sh) on first init only.
export SENSOR_DB_PASSWORD="$(jq -r '.sensor_test_db_password' "$OPTIONS")"

REPL_USER="$(jq -r '.replication_user // "replicator"' "$OPTIONS")"
REPL_PASSWORD="$(jq -r '.replication_password // ""' "$OPTIONS")"
BACKUP_ENABLED="$(jq -r '.backup_enabled // false' "$OPTIONS")"
BACKUP_PATH="$(jq -r '.backup_path // "/share/pipeline-postgres-backup"' "$OPTIONS")"
RETENTION_FULL="$(jq -r '.backup_retention_full // 4' "$OPTIONS")"
FULL_DAYS="$(jq -r '.backup_full_interval_days // 7' "$OPTIONS")"
INCR_HOURS="$(jq -r '.backup_incr_interval_hours // 24' "$OPTIONS")"

# Persist data across restarts/updates in the add-on's own data volume.
export PGDATA=/data/pgdata
mkdir -p "$PGDATA"

STANZA=main
PGBR_CONF=/etc/pgbackrest/pgbackrest.conf

# TimescaleDB has to be preloaded by the server itself; the extension is then
# created per database (20-init-timescaledb.sh on first init, or by the
# reconcile below). Telemetry is off — this is a home host.
PG_OPTS=(-c shared_preload_libraries=timescaledb -c timescaledb.telemetry_level=off)

psql_db() {
    local db="$1"; shift
    PGPASSWORD="$POSTGRES_PASSWORD" psql -q -v ON_ERROR_STOP=1 \
        -h 127.0.0.1 -U "$POSTGRES_USER" -d "$db" "$@"
}

psql_super() {
    psql_db "$POSTGRES_DB" "$@"
}

# A small status file the Add-on Watchdog reads. The watchdog deliberately holds
# no database credentials, so it cannot ask Postgres anything itself; this add-on
# reports its own state instead, the same way the trackers publish /api/stats.
# jq builds the JSON so a detail string containing a quote cannot break the file.
STATUS_DIR=/share/pipeline-status
STATUS_FILE="$STATUS_DIR/pipeline-postgres.json"

write_status() {
    local ok="$1" detail="$2"
    shift 2
    mkdir -p "$STATUS_DIR" 2>/dev/null || return 0
    jq -n --arg slug pipeline-postgres --arg kind backup \
        --argjson ok "$ok" --arg detail "$detail" \
        --argjson updated_at "$(date +%s)" --argjson metrics "${1:-null}" \
        '{slug: $slug, kind: $kind, ok: $ok, detail: $detail,
          updated_at: $updated_at, metrics: $metrics}' \
        > "$STATUS_FILE" 2>/dev/null || true
}

# --- backups ------------------------------------------------------------------
#
# A posix repository rather than S3, for two reasons. pgBackRest speaks only TLS
# to an S3 endpoint — its sole knob is repo-storage-verify-tls, which verifies a
# certificate rather than turning TLS off — and the MinIO add-on serves plain
# HTTP. And MinIO stores its own data on this host's disk, so archiving there
# would not have been off-host protection in the first place; it would only have
# added a service whose being stopped fills pg_wal.
#
# /share is mapped in, which means Home Assistant's own backups include the
# repository, and mounting a NAS share at that path makes the archive genuinely
# off-host without changing anything here.
if [ "$BACKUP_ENABLED" = "true" ]; then
    mkdir -p "$BACKUP_PATH" /data/pgbackrest-spool /data/pgbackrest-log /etc/pgbackrest
    chown -R postgres:postgres "$BACKUP_PATH" /data/pgbackrest-spool /data/pgbackrest-log

    # archive_command runs as the postgres user (it is a server child), so the
    # config has to be readable by it — root-owned 600 would break archiving in
    # a way that only shows up as WAL quietly piling up.
    cat > "$PGBR_CONF" <<EOF
[global]
repo1-path=${BACKUP_PATH}
repo1-retention-full=${RETENTION_FULL}
spool-path=/data/pgbackrest-spool
log-path=/data/pgbackrest-log
log-level-console=info
log-level-file=info
start-fast=y
# Keeps archiving off the commit path: a slow or briefly unavailable repository
# should not stall writes.
archive-async=y

[${STANZA}]
pg1-path=${PGDATA}
pg1-port=5432
pg1-user=${POSTGRES_USER}
pg1-database=${POSTGRES_DB}
EOF
    chown postgres:postgres "$PGBR_CONF"
    chmod 600 "$PGBR_CONF"

    PG_OPTS+=(
        -c archive_mode=on
        -c "archive_command=pgbackrest --stanza=${STANZA} archive-push %p"
        # A quiet database would otherwise leave the last partial segment
        # unarchived indefinitely, which is a silent hole in the recovery window.
        -c archive_timeout=300
    )
    echo "[Pipeline Postgres] backups on: pgBackRest repo at ${BACKUP_PATH}"
else
    echo "[Pipeline Postgres] backups off (backup_enabled=false); no WAL archiving"
fi

# --- everything that needs a running server -----------------------------------
#
# One reconcile pass rather than a fresh-vs-existing branch: every step below is
# idempotent, so it is correct immediately after initdb and on every later start.
(
    for _ in $(seq 1 90); do
        pg_isready -q -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" && break
        sleep 2
    done
    if ! pg_isready -q -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"; then
        echo "[Pipeline Postgres] WARNING: server never became ready; skipped setup"
        exit 0
    fi

    # TimescaleDB in the pipeline database. The initdb hook covers a fresh
    # install; this covers a data directory created before 1.1.0.
    psql_super -c 'CREATE EXTENSION IF NOT EXISTS timescaledb' \
        && echo "[Pipeline Postgres] TimescaleDB extension ready in $POSTGRES_DB" \
        || echo "[Pipeline Postgres] WARNING: could not enable TimescaleDB in $POSTGRES_DB"

    # A replication role, so a standby can stream. Created if absent, and its
    # password set every start so changing the option actually takes effect —
    # otherwise rotating it in the UI would silently do nothing. %I/%L quote the
    # identifier and the literal.
    if [ -n "$REPL_PASSWORD" ]; then
        psql_super -v ruser="$REPL_USER" -v rpw="$REPL_PASSWORD" <<'SQL' \
            && echo "[Pipeline Postgres] replication role ready" \
            || echo "[Pipeline Postgres] WARNING: could not set up the replication role"
SELECT format('CREATE ROLE %I WITH LOGIN REPLICATION PASSWORD %L', :'ruser', :'rpw')
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'ruser')\gexec
SELECT format('ALTER ROLE %I WITH LOGIN REPLICATION PASSWORD %L', :'ruser', :'rpw')\gexec
SQL

        # The stock entrypoint's generated pg_hba.conf has no `replication`
        # line, so a standby would be refused before it ever authenticated.
        # Scoped to the Supervisor bridge network rather than all addresses.
        HBA_LINE="host replication ${REPL_USER} 172.30.32.0/23 scram-sha-256"
        if ! grep -qF "$HBA_LINE" "$PGDATA/pg_hba.conf" 2>/dev/null; then
            echo "$HBA_LINE" >> "$PGDATA/pg_hba.conf"
            psql_super -c 'SELECT pg_reload_conf()' >/dev/null \
                && echo "[Pipeline Postgres] pg_hba: allowed replication from 172.30.32.0/23"
        fi
    fi

    # The sensor_test role/database/schema for an external, non-Home-Assistant
    # timeseries client. The initdb hook (30-init-sensor-db.sh) covers a fresh
    # install; this covers an add-on that was already running before this
    # feature shipped — same reasoning as the replication role above, and its
    # password is set every start so rotating sensor_test_db_password in the
    # UI actually takes effect.
    psql_super -v suser="sensor_test" -v spw="$SENSOR_DB_PASSWORD" <<'SQL' \
        && echo "[Pipeline Postgres] sensor_test role ready" \
        || echo "[Pipeline Postgres] WARNING: could not set up the sensor_test role"
SELECT format('CREATE ROLE %I WITH LOGIN PASSWORD %L', :'suser', :'spw')
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'suser')\gexec
SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'suser', :'spw')\gexec
SQL

    if [ "$(psql_super -tAc "SELECT 1 FROM pg_database WHERE datname = 'sensor_db'")" != "1" ]; then
        psql_super -c 'CREATE DATABASE sensor_db OWNER sensor_test' \
            && echo "[Pipeline Postgres] sensor_db database created" \
            || echo "[Pipeline Postgres] WARNING: could not create the sensor_db database"
    fi

    psql_db sensor_db -c 'CREATE EXTENSION IF NOT EXISTS timescaledb' \
        -c 'CREATE SCHEMA IF NOT EXISTS sensor AUTHORIZATION sensor_test' \
        -c "ALTER ROLE sensor_test SET search_path TO sensor, public" \
        && echo "[Pipeline Postgres] sensor schema ready in sensor_db" \
        || echo "[Pipeline Postgres] WARNING: could not set up the sensor schema"

    [ "$BACKUP_ENABLED" = "true" ] || exit 0

    # stanza-create needs the server up and archive_command already set, which
    # is why it lives here rather than beside the config generation. It is
    # idempotent in practice — an existing stanza makes it a no-op error, so
    # the outcome that matters is `check`.
    gosu postgres pgbackrest --stanza="$STANZA" stanza-create 2>/dev/null \
        && echo "[Pipeline Postgres] pgBackRest stanza created" \
        || echo "[Pipeline Postgres] pgBackRest stanza already present"

    if gosu postgres pgbackrest --stanza="$STANZA" check; then
        echo "[Pipeline Postgres] pgBackRest check passed (archiving works)"
        write_status true "archiving works, no backup yet this start" \
            '{"archiving_ok": true}'
    else
        echo "[Pipeline Postgres] WARNING: pgBackRest check failed — WAL will pile" \
             "up in pg_wal until this is fixed. See DOCS.md."
        # Reported as not-ok immediately: this is the state where WAL grows
        # unbounded, and it should not wait for a backup cycle to surface.
        write_status false "pgBackRest check failed; WAL is accumulating" \
            '{"archiving_ok": false}'
    fi

    # Backup loop. A full backup is what retention expires against, so one has
    # to exist before an incremental means anything; pgBackRest promotes an
    # incremental to a full automatically when no full is present, but taking
    # the decision here keeps the log honest about which ran.
    last_full=0
    while true; do
        now=$(date +%s)
        if [ $(( now - last_full )) -ge $(( FULL_DAYS * 86400 )) ]; then
            type=full
        else
            type=incr
        fi
        if gosu postgres pgbackrest --stanza="$STANZA" --type="$type" backup; then
            echo "[Pipeline Postgres] $type backup complete"
            [ "$type" = "full" ] && last_full=$now
            write_status true "$type backup complete" \
                "$(jq -n --arg t "$type" --argjson at "$(date +%s)" \
                    '{last_backup_type: $t, last_backup_at: $at, archiving_ok: true}')"
        else
            echo "[Pipeline Postgres] WARNING: $type backup failed"
            write_status false "$type backup failed" \
                "$(jq -n --arg t "$type" '{last_backup_type: $t, backup_failed: true}')"
        fi
        # The inventory, in the add-on log. Without this the only way to see
        # what can be restored is a shell inside the container, which on Home
        # Assistant OS means installing an SSH add-on and turning protection
        # off — too much ceremony for "did last night's backup work".
        gosu postgres pgbackrest --stanza="$STANZA" info 2>&1 \
            | sed 's/^/[Pipeline Postgres] info: /' || true
        sleep $(( INCR_HOURS * 3600 ))
    done
) &

echo "[Pipeline Postgres] starting PostgreSQL 16 + TimescaleDB (PGDATA=$PGDATA, db=$POSTGRES_DB)"

# Hand off to the stock Postgres entrypoint, which (running as root) fixes
# ownership on PGDATA, runs any first-boot init scripts, then drops to the
# postgres user.
exec docker-entrypoint.sh postgres "${PG_OPTS[@]}"
