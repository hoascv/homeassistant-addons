#!/usr/bin/env bash
set -e

OPTIONS=/data/options.json

PRIMARY_HOST="$(jq -r '.primary_host // "172.30.32.1"' "$OPTIONS")"
PRIMARY_PORT="$(jq -r '.primary_port // 5432' "$OPTIONS")"
REPL_USER="$(jq -r '.replication_user // "replicator"' "$OPTIONS")"
REPL_PASSWORD="$(jq -r '.replication_password' "$OPTIONS")"
SLOT_NAME="$(jq -r '.slot_name // "replica1"' "$OPTIONS")"

export PGDATA=/data/pgdata
mkdir -p "$PGDATA"
chown postgres:postgres /data "$PGDATA"

# --- clone the primary, once --------------------------------------------------
#
# A standby's data directory is a copy of the primary's, not something initdb
# produces, so there is no first-boot SQL to run and no hooks to honour. The
# clone happens exactly once: on every later start this directory is already a
# standby and pg_basebackup would refuse anyway.
if [ ! -s "$PGDATA/PG_VERSION" ]; then
    echo "[Pipeline Postgres Replica] cloning ${PRIMARY_HOST}:${PRIMARY_PORT} (first start)"
    # -R writes standby.signal and primary_conninfo, so the result starts as a
    # standby with no further configuration.
    # -C -S creates the replication slot on the primary, which is what stops the
    # primary discarding WAL this replica has not consumed yet.
    # -X stream fetches WAL during the copy, so a long clone cannot end with
    # segments already recycled.
    if ! PGPASSWORD="$REPL_PASSWORD" gosu postgres pg_basebackup \
            -h "$PRIMARY_HOST" -p "$PRIMARY_PORT" -U "$REPL_USER" \
            -D "$PGDATA" -R -C -S "$SLOT_NAME" -X stream --progress; then
        echo "[Pipeline Postgres Replica] ERROR: clone failed. Check that the primary" \
             "is running, that replication_password matches its option, and that it is" \
             "1.2.0 or later (earlier versions have no replication role and no pg_hba" \
             "line for it)."
        # Leave nothing half-copied behind: a partial directory would look
        # initialised on the next start and never be retried.
        rm -rf "${PGDATA:?}/"* 2>/dev/null || true
        exit 1
    fi
    echo "[Pipeline Postgres Replica] clone complete"
else
    echo "[Pipeline Postgres Replica] existing standby data directory, skipping clone"
fi

# Report what the standby is doing, on a loop. The useful question is not
# whether the port answers — the Add-on Watchdog's probe covers that — but
# whether it is still in recovery and how far behind it is. A standby that has
# silently stopped replaying still accepts connections and still serves stale
# data, which is the failure worth catching.
#
# The status file is what the watchdog reads: it holds no database credentials
# by design, so each add-on reports its own state. This one already has a login,
# so nothing new is granted here.
STATUS_DIR=/share/pipeline-status
STATUS_FILE="$STATUS_DIR/pipeline-postgres-replica.json"

(
    for _ in $(seq 1 60); do
        pg_isready -q -h 127.0.0.1 -p 5432 && break
        sleep 2
    done
    while true; do
        # Over TCP as the replication role, not the local socket as the OS user
        # `postgres`: this image's superuser is named after the primary's
        # postgres_user option, so a role called `postgres` need not exist. The
        # `postgres` database always does — initdb creates it regardless.
        row=$(PGPASSWORD="$REPL_PASSWORD" psql -tA -F'|' -h 127.0.0.1 -U "$REPL_USER" \
            -d postgres -c "SELECT pg_is_in_recovery(),
                COALESCE(EXTRACT(EPOCH FROM now() - pg_last_xact_replay_timestamp())::int, 0)" \
            2>/dev/null || echo "")
        in_recovery="${row%%|*}"
        lag="${row##*|}"
        mkdir -p "$STATUS_DIR" 2>/dev/null || true

        if [ "$in_recovery" = "t" ]; then
            ok=true; detail="streaming, ${lag}s behind"
        elif [ "$in_recovery" = "f" ]; then
            # Promoted by hand, or promoted by accident. Either way it is no
            # longer a replica of anything and should not read as healthy.
            ok=false; detail="promoted — no longer a standby"
        else
            ok=false; detail="cannot query the standby"; lag=null
        fi
        jq -n --arg slug pipeline-postgres-replica --arg kind replica \
            --argjson ok "$ok" --arg detail "$detail" \
            --argjson updated_at "$(date +%s)" \
            --argjson metrics "$(jq -n --argjson lag "${lag:-null}" \
                --arg rec "${in_recovery:-unknown}" \
                '{lag_seconds: $lag, in_recovery: $rec}')" \
            '{slug: $slug, kind: $kind, ok: $ok, detail: $detail,
              updated_at: $updated_at, metrics: $metrics}' \
            > "$STATUS_FILE" 2>/dev/null || true

        echo "[Pipeline Postgres Replica] ${detail}"
        sleep 60
    done
) &

echo "[Pipeline Postgres Replica] starting read-only standby (host port 5433)"

# hot_standby is on by default in 16, named here because it is the entire point
# of this add-on: without it the standby replays but refuses connections.
exec docker-entrypoint.sh postgres \
    -c shared_preload_libraries=timescaledb \
    -c timescaledb.telemetry_level=off \
    -c hot_standby=on
