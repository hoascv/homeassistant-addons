# Changelog

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
