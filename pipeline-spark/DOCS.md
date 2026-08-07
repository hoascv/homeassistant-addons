# Pipeline Spark

Apache Spark 4.1 running as a **single-node standalone cluster** (a master and one
worker in the same add-on) for the data-pipeline stack. Airflow submits jobs to it;
jobs read/write MinIO over `s3a://` and write results to Postgres over JDBC.

Part of a four-add-on data pipeline: **Pipeline Postgres**, **Pipeline MinIO**,
**Pipeline Spark**, **Pipeline Airflow**. Start order: `postgres → minio → spark → airflow`.

> **Heavy.** Spark wants real memory — set `worker_memory` to what your host can
> spare (default 2G). amd64 only; needs Java 17 (bundled in the image).

## What it does

- Starts a Spark **master** (RPC `:7077`, standalone REST submission API `:6066`,
  web UI `:8082` on the host), one **worker** (web UI `:8083`), and a **Spark
  Connect server** (`sc://…:15002`).
- The Connect server is how Airflow runs jobs: it *is* the driver, so Spark's
  memory and its S3A/Delta configuration stay in this add-on. Airflow holds only
  a gRPC client. Delta's Connect plugins are loaded into it, so `DeltaTable` and
  `MERGE` work remotely — note Delta Connect is upstream-flagged as **preview**.
- Pre-configures the MinIO S3A endpoint and credentials in `spark-defaults.conf`.
- Bakes in the **PostgreSQL JDBC driver** and pulls **hadoop-aws** (matched to the
  image's Hadoop version) on the first submit via `spark.jars.packages` — so the
  first job needs internet and takes a bit longer while it downloads; later jobs use
  the cached copy in `/data/spark/.ivy2`.
- Ships an example job at `/opt/pipeline/jobs/example_job.py` (used by the Airflow
  example DAG).

## Configuration

- **worker_memory** / **worker_cores**: resources for the single worker (e.g. `4G`, 4).
- **minio_endpoint**: S3 endpoint for `s3a://` access (default
  `http://172.30.32.1:9000`, i.e. the Pipeline MinIO add-on).
- **minio_access_key** / **minio_secret_key**: match the MinIO add-on's
  `root_user` / `root_password`.
- **metastore_uris**: empty by default, which leaves Spark on a session-local
  catalog where tables are addressed by path. Set it to the Pipeline Metastore
  add-on (`thrift://172.30.32.1:9083`) to get a Hive catalog and real table
  names — see that add-on's docs. The first query afterwards downloads a
  matching Hive 4.1.0 client, once.

## Submitting jobs

Spark standalone supports `--deploy-mode cluster` **only for JVM applications**.
A PySpark job submitted that way is rejected outright:

```
Cluster deploy mode is currently not supported for python applications
on standalone clusters
```

So scheduled jobs don't use spark-submit at all — they go through the **Spark
Connect server** on `:15002`, whose own process is the driver and runs here. The
client (Airflow) sends an unresolved query plan over gRPC and gets results back;
it needs no Spark installation, no JVM, and no credentials of its own.

A hand-rolled `spark-submit` still works, in **client mode** — the driver then
runs wherever you launched it, and must be reachable from this container.

To submit by hand from inside this add-on:

```
spark-submit --master spark://<host-ip>:7077 \
  /opt/pipeline/jobs/example_job.py s3a://raw/sample.csv \
  jdbc:postgresql://<host-ip>:5432/pipeline pipeline_result pipeline <pw>
```

> **Networking note:** Spark standalone across the add-on boundary uses the host
> gateway (`172.30.32.1`). A client-mode submit needs only the master RPC port,
> `7077` — the REST API on `6066` is for JVM jobs submitted in cluster mode and
> is not used by the bundled DAGs.
>
> Submitting from another machine takes one more thing than people expect: in
> client mode the driver runs on *your* machine and the executors here connect
> **back** to it, so that machine has to be reachable from the add-on, not only
> the other way round. Use the Home Assistant host's LAN IP for `--master`, and
> set `spark.driver.host` to an address this container can reach you on.
