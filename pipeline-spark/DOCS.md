# Pipeline Spark

Apache Spark 4.1 running as a **single-node standalone cluster** (a master and one
worker in the same add-on) for the data-pipeline stack. Airflow submits jobs to it;
jobs read/write MinIO over `s3a://` and write results to Postgres over JDBC.

Part of a seven-add-on data pipeline. Start them in this order:
`postgres → minio → [metastore] → spark → airflow → notebook`. **Pipeline
Metastore** and **Pipeline Postgres Replica** are optional — nothing needs them
until you want table names instead of paths, or a standby.

> **Heavy.** Spark wants real memory — set `worker_memory` to what your host can
> spare (default 2G). amd64 only; needs Java 17 (bundled in the image).

## What it does

- Starts a Spark **master** (RPC `:7077`, standalone REST submission API `:6066`,
  web UI `:8082` on the host), one **worker** (web UI `:8083`), and a **Spark
  Connect server** (`sc://…:15002`) whose application UI is on host port **4040**.
  The worker UI is 8083 *inside* the container as well as outside, unlike the
  master's 8080→8082: Spark builds links from its own view of itself and knows
  nothing of Home Assistant's port remapping, so a worker on a different
  internal port produces a link to a port the host never mapped.
  4040 is the one to open when a query is slow: it shows the running queries,
  jobs and stages of the session Airflow and the notebooks are actually using —
  the master UI at 8082 only shows that the application exists. The add-on page's
  **Open Web UI** button goes to the master.
- The Connect server is how Airflow runs jobs: it *is* the driver, so Spark's
  memory and its S3A/Delta configuration stay in this add-on. Airflow holds only
  a gRPC client. Delta's Connect plugins are loaded into it, so `DeltaTable` and
  `MERGE` work remotely — note Delta Connect is upstream-flagged as **preview**.
- Pre-configures the MinIO S3A endpoint and credentials in `spark-defaults.conf`.
- Bakes in the **PostgreSQL JDBC driver** and pulls **hadoop-aws** (matched to the
  image's Hadoop version) on the first submit via `spark.jars.packages` — so the
  first job needs internet and takes a bit longer while it downloads; later jobs use
  the cached copy in `/data/spark/.ivy2`.
- Ships an example job at `/opt/pipeline/jobs/example_job.py`, as a `spark-submit`
  reference. The Airflow example DAG does not use it — that DAG builds a Spark
  Connect session and does its work inline, with no `spark-submit` anywhere.

## Configuration

- **public_host**: the address you reach this machine on — a LAN IP or hostname,
  no scheme and no port (e.g. `192.168.1.50` or `homeassistant.local`). Empty by
  default.

  **Set this if you want the master UI's links to work.** Spark advertises
  whatever it binds to, and it binds to `0.0.0.0` so the published ports reach
  it — so the master's **Workers** link comes out as `http://0.0.0.0:8081`,
  which no browser can resolve. `public_host` changes only what the web UIs put
  in links; nothing rebinds, and master, worker and Connect keep finding each
  other exactly as before. It cannot be detected from inside the container: your
  browser is on the LAN and this add-on is on the Supervisor's bridge network.
- **worker_memory** / **worker_cores**: resources for the single worker (e.g. `4G`, 4).
- **minio_endpoint**: S3 endpoint for `s3a://` access (default
  `http://172.30.32.1:9000`, i.e. the Pipeline MinIO add-on).
- **minio_access_key** / **minio_secret_key**: match the MinIO add-on's
  `root_user` / `root_password`.
- **metastore_uris**: empty by default, which leaves Spark on a session-local
  catalog where tables are addressed by path. Set it to the Pipeline Metastore
  add-on to get a Hive catalog and real table names — see that add-on's docs.

  **Use the add-on hostname, not the gateway:**

  ```
  thrift://<prefix>-pipeline-metastore:9083
  ```

  `<prefix>` is your repository's hash — the same one in this add-on's own
  hostname. This is how the Airflow DAGs reach the trackers, and it needs no
  published port at all.

  The gateway form (`thrift://172.30.32.1:9083`) looks equivalent and is not.
  On at least one host, something else answers that port: it accepts the
  connection and holds it open, so a plain socket test says "healthy" while
  every Thrift request vanishes — Spark reports `Socket is closed by peer` and
  the metastore logs nothing, because nothing arrived. If you see that, the
  proof is to set the metastore's `log_level: DEBUG` and compare: a connection
  that truly arrives shows up there within a second.

  **Restart Spark after the metastore restarts.** The Connect server builds its
  Hive catalog once per JVM, so it keeps using sockets the restart already
  killed, and every query then fails with `Socket is closed by peer`.

- **metastore_jars** (default `path`): where the Hive 4.1.0 client comes from.

  `path` uses jars baked into the image at build time — no network, no delay.
  `maven` resolves them at run time instead, which is how 1.4.0 and 1.5.0
  behaved and is kept only as an escape hatch: it re-resolves a ~270-module
  dependency tree on **every** Connect server start, not just the first,
  because the metadata lookup goes to Maven Central even with a warm cache. On
  a real install that measured over four minutes before a single query reached
  the metastore, and it meant SQL-by-name did not work at all without internet.

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
