# Changelog

## 1.3.0

- **An unconfigured tracker now fails the run instead of skipping it.** Skipping
  left the run green, so a pipeline that was loading nothing looked exactly like
  a healthy one — the single state most worth noticing. The failure message
  names the Variables to set.
- **The runs say what they did**: how many rows a bootstrap carried, how many
  changes an incremental pass found, where the watermark moved to, and — when
  there is genuinely nothing new — that too, so a quiet run is quiet on purpose.
- **The tracker DAGs are refreshed on every start.** They were only ever copied
  if absent, which meant a fix to them could never reach an installation that
  already had the old copy. `example_pipeline.py` is still yours to edit and is
  left alone.

## 1.2.1

- **Actually fix the login.** 1.2.0 created the admin account correctly, but the
  add-on then started Airflow with `airflow standalone`, which forces its own
  development auth manager on the way up and discarded it — so the account
  existed while the running server ignored it. The four Airflow components are
  now started directly, which is all `standalone` was doing anyway.
- If one component stops, the add-on stops the rest so Home Assistant restarts
  it, rather than limping along half-running.

## 1.2.0

- **Fix not being able to log in.** `admin_user` and `admin_password` were
  being ignored: Airflow 3 only applies them when the FAB auth manager is
  installed, and it wasn't — so Airflow fell back to its development
  auth manager, which invents a password, prints it to the log once and stores
  it in plaintext. The FAB provider is now installed and selected, so the
  options work as documented.
- **Changing `admin_password` now takes effect.** The old path only ever
  *created* a user and silently did nothing if one already existed, so editing
  the option later changed nothing. The password is applied on every start.
- Sessions survive a restart: the session secret is persisted alongside the
  Fernet key instead of being regenerated each boot.

## 1.1.0

- New **gym_tracker_ingest** and **coop_tracker_ingest** DAGs: pull each
  tracker's change feed, archive the raw responses to MinIO, and merge them
  into Delta tables via Spark. Hourly, incremental, and idempotent — the
  watermark only advances once the merge has succeeded.
- The bundled Spark client is now **4.1.3**, matching the Spark add-on.

## 1.0.3

- Added an **Open Web UI** button on the add-on page, so Airflow can be opened
  from within Home Assistant.

## 1.0.2

- Fixed the image build failing with "You are running pip as root": provider
  packages are now installed as the `airflow` user (as the base image
  requires). The add-on still runs as root (to write `/data` and `/share`) and
  sets `HOME=/home/airflow` so those packages resolve.

## 1.0.1

- Set the base image directly in the Dockerfile and removed `build.yaml`
  (deprecated by Supervisor 2026.04.0, which no longer passes `BUILD_FROM`).

## 1.0.0

- First release. Apache Airflow 3.3 (LocalExecutor), web UI on host port 8085.
- Uses the Pipeline Postgres add-on for its metadata database.
- Pre-wired connections `minio_default` (S3), `spark_default`, and `pipeline_pg`,
  plus an `example_pipeline` DAG that runs MinIO → Spark → Postgres end to end.
- Bundled Spark client for `SparkSubmitOperator`. DAGs live in
  `/share/pipeline-airflow/dags`. amd64 only.
