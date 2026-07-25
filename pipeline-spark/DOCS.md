# Pipeline Spark

Apache Spark 4.2 running as a **single-node standalone cluster** (a master and one
worker in the same add-on) for the data-pipeline stack. Airflow submits jobs to it;
jobs read/write MinIO over `s3a://` and write results to Postgres over JDBC.

Part of a four-add-on data pipeline: **Pipeline Postgres**, **Pipeline MinIO**,
**Pipeline Spark**, **Pipeline Airflow**. Start order: `postgres → minio → spark → airflow`.

> **Heavy.** Spark wants real memory — set `worker_memory` to what your host can
> spare (default 2G). amd64 only; needs Java 17 (bundled in the image).

## What it does

- Starts a Spark **master** (RPC `:7077`, standalone REST submission API `:6066`,
  web UI `:8082` on the host) and one **worker** (web UI `:8083`).
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

## Submitting jobs

Airflow submits with `--deploy-mode cluster` to the standalone REST API, so the
driver runs inside this add-on (no ports need to be published back from Airflow).
For a cluster-mode submit the job file must exist **on the worker** — either baked
into this image (like the example) or staged on MinIO (`s3a://…`).

To submit by hand from elsewhere:

```
spark-submit --master spark://<host-ip>:7077 --deploy-mode cluster \
  /opt/pipeline/jobs/example_job.py s3a://raw/sample.csv \
  jdbc:postgresql://<host-ip>:5432/pipeline pipeline_result pipeline <pw>
```

> **Networking note:** Spark standalone across the add-on boundary uses the host
> gateway (`172.30.32.1`) and the REST submission API. If you submit from another
> machine, use the Home Assistant host's LAN IP and make sure ports 7077/6066 are
> reachable.
