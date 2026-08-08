# Changelog

## 1.6.0

- The Hive metastore client is baked into the image and used via
  `spark.sql.hive.metastore.jars=path`. Resolving it from Maven — what 1.4.0
  introduced — turned out to re-run a ~270-module Ivy resolution on *every*
  Connect server start rather than only the first, since the metadata lookup
  still goes to Maven Central with a warm cache. Measured on a real install:
  over four minutes before a query reached the metastore, repeated after each
  restart, and SQL-by-name simply unavailable without internet.
- The jars are resolved in a separate build stage, so maven and its build
  clutter stay out of the add-on image; only the resolved jars are copied in.
- `metastore_jars: maven` restores the old behaviour if the baked jars ever
  prove wrong.

## 1.5.0

- The **Spark Connect application UI** is published on host port 4040 — running
  queries, jobs and stages. It was not exposed at all, which is the one view
  that answers "what is this slow query actually doing". Its port is pinned, so
  it cannot drift to 4041 and leave the published port dead.
- An **Open Web UI** button on the add-on page, landing on the master UI, which
  links onward to the worker and to each running application.
- `metastore_uris` documents the add-on-hostname form alongside the gateway one,
  and that Spark must be restarted after the metastore restarts — its Hive
  catalog is built once per JVM and otherwise keeps using dead sockets.

## 1.4.0

- New `metastore_uris` option. Set it to the Pipeline Metastore add-on
  (`thrift://172.30.32.1:9083`) and Spark uses a Hive catalog, so tables have
  names; empty — the default — keeps the session-local catalog and path-only
  addressing that 1.3.0 and earlier had.
- When it is set, Spark is told `spark.sql.hive.metastore.version 4.1.0` with
  `jars maven`: the built-in Hive client is 2.3.10 and cannot talk to a 4.1.0
  metastore, so a matching client is fetched once into the persisted Ivy cache.

## 1.3.0

- **Runs a Spark Connect server** on `:15002`, alongside the master and worker.
  This is how Pipeline Airflow submits work now: the Connect server process is
  the driver, so Spark's heap, the S3A credentials and the Delta jars all stay
  in this add-on, and Airflow needs no Spark installation at all.
- Delta's Connect plugins (`delta-connect-server`) are loaded, so `DeltaTable`
  and `MERGE` work over a remote session. Delta Connect is upstream-flagged as
  **preview**; it is exercised here by the tracker merge job.
- Started with `spark-submit` rather than `sbin/start-connect-server.sh`, which
  hands off to `spark-daemon.sh` and returns success immediately — a server that
  died on startup would have looked healthy. This form is supervised with the
  master and worker.

## 1.2.2

- Corrected the description of port 6066. It was documented as the port Airflow
  submits through in cluster mode; since Pipeline Airflow 1.4.0 submits are
  client mode over RPC 7077 and nothing here uses 6066. The port stays published
  for JVM jobs submitted that way by hand.
- The networking note now mentions the part that actually bites when submitting
  from another machine: in client mode the executors connect **back** to your
  driver, so your machine has to be reachable from the add-on too.

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
