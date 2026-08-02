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
- Pre-creates two connections (via environment, so they always exist):
  - **minio_default** — AWS/S3 connection pointed at MinIO's endpoint,
  - **pipeline_pg** — your pipeline Postgres database.
- Seeds `example_pipeline` into `/share/pipeline-airflow/dags` on first boot. Put
  your own DAGs in that folder (editable via the Samba / File-editor add-ons).
- Talks to Spark over **Spark Connect** (`sc://…:15002`). There is no JVM and no
  Spark installation in this add-on — just a gRPC client — so Spark's driver runs
  in the Pipeline Spark add-on rather than beside the scheduler.

## Configuration

- **admin_user** / **admin_password**: the Airflow web login. **Change the
  password before starting.** Changing it later also works — it's reapplied
  every time the add-on starts, so you can reset a forgotten one from here.
- **postgres_host** / **postgres_port** / **airflow_db_password**: how to reach the
  metadata DB. Defaults target the Pipeline Postgres add-on via the host gateway
  (`172.30.32.1:5432`); `airflow_db_password` must match that add-on's value.
- **spark_master** / **spark_connect_port**: the Spark Connect endpoint (default
  `172.30.32.1:15002`, i.e. the Pipeline Spark add-on). Supplied to the DAGs as
  the `spark_connect_url` Variable, so it is configured here and nowhere else.
- **minio_endpoint** / **minio_access_key** / **minio_secret_key**: the MinIO S3 API
  and credentials (match the Pipeline MinIO add-on).
- **pipeline_pg_user** / **pipeline_pg_password** / **pipeline_pg_db**: the pipeline
  database the example writes results to (match Pipeline Postgres).

## Opening Airflow from Home Assistant

The add-on page has an **Open Web UI** button that opens Airflow (on host port
8085) from within Home Assistant.

To add it to the **sidebar** instead, use a `panel_iframe` in Home Assistant's
`configuration.yaml`:

```yaml
panel_iframe:
  airflow:
    title: "Airflow"
    icon: mdi:airplane-takeoff
    url: "http://<home-assistant-host-ip>:8085"
```

Because Airflow requires a fixed base URL and Home Assistant's ingress path is
dynamic, the add-on does **not** use ingress (an embedded sidebar panel with no
extra port). The iframe above is the closest sidebar experience; note the iframe
loads over HTTP, so a browser blocking mixed content on an HTTPS Home Assistant
may open it in a new tab instead.

## Running the example

1. Open the UI at `http://<home-assistant-host-ip>:8085` and log in.
2. Unpause **`example_pipeline`** and trigger it.
3. It uploads a CSV to MinIO's `raw` bucket, submits a Spark job that aggregates it
   and writes to the `pipeline_result` table in Postgres, then asserts rows exist.

Watch the Spark master UI (`:8082`) to see the job run. If the first Spark task is
slow, it's downloading `hadoop-aws` once (cached afterwards).

> **Note:** Spark work runs over **Spark Connect**. A task here builds a session
> with `.remote("sc://…:15002")` and the driver runs inside the Pipeline Spark
> add-on, which already holds the S3A credentials and the Delta jars. Nothing is
> submitted, so there is no `spark_default` connection and no `spark-defaults.conf`
> in this container. A consequence worth knowing: a job's own `print()` output
> goes to the Connect server's log, so the bundled DAGs return their results and
> log them from the task instead.
>
> **Security:** the web UI is published on the host network. Use a strong admin
> password and don't expose the host to the internet.

## Loading the trackers into Delta

Two DAGs, `gym_tracker_ingest` and `coop_tracker_ingest`, pull each add-on's
change feed and merge it into Delta tables on MinIO at
`s3a://lakehouse/<source>/<table>`.

Each run reads its watermark, fetches either a full snapshot (first run) or
just the changes since, archives every raw response to `s3a://raw/<source>/`,
and submits the `trackers_merge` Spark job. The watermark advances only once
the merge succeeds, so a failure re-runs the batch — the merge is keyed on the
row id and guarded by the change sequence, so that converges rather than
double-counting.

To set it up, for each tracker:

1. In the tracker add-on's configuration, set an **api_token**.
2. In its **Network** section, publish a host port (they both listen on 8099
   internally, so give them different host ports).
3. Add the Airflow Variables:

   | Variable | Example |
   |---|---|
   | `gym_tracker_base_url` | `http://172.30.32.1:8099` |
   | `gym_tracker_api_token` | the token you set |
   | `coop_tracker_base_url` | `http://172.30.32.1:8098` |
   | `coop_tracker_api_token` | the token you set |

A source whose token isn't set **fails** the run rather than skipping it. A skip
would leave the run green, and a pipeline quietly loading nothing looks exactly
like a healthy one.

`trackers_ingest.py` is maintained by the add-on and replaced on every start, so
fixes reach you on update — edit it in the repository rather than in
`/share`. `example_pipeline.py` is yours and is never overwritten.

Rows land with the payload as a JSON string plus `row_id`, `seq`, `changed_at`
and `deleted_at`. Deletes are soft — the row keeps its last known state — since
these apps do delete (un-ticking a challenge removes the tick *and* the workout
it logged), and "logged then taken back" is worth keeping. The payload stays
JSON rather than typed columns because both apps gain columns regularly; parse
it downstream with `from_json` and an explicit schema for the tables you query.
