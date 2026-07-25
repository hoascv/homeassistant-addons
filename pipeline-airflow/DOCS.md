# Pipeline Airflow

Apache Airflow 3.3 (LocalExecutor) — the orchestrator for the data-pipeline stack.
It stores its metadata in the Pipeline Postgres add-on and ships **pre-wired
connections** to MinIO and Spark plus an example end-to-end DAG.

Part of a four-add-on data pipeline: **Pipeline Postgres**, **Pipeline MinIO**,
**Pipeline Spark**, **Pipeline Airflow**. Start order: `postgres → minio → spark → airflow`.

> **Start Postgres, MinIO and Spark first.** Airflow waits for its metadata
> database on boot; Spark and MinIO must be up before you run the DAG.

## What it does

- Runs `airflow standalone` (api-server + scheduler + triggerer + dag-processor)
  with **LocalExecutor**, web UI on host port **8085**.
- Uses `postgresql://airflow@<postgres_host>:<postgres_port>/airflow` for metadata
  (the `airflow` DB the Pipeline Postgres add-on created).
- Pre-creates three connections (via environment, so they always exist):
  - **minio_default** — AWS/S3 connection pointed at MinIO's endpoint,
  - **spark_default** — the Spark standalone master,
  - **pipeline_pg** — your pipeline Postgres database.
- Seeds `example_pipeline` into `/share/pipeline-airflow/dags` on first boot. Put
  your own DAGs in that folder (editable via the Samba / File-editor add-ons).
- Includes a Spark client so `SparkSubmitOperator` can submit to the cluster.

## Configuration

- **admin_user** / **admin_password**: the Airflow web login. **Change the
  password before starting.**
- **postgres_host** / **postgres_port** / **airflow_db_password**: how to reach the
  metadata DB. Defaults target the Pipeline Postgres add-on via the host gateway
  (`172.30.32.1:5432`); `airflow_db_password` must match that add-on's value.
- **spark_master** / **spark_port**: the Spark master (default `172.30.32.1:7077`).
- **minio_endpoint** / **minio_access_key** / **minio_secret_key**: the MinIO S3 API
  and credentials (match the Pipeline MinIO add-on).
- **pipeline_pg_user** / **pipeline_pg_password** / **pipeline_pg_db**: the pipeline
  database the example writes results to (match Pipeline Postgres).

## Running the example

1. Open the UI at `http://<home-assistant-host-ip>:8085` and log in.
2. Unpause **`example_pipeline`** and trigger it.
3. It uploads a CSV to MinIO's `raw` bucket, submits a Spark job that aggregates it
   and writes to the `pipeline_result` table in Postgres, then asserts rows exist.

Watch the Spark master UI (`:8082`) to see the job run. If the first Spark task is
slow, it's downloading `hadoop-aws` once (cached afterwards).

> **Note:** the example submits in Spark **cluster** deploy mode, so the job file
> must exist on the Spark worker — it's baked into the Pipeline Spark image. For your
> own Spark jobs, either stage them on MinIO (`s3a://…`) or bake them in.
>
> **Security:** the web UI is published on the host network. Use a strong admin
> password and don't expose the host to the internet.
