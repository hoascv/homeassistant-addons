# Changelog

## 1.2.1

- Documented that jobs run in **client mode**, not cluster mode: Spark
  standalone rejects a PySpark application submitted with `--deploy-mode
  cluster`. The driver therefore runs in the submitting container, which is why
  the tracker merge job now ships with the Airflow add-on rather than this one.
- Corrected the documented Spark version (4.1, not 4.2).

## 1.2.0

- The Delta tables now carry an **actor** column — who each change came from:
  `user`, `automation` or `migration`.

## 1.1.0

- **Delta Lake support.** `delta-spark` is pulled at first submit alongside
  hadoop-aws, and the Delta SQL extension and catalog are configured by
  default, so a job can read and write Delta tables with no extra setup.
- **Spark pinned to 4.1.3** (from 4.2.0). Delta publishes no build for Spark
  4.2 — its newest release declares Spark 4.1 as its provided dependency, and
  Delta binds to internals a minor version can move.
- New **trackers_merge** job: merges a Gym/Coop Tracker change batch into Delta
  tables, keyed on the row id and guarded by the change sequence, so re-running
  a batch converges instead of double-counting.

## 1.0.2

- Fixed a start-up crash loop: the base image has no writable
  `$SPARK_HOME/conf`, so writing `spark-defaults.conf` there failed. The
  config now lives in `/data/spark/conf` (via `SPARK_CONF_DIR`), alongside the
  worker scratch dir and Ivy cache.

## 1.0.1

- Set the base image directly in the Dockerfile and removed `build.yaml`
  (deprecated by Supervisor 2026.04.0, which no longer passes `BUILD_FROM`).

## 1.0.0

- First release. Apache Spark 4.2 single-node standalone cluster (master + worker).
- Master RPC on host 7077, REST submission API on 6066, master/worker UIs on
  8082/8083.
- Pre-configured MinIO S3A access; bundled PostgreSQL JDBC driver; hadoop-aws pulled
  on first submit and cached in `/data/spark/.ivy2`. Ships an example job. amd64 only.
