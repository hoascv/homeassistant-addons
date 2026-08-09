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
2. In **this** add-on's configuration, put the same value in
   `gym_tracker_api_token` / `coop_tracker_api_token`, and restart.

That's all. **No host port needs publishing.** Add-ons reach each other by
hostname on the Supervisor network, and every add-on from one repository shares
a prefix — this container is `<prefix>-pipeline-airflow`, so the trackers are
`<prefix>-gym-tracker` and `<prefix>-coop-tracker`. The prefix is read from this
container's own hostname, so it follows the repository being re-added (the
prefix is its hash) or a local install (`local-…`) without being reconfigured.

Keeping the trackers off any host port also keeps their API off your LAN, where
an `api_token` would be the only thing in front of it.

Set `gym_tracker_base_url` / `coop_tracker_base_url` only to **override** that —
a tracker on another machine, say. A published host port then has to match:

   | Option | Example |
   |---|---|
   | `gym_tracker_base_url` | `http://172.30.32.1:8099` |
   | `coop_tracker_base_url` | `http://172.30.32.1:8098` |

   Airflow Variables of the same names also work, but the options are the
   reliable route: they are read *first*, and they can't suffer the failure a UI
   Variable can, where a stray character in the **key** is invisible in the list
   and makes the Variable impossible to find. An option left blank is not
   exported, so it never shadows a Variable you set deliberately.

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

## Is the storage the limit?

When writes get slower at certain times of day, the question is whether the
storage is the constraint or the workload grew — and a maximum settles neither.
`jobs/iobench.sh` runs a load shaped like a refresh and reports what the device
did under it: `bulk` sequential writes as Spark writes parquet, `commit` small
fsynced writes as Postgres' WAL does, `read` back, and `mixed` — both at once,
which is what a refresh actually does and the only phase where contention shows.

It is published to `/share/pipeline-airflow/lib/iobench.sh` on every start:

```sh
sh /share/pipeline-airflow/lib/iobench.sh --dir /data --size 256
```

Two numbers carry the argument, and neither is throughput. **busy%** is the share
of time the device had a request in flight; **write wait** is milliseconds per
write:

| busy% | write wait | reading |
|---|---|---|
| high | high | **saturated** — the device is the limit |
| low | high | slow device, or one shared with something you cannot see |
| high | low | busy but keeping up |
| low | low | the storage is not the problem; look at the workload |

`commit` will show far lower MB/s than `bulk` on any device — measured here,
1422 MB/s against 2.9 MB/s on the same disk. That is not a fault: fsynced 8 KiB
writes are latency-bound where bulk writes are throughput-bound, and a pipeline
that commits often lives in the second world.

`simulate_io.ipynb` drives it, saves runs by label, and compares a quiet baseline
against one taken during the slow window — same workload either side, so the
difference is the device rather than the work.

### Running it on other servers

**POSIX `sh` and coreutils only** — no Python, no packages, nothing to install.
That is the point: copy the one file anywhere and results are comparable.

```sh
scp iobench.sh user@other:/tmp/ && ssh user@other 'sh /tmp/iobench.sh --dir /var/tmp --size 256'
```

