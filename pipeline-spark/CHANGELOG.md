# Changelog

## 1.0.0

- First release. Apache Spark 4.2 single-node standalone cluster (master + worker).
- Master RPC on host 7077, REST submission API on 6066, master/worker UIs on
  8082/8083.
- Pre-configured MinIO S3A access; bundled PostgreSQL JDBC driver; hadoop-aws pulled
  on first submit and cached in `/data/spark/.ivy2`. Ships an example job. amd64 only.
