# Changelog

## 1.1.1

- **Documentation fix with real consequences: the metastore URI was wrong.**
  These docs told you to set `thrift://172.30.32.1:9083` on the Spark add-on.
  On at least one host something else answers that gateway port — it accepts the
  connection and holds it open, so a socket probe reads healthy while every
  Thrift request vanishes, and Spark reports `Socket is closed by peer` while
  this add-on logs nothing because nothing arrived. Use the add-on hostname,
  `thrift://<prefix>-pipeline-metastore:9083`. The Spark add-on's docs have said
  so since 1.6.0; this one had not caught up.
- Replaced the paragraph claiming the first query after enabling needs internet
  and is then cached. The default is `metastore_jars: path`, whose client jars
  are baked into the Spark image at build time — no internet, no first-query
  delay. The `maven` escape hatch re-resolves on *every* Connect server start,
  not once.

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