Verified to give identical results under **dash** (Debian/Ubuntu `/bin/sh`),
**ksh** and **bash**. No arrays, no `[[`, no `local`, no `seq`; the `awk` program
avoids line-continued string literals so `mawk` (Debian's default), `gawk`,
`busybox awk` and BSD `awk` all read it alike.

Where a system differs it detects and reports rather than assuming:

| Difference | What happens |
|---|---|
| No `O_DIRECT` (tmpfs, some overlays) | falls back to `conv=fsync`, and says figures include the page cache |
| No `/proc/diskstats` | device columns read 0, workload timings still stand, `device:` says `unknown` |
| No `/proc/uptime` or `date +%N` (busybox) | whole-second timing, announced on the `timer:` line — raise `--size` |
| `df` wrapping long device names (any LVM host) | handled: free space is read from the last line, counting fields from the right |

That last one was a real bug rather than a hypothetical: on
`/dev/mapper/ubuntu--vg-ubuntu--lv`, `df` wraps, the naive parse read 0 MB free,
and the script refused to start on a healthy machine.

**Untested: a real Linux host.** This was written on macOS, which has no
`/proc/diskstats`, so the utilisation and wait columns are exercised only by the
equivalent arithmetic in the Add-on Watchdog's unit tests. If those columns read
0 while `device:` names a real disk, that is the thing to report.

The script writes real data and competes with whatever else is running. It
refuses to start without enough free space and removes its test file on the way
out, including on interrupt. For continuous capture rather than a point
measurement, the Add-on Watchdog samples the same counters every ten seconds.

## Developing

The pipeline code is three files, and none of them need a rebuild to read:

| File | What it is |
|---|---|
| `dags/trackers_ingest.py` | orchestration only — Variables, watermark, S3 archiving |
| `jobs/trackers_feed.py` | reading a tracker: discovery, HTTP, bootstrap-vs-paging |
| `jobs/trackers_merge.py` | the Spark job that MERGEs a batch into Delta |
| `jobs/lakehouse.py` | reading those tables back, with a schema per table; `register()` names them in the optional metastore |
| `jobs/iobench.sh` | storage under a refresh-shaped load — POSIX `sh` only, so it runs on a server with no Python |

The DAG module is kept thin deliberately: it imports `airflow.sdk` and two
providers and builds its DAGs on import, so nothing in it can be unit-tested.
Anything worth testing lives in `jobs/`, which is plain Python.

### From JupyterLab

The add-on publishes `jobs/` to `/share/pipeline-airflow/lib/` on every start, so
a JupyterLab add-on on the same machine can import exactly what the scheduler
runs — they cannot drift, because the copy is refreshed each boot. Starter
notebooks are seeded once into `/share/pipeline-airflow/notebooks/`:

| Notebook | |
|---|---|
| `pipeline_scratchpad.ipynb` | the same code the scheduler runs, importable — read a tracker, query the lakehouse, try a merge |
| `explore_lakehouse.ipynb` | the Delta tables through `lakehouse.py`, by path: volume, adherence, weight, eggs |
| `explore_catalog.ipynb` | the same data by **name** — `spark.sql("SELECT * FROM gym_tracker.workout_logs_typed")`, needing the optional Pipeline Metastore add-on |
| `simulate_io.ipynb` | runs a refresh-shaped write load and reports what the storage did under it — for when writes get slower at certain times of day |

Seeding is `cp -n`, so a notebook you have edited is never overwritten and a
newly added one still arrives.

**The JupyterLab file browser will not show `/share`.** That add-on pins its root:

```python
c.ServerApp.root_dir = '/config/notebooks'
```

so nothing under `/share` can appear in the file tree, however it is mapped. The
mount is still there — Python in a notebook can `open()` and import from `/share`
perfectly well — it is only the browser that is confined. Symlink it in.

JupyterLab also needs the Spark Connect client, which is **pure Python — no JVM
and no Spark installation**. Both go in the JupyterLab add-on's own
configuration, where they re-run at every start and so survive updates:

```yaml
init_commands:
  - ln -sfn /share/pipeline-airflow /config/notebooks/pipeline
  - pip install --no-cache-dir "pyspark-client==4.1.3"
  - pip install --no-cache-dir --no-deps "delta-spark==4.3.1"
```

The symlink puts `pipeline/` at the top of the file browser, with `dags/`,
`lib/` and `notebooks/` inside it — so the DAGs are editable there too.

`--no-deps` on `delta-spark` because it pins `pyspark<=4.1.1` and would otherwise
pull the full ~400 MB distribution in on top of the slim client.

```python
import sys; sys.path.insert(0, "/share/pipeline-airflow/lib")
from trackers_merge import merge_batch
spark = SparkSession.builder.remote("sc://172.30.32.1:15002").getOrCreate()
```

### Editing the tracker DAG in place

`trackers_ingest.py` is normally overwritten in `/share` on every start, so fixes
reach an installation that already has an old copy. That also means edits made
there are lost. Set **`manage_bundled_dags: false`** to take ownership:

```
[Pipeline Airflow] dev mode: leaving /share/.../trackers_ingest.py alone
[Pipeline Airflow]   (you own it now; add-on updates will not change it)
```

The trade is real: you stop receiving DAG fixes with add-on updates, and will
have to merge them yourself. `example_pipeline.py` and the notebooks are yours
either way and are never overwritten.

### Tests

```
./scripts/dev-setup.sh          # .venv, and a JDK in .jdk only if you need one
.venv/bin/python -m pytest
```

`dev-setup.sh` fetches a Temurin JDK into `.jdk` rather than installing Java
system-wide, because the Spark tests need a JVM and wanting to run a test suite
is not a reason to change your machine. Without one:

```
.venv/bin/python -m pytest -m "not spark"
```

The Spark tests also skip themselves automatically when no *working* JVM is
present — `java` existing is not the same as `java` running, which macOS in
particular gets wrong. They are the only tests that exercise the MERGE, so CI
installs Java and runs them.
