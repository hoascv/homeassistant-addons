# Changelog

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
