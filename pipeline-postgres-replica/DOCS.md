# Pipeline Postgres Replica

A streaming **read-only** standby of the Pipeline Postgres add-on, listening on
host port **5433**. It replays the primary's WAL continuously, so it is seconds
behind at most.

Requires **Pipeline Postgres 1.2.0 or later**, which is the release that adds a
replication role and the `pg_hba` line permitting it.

> **This is not a backup, and it is not off-host.** A replica replays a
> `DROP TABLE` faithfully within a second, and on this machine it shares the
> primary's disk. Use the primary's `backup_enabled` (point-in-time recovery)
> for protecting the data; use this for reading it.

## What it's for

- **Read-only queries** that would otherwise compete with the pipeline —
  analytics, dashboards, a notebook exploring the tables.
- **Fast promotion** when the primary's data directory is corrupted or an
  upgrade goes wrong: a standby that is already warm becomes a working database
  in seconds rather than the minutes a restore takes.

Why it is a separate add-on rather than a `role` option on the primary: Home
Assistant installs one instance per slug, so a second Postgres on the same
machine has to be a second add-on. It also needs its own host port.

## Setting it up

1. On **Pipeline Postgres**, set `replication_password` (and start it, so the
   role and `pg_hba` line are applied).
2. Here, set the same `replication_password`. The defaults for the rest are
   already right for the usual layout.
3. Start it. The first start clones the primary with `pg_basebackup`, which
   takes as long as the database is large; progress is in the log.

If the clone fails, the data directory is wiped rather than left half-copied —
otherwise the next start would mistake it for an initialised standby and never
retry.

## Configuration

- **primary_host** / **primary_port**: the primary. Defaults to the Supervisor
  bridge gateway, `172.30.32.1:5432`.
- **replication_user** / **replication_password**: must match the primary's
  options of the same name.
- **slot_name**: the replication slot created on the primary. The slot is what
  stops the primary discarding WAL this replica has not consumed yet — which
  also means that **if this add-on is stopped for a long time, the primary
  retains WAL on its behalf and its disk grows**. Drop the slot on the primary
  if you retire the replica:

  ```sql
  SELECT pg_drop_replication_slot('replica1');
  ```

## Checking it

The log reports recovery state and lag shortly after start. To ask directly:

```sql
-- on the replica (port 5433): true
SELECT pg_is_in_recovery();

-- on the primary: one row per connected standby, with its lag
SELECT client_addr, state, replay_lag FROM pg_stat_replication;
```

Writes fail here, as they should:
`ERROR: cannot execute INSERT in a read-only transaction`.

## Promoting it

Promotion is deliberately manual — this add-on will never decide on its own
that the primary is dead, because on a single host the usual reason both are
unreachable is that the host is busy, not that the primary has failed.

```bash
# from the replica's container
pg_ctl promote -D /data/pgdata
```

It then becomes a normal read-write database on port 5433. Afterwards it is no
longer a standby: point your clients at 5433, and rebuild a replica later by
stopping this add-on, clearing its data, and starting it again against whichever
instance is now the primary.

## Notes

- The image matches the primary's exactly, TimescaleDB included. Physical
  replication ships WAL records, so a standby with a different extension build
  would refuse to start on them.
- There are no init hooks here. A standby's data directory is cloned, never
  initialised, so anything in `docker-entrypoint-initdb.d` would never run.
- The Add-on Watchdog probes port 5432 in the container and reports this add-on
  like any other. It does not report replication lag: it deliberately holds no
  database credentials.
