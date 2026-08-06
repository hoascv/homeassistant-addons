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

## Connecting

- From other add-ons on the same host, connect to `172.30.32.1:5432` (the
  Supervisor bridge gateway, which reaches this add-on's published port). This is
  the default the Pipeline Airflow add-on uses.
- From your LAN, connect to `<home-assistant-host-ip>:5432`.

> **Security:** this publishes Postgres on the host network. Keep the passwords
> strong and do not expose the host to the internet.
