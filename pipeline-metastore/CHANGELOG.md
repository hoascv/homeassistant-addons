# Changelog

## 1.1.0

- New `log_level` option. Hive's Thrift server drops a connection whose message
  it cannot deserialize **silently** at INFO, so a client failing its handshake
  leaves no trace at all — which made "the metastore logged nothing" look like
  evidence that nothing arrived, when it is evidence of neither. `DEBUG` shows
  the connection and what it choked on.

## 1.0.0

- First release. Apache Hive standalone metastore 4.1.0 on Thrift port 9083,
  giving the lakehouse's Delta tables names instead of paths.
- Catalog stored in its own `metastore` database on the Pipeline Postgres
  add-on; the role and database are created on first start, and the schema is
  installed with `schematool -initSchema`.
- Resolves `s3a://` locations against MinIO, so registered tables can point at
  the lakehouse bucket.
- Optional: nothing uses it until the Pipeline Spark add-on's `metastore_uris`
  is set. amd64 only.
