# Changelog

## 1.5.0

- New **`hoas`** superuser role for external DB-admin tools (pgAdmin, DBeaver,
  a workstation `psql`, ...), separate from `postgres_user` so those tools
  don't need the pipeline app's own credentials. Off unless
  **hoas_admin_password** is set; reconciled every start like the replication
  role, so rotating the password in the UI takes effect on the next restart.

## 1.4.1

- Fixed: on an add-on already running before 1.4.0, the `sensor_test` role,
  `sensor_db` database and `sensor` schema never appeared — the initdb hook
  that provisions them only runs against an empty data directory, which an
  existing install never has. Reconciled on every start instead (like the
  replication role), so it now also applies to add-ons that were already
  installed, and rotating `sensor_test_db_password` in the UI takes effect
  immediately rather than only on a fresh install.

## 1.4.0

- On first start, provisions a **`sensor_test`** role, a **`sensor_db`**
  database (owned by that role, TimescaleDB enabled) and a **`sensor`** schema
  inside it, for an external, non-Home-Assistant client to store timeseries
  data. New **sensor_test_db_password** option, same first-start-only caveat
  as `airflow_db_password`.

## 1.3.1

- Documentation fixes. Documented **`replication_user`** (default `replicator`),
  the one option with no entry — the replica's docs point at it by name while
  this add-on's never mentioned it, and the two must match.
- Said that the add-on writes its backup state to
  `/share/pipeline-status/pipeline-postgres.json` and that the Add-on Watchdog
  shows it, so watching the log is not the only way to know archiving works.
- Corrected the opening line: this is a seven-add-on pipeline, not four.

## 1.3.0

- Writes `/share/pipeline-status/pipeline-postgres.json` after each backup and
  after the start-up archive check, so Add-on Watchdog 1.8.0 can report whether
  backups are actually running. A failed check is reported immediately rather
  than waiting for a backup cycle — that is the state where WAL accumulates.

## 1.2.0

- **Point-in-time recovery**, off by default. Setting `backup_enabled` turns on
  WAL archiving and scheduled pgBackRest backups, so the database can be
  restored to any moment in the retention window — the second before a bad
  migration, not just the last nightly.
- The repository is a directory under `/share` (default
  `/share/pipeline-postgres-backup`), not S3. pgBackRest speaks only TLS to an
  S3 endpoint and the MinIO add-on serves plain HTTP; MinIO also stores its data
  on this same disk, so archiving there would have added a dependency without
  adding safety. `/share` is included in Home Assistant's own backups, and
  mounting a NAS share at that path makes the archive off-host with no other
  change.
- **Read the DOCS section before enabling.** With archiving on, Postgres keeps
  WAL until the archive accepts it; an unwritable repository fills the disk and
  stops the database. The log says plainly whether archiving works, and the
  escape hatch is documented.
- **Replication support**: a `replicator` role and a `pg_hba` line for the
  Supervisor network, which the new Pipeline Postgres Replica add-on needs. The
  role's password is set on every start, so rotating the option works.
- After every backup the add-on log carries `pgbackrest info` — the backup
  inventory and what can be restored to — so checking on backups needs the
  add-on log rather than a shell inside the container.
- The start-up reconcile is now a single idempotent pass instead of a
  fresh-versus-existing branch.

## 1.1.0

- Added TimescaleDB (`timescaledb-2-postgresql-16`). It is preloaded by the
  server and the extension is enabled in the pipeline database — on first start
  for new installs, and on the next start for an existing data directory.
- TimescaleDB telemetry is disabled (`timescaledb.telemetry_level=off`).

## 1.0.1

- Set the base image directly in the Dockerfile and removed `build.yaml`
  (deprecated by Supervisor 2026.04.0, which no longer passes `BUILD_FROM`).

## 1.0.0

- First release. PostgreSQL 16 for the data-pipeline stack, on host port 5432.
- Provisions a separate `airflow` role and database (on first start) for the
  Pipeline Airflow add-on's metadata store.
- Data persisted in `/data/pgdata`. amd64 only.
