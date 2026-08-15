# Pipeline Notebook

JupyterLab, already wired to the data-pipeline stack: Spark Connect, the Delta
lakehouse on MinIO, and the tracker change feeds.

Part of a seven-add-on data pipeline. Start them in this order:
`postgres → minio → [metastore] → spark → airflow → notebook`. **Pipeline
Metastore** and **Pipeline Postgres Replica** are optional — nothing needs them
until you want table names instead of paths, or a standby.

> **Start Pipeline Airflow at least once first.** It creates
> `/share/pipeline-airflow/`, which is this add-on's home directory, and
> publishes the pipeline modules into `lib/` there.

## Why not the community JupyterLab add-on

That one works, but fights this use case on three fronts: it pins its file
browser to `/config/notebooks`, so `/share` cannot appear in the tree however it
is mapped; packages have to be reinstalled through `init_commands` on every
start; and it clones example notebooks from GitHub on first boot. This add-on
instead opens directly on the pipeline, with the client libraries baked in.

If you already use the community one, nothing here replaces it — they coexist.

## What it does

- Serves JupyterLab **through Home Assistant ingress only**, with nginx in front.
  That is not decoration: Home Assistant strips the ingress prefix before
  forwarding, while JupyterLab needs it in `base_url` to build its asset and
  websocket URLs, so nginx adds it back. Jupyter itself binds loopback, and nginx
  answers only Home Assistant's ingress address. There is no `ports:`
  section, so the notebook is never reachable from your LAN. That is deliberate:
  a notebook server executes arbitrary code as root on the Home Assistant host,
  and Home Assistant's own login is a better gate than a token to mislay.
- Opens on `/share/pipeline-airflow`, so `dags/`, `lib/` and `notebooks/` are all
  in the file browser — the tracker DAG is editable here (see
  `manage_bundled_dags` in the Pipeline Airflow docs, or your edits are
  overwritten on its next start).
- Puts `/share/pipeline-airflow/lib` on `PYTHONPATH`, so
  `from trackers_merge import merge_batch` works with no `sys.path` line. That
  directory is refreshed by the Airflow add-on on every start, so a notebook and
  the scheduler cannot drift.
- Bakes in **`pyspark-client`** and **`delta-spark`** — no JVM and no Spark
  installation, because the Spark Connect client is pure Python over gRPC.
  `pandas`, `pyarrow` and `numpy` come along with it, so `.toPandas()` works.
- Exports the stack's addresses and credentials as environment variables, so a
  fresh notebook needs to be told nothing.

## Configuration

- **spark_connect_url**: the Pipeline Spark add-on's Connect endpoint (default
  `sc://172.30.32.1:15002`). Available in notebooks as `$SPARK_CONNECT_URL`.
- **minio_endpoint** / **minio_access_key** / **minio_secret_key**: match the
  Pipeline MinIO add-on. Exported as `$MINIO_ENDPOINT`, `$MINIO_ACCESS_KEY`,
  `$MINIO_SECRET_KEY` — for reading MinIO directly with boto3 or duckdb; Spark
  gets its own credentials from the Spark add-on.
- **gym_tracker_api_token** / **coop_tracker_api_token**: the same values as in
  the tracker add-ons, if you want to call the feeds from a notebook. Exported as
  `$GYM_TRACKER_API_TOKEN` / `$COOP_TRACKER_API_TOKEN`.

## Getting started

Open it from the sidebar and run `notebooks/pipeline_scratchpad.ipynb`, which
covers reading a tracker feed, querying the Delta tables, and running a merge
against a scratch path.

```python
from pyspark.sql import SparkSession
import os

spark = SparkSession.builder.remote(os.environ["SPARK_CONNECT_URL"]).getOrCreate()
spark.read.format("delta").load("s3a://lakehouse/gym_tracker/workout_logs").count()
```

`data` in those tables is a JSON string, on purpose — the trackers gain columns
regularly and inferring a schema per batch would eventually produce two batches
that disagree about a type. Parse it with an explicit schema:

```python
from pyspark.sql import functions as F

SCHEMA = "id long, ts string, reps long, sets long, hr_avg long"
workouts = logs.select(F.from_json("data", SCHEMA).alias("w")).select("w.*")
```

Anything the schema doesn't mention is ignored, and anything missing comes back
null, so this keeps working as the apps change.

## Running outside Home Assistant

You don't have to use this add-on at all. **Pipeline Spark**'s Connect server
(`:15002`) and **Pipeline MinIO**'s S3 API (`:9000`) are both published to
your LAN, 1:1, in their own `config.yaml` — the same `SparkSession.builder
.remote(...)` call the notebooks here use works unchanged from a plain local
JupyterLab or VS Code, full-width browser tab instead of Home Assistant's
ingress iframe.

Install the same versions this add-on bakes in, so nothing drifts between the
two:

```
pip install "jupyterlab>=4.2,<5" "pyspark-client==4.1.3"
pip install --no-deps "delta-spark==4.3.1"
```

Then set what this add-on would otherwise export for you — using the Home
Assistant host's real LAN address (or its `public_host`, if you've set one in
the Pipeline Spark add-on) instead of the internal gateway `172.30.32.1`,
which only resolves from other add-on containers:

```python
import os
os.environ["SPARK_CONNECT_URL"] = "sc://<home-assistant-host-ip-or-name>:15002"
os.environ["MINIO_ENDPOINT"] = "http://<home-assistant-host-ip-or-name>:9000"
os.environ["MINIO_ACCESS_KEY"] = "<root_user>"
os.environ["MINIO_SECRET_KEY"] = "<root_password>"
```

Getting the notebook file itself onto your machine is one of two things,
depending on what it imports:

- A notebook with no `/share/pipeline-airflow/lib` import (`from lakehouse
  import ...`, `from trackers_merge import ...`) — copy it straight out of
  this repository's `pipeline-airflow/notebooks/` folder. No Home Assistant
  access needed at all.
- A notebook that does import from `lib/` — that directory isn't in git, so
  pull the live file from `/share/pipeline-airflow/notebooks/` the same way
  the top-level README's "Testing locally without Git" section reaches
  `/addons`: a Samba or SSH & Web Terminal share onto the Home Assistant host.

> **This inherits Pipeline Spark's own security note.** A session opened this
> way runs with the same access as the Airflow DAGs' own — Spark Connect has
> no authentication or credential of its own, so anyone who can reach `:15002`
> already can. See the **No authentication** callout in Pipeline Spark's docs
> before forwarding that port any further than your own LAN.

## Notes

- Settings and workspaces live in `/data`, so the layout survives a restart.
  Notebooks live in `/share`, so they survive a *reinstall*.
- `pyspark-client` is pinned to the Pipeline Spark add-on's Spark version and
  `delta-spark` to its Delta jars. Upgrading Spark means upgrading both here.
- amd64 only, like the rest of the pipeline.
