# Pipeline Postgres

PostgreSQL 16 with **TimescaleDB** for the data-pipeline stack. It stores your
pipeline data **and** hosts Apache Airflow's metadata database, so you only need
one Postgres instance for the whole stack.

Part of a four-add-on data pipeline: **Pipeline Postgres**, **Pipeline MinIO**,
**Pipeline Spark**, **Pipeline Airflow**. Start them in this order:
`postgres → minio → spark → airflow`.

> **Heavy stack.** The full pipeline (Spark + Airflow + Postgres + MinIO) is meant
> for an amd64 host with plenty of RAM (8–16 GB). It is not suitable for a
> Raspberry Pi or a 32-bit host.

## What it does

- Runs PostgreSQL 16, listening on host port **5432**.
- Creates your main pipeline database (default `pipeline`) owned by your user.
- Enables **TimescaleDB** in that database, for hypertables, continuous
  aggregates and compression on time-series data.
- On first start only, provisions a separate **`airflow`** role and database that
  the Pipeline Airflow add-on uses for its metadata.
- Stores all data in the add-on's persistent volume (`/data/pgdata`), so it
  survives restarts and updates.

## Configuration

- **postgres_user** / **postgres_password**: the main pipeline login. **Change the
  password before starting.**
- **postgres_db**: the main pipeline database name (default `pipeline`).
- **airflow_db_password**: password for the auto-created `airflow` role. Set the
  **same value** in the Pipeline Airflow add-on's `airflow_db_password`.

> The user, databases and passwords are applied **only on the first start** (when
> the data directory is empty). To change them afterwards you must either use SQL
> (`ALTER ROLE … PASSWORD …`) or reset the add-on's data.

## TimescaleDB

The extension is enabled in the pipeline database only — Airflow's metadata
database is left as plain Postgres. Turn a table into a hypertable once it has a
time column:

```sql
CREATE TABLE readings (ts timestamptz NOT NULL, sensor text, value double precision);
SELECT create_hypertable('readings', 'ts');
```

Notes:

- Telemetry is disabled (`timescaledb.telemetry_level=off`); nothing is reported
  to Timescale.
- Upgrading an add-on that was created before 1.1.0 keeps your data: the
  extension is added to the existing pipeline database on the next start. Check
  the add-on log for `TimescaleDB extension ready`.
- Other databases you create yourself need their own
  `CREATE EXTENSION timescaledb;`.

## Backups and point-in-time recovery

Off by default. Setting **backup_enabled** turns on WAL archiving and scheduled
backups via pgBackRest, which together give **point-in-time recovery**: you can
restore the database to any moment inside the retention window, including the
second before a bad migration or an accidental `DROP`.

The repository is a plain directory, **`/share/pipeline-postgres-backup`** by
default. Two consequences worth knowing:

- Home Assistant's own backups include `/share`, so the repository travels with
  them.
- Mounting a NAS share at that path makes the archive genuinely **off-host**
  without changing anything else. Until you do, the archive is on the same disk
  as the database: it protects against mistakes and corruption, not against
  losing the machine.

It is not S3. pgBackRest speaks only TLS to an S3 endpoint — its one knob,
`repo-storage-verify-tls`, verifies a certificate rather than disabling TLS —
and the MinIO add-on serves plain HTTP. MinIO also stores its data on this same
disk, so archiving there would have added a dependency without adding safety.

### Read this before enabling

With archiving on, Postgres **keeps every WAL segment until the archive accepts
it**. If the repository is unwritable — a full disk, an unmounted share — WAL
accumulates in `/data/pgdata/pg_wal` until the disk fills and the database
stops. That is the one way this feature can take the database down, so:

- Check the log after enabling. A healthy start says
  `pgBackRest check passed (archiving works)`. A failure says so explicitly and
  warns that WAL will pile up.
- Make sure the backup path is on something with room for the database plus its
  churn.

**If WAL is piling up:** set `backup_enabled: false` and restart. That clears
`archive_mode`, and Postgres discards the retained segments at the next
checkpoint. Fix the repository, then switch it back on.

### Configuration

- **backup_enabled**: off by default; nothing changes until you set it.
- **backup_path**: where the repository lives (default
  `/share/pipeline-postgres-backup`).
- **backup_retention_full**: how many full backups to keep (default 4). Older
  ones, and the WAL only they needed, are expired automatically.
- **backup_full_interval_days** (default 7) / **backup_incr_interval_hours**
  (default 24): how often a full and an incremental run.

### Restoring

Stop every add-on that uses the database first (Airflow, the metastore, Spark
jobs). Then, from the add-on's container:

```bash
# what you can restore to
pgbackrest --stanza=main info

# the whole database, back to the latest backup + all archived WAL
pgbackrest --stanza=main --delta restore

# or to a moment — the point of all this
pgbackrest --stanza=main --delta --type=time \
  --target="2026-08-08 14:30:00+02" restore
```

`--delta` restores only what differs, which is much faster than emptying the
directory first. After a `--type=time` restore the server starts paused at that
point; confirm the data looks right, then:

```sql
SELECT pg_wal_replay_resume();
```

Restoring rewinds **everything in the instance** — the pipeline database,
Airflow's metadata and the metastore catalog together, since they share one
Postgres. Airflow will see task history from the restore point.

## Replication

Set **replication_password** and the add-on creates a `replicator` role and
permits replication connections from the Supervisor network. That is all the
primary needs; the standby lives in the separate **Pipeline Postgres Replica**
add-on.

Changing the password later takes effect on the next start — the role's password
is set every time, so rotating it in the UI actually rotates it.

> A replica is not a backup. It replays a mistake within a second, and on this
> machine it shares this disk. Backups above are what protect the data.

## Connecting

- From other add-ons on the same host, connect to `172.30.32.1:5432` (the
  Supervisor bridge gateway, which reaches this add-on's published port). This is
  the default the Pipeline Airflow add-on uses.
- From your LAN, connect to `<home-assistant-host-ip>:5432`.

> **Security:** this publishes Postgres on the host network. Keep the passwords
> strong and do not expose the host to the internet.
