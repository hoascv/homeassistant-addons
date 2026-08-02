# Changelog

## 2.0.0

- **Spark jobs now run over Spark Connect.** The driver moves out of this
  container and into the Pipeline Spark add-on. Previously it ran here: Spark
  standalone cannot run a PySpark driver in cluster mode, so the only option was
  a client-mode driver sharing this container with the scheduler and api-server,
  and needing the executors to call back across the add-on boundary. Now this
  add-on is a gRPC client and the whole Spark conversation stays inside the
  Spark add-on.
- **No JVM and no Spark distribution here any more** — about 700 MB less image.
  `pyspark-client` replaces them; `delta-spark` stays for the client half of
  Delta Connect only.
- **The MinIO secret no longer touches this container.** There is no
  `spark-defaults.conf` to write, because a Connect client cannot configure the
  server. The `spark_default` connection is gone for the same reason.
- **`spark_port` is replaced by `spark_connect_port`** (default 15002). It is
  handed to the DAGs as the `spark_connect_url` Variable, so the endpoint is
  configured in the add-on options and nowhere else.
- The example DAG uses Connect too. It is yours to edit and is never
  overwritten, so an older copy in `/share` still calling `SparkSubmitOperator`
  will fail — the add-on now says so at startup. Delete it to get the new one.
- **A Variable that exists but can't be found is now diagnosed, not guessed at.**
  `fetch` distinguishes "no Variable with this exact key" from "set but empty",
  and the first case says what actually causes it: a key with a trailing space
  looks identical in the Variables list and defeats an exact lookup.

## 1.5.0

- **The merge job flattens the feed in Spark, not on the driver.** It used to
  `collect()` every archived response and pick it apart with Python loops, so
  Spark only did the write — and after the move to client mode that parsing
  happened inside this add-on, next to the scheduler. The payloads are now
  flattened with Spark expressions on the executors: `map<string,string>` and
  `array<string>` over the raw JSON, which `from_json` fills with re-serialised
  JSON text, so nothing is inferred and nothing is typed. The only thing that
  reaches the driver is the list of table names.
- Verified end-to-end against a real backup: a 254-row bootstrap plus a
  146-change page, checked row by row against an independently computed
  expectation — payloads, soft deletes, actors — and re-applied to confirm the
  merge is idempotent.

## 1.4.0

- **The tracker DAGs can actually read their Variables.** `fetch` read the API
  token through `airflow.models.Variable`, which in Airflow 3 cannot see
  Variables from a task: a worker has no metadata-database access, so the call
  warned and returned the default. A token that *was* set read back as unset and
  the run failed claiming it was missing. Now `airflow.sdk.Variable`, which
  resolves through the execution API. The `dag`/`task` decorators and
  `AirflowFailException` moved to `airflow.sdk` with it.
- **Spark jobs submit in client mode.** Spark standalone supports cluster deploy
  mode only for JVM applications — a PySpark job was rejected before it started
  ("Cluster deploy mode is currently not supported for python applications on
  standalone clusters"). This affected the example DAG and would have hit the
  tracker merge next.
- **The merge job ships with this add-on.** It was referenced at
  `/opt/pipeline/jobs/trackers_merge.py` but never copied into any image, so the
  path did not exist. In client mode the driver runs here, so the job lives here.
- **The driver gets a Spark configuration.** In client mode the driver runs in
  this container, so the workers' settings do not reach it. The add-on now
  writes `spark-defaults.conf` with the MinIO S3A credentials, the Delta and
  hadoop-aws packages, and a driver address the executors can call back on.
  Written to a file, not passed per task, so the MinIO secret never lands in a
  task log.

## 1.3.0

- **An unconfigured tracker now fails the run instead of skipping it.** Skipping
  left the run green, so a pipeline that was loading nothing looked exactly like
  a healthy one — the single state most worth noticing. The failure message
  names the Variables to set.
- **The runs say what they did**: how many rows a bootstrap carried, how many
  changes an incremental pass found, where the watermark moved to, and — when
  there is genuinely nothing new — that too, so a quiet run is quiet on purpose.
- **The tracker DAGs are refreshed on every start.** They were only ever copied
  if absent, which meant a fix to them could never reach an installation that
  already had the old copy. `example_pipeline.py` is still yours to edit and is
  left alone.

## 1.2.1

- **Actually fix the login.** 1.2.0 created the admin account correctly, but the
  add-on then started Airflow with `airflow standalone`, which forces its own
  development auth manager on the way up and discarded it — so the account
  existed while the running server ignored it. The four Airflow components are
  now started directly, which is all `standalone` was doing anyway.
- If one component stops, the add-on stops the rest so Home Assistant restarts
  it, rather than limping along half-running.

## 1.2.0

- **Fix not being able to log in.** `admin_user` and `admin_password` were
  being ignored: Airflow 3 only applies them when the FAB auth manager is
  installed, and it wasn't — so Airflow fell back to its development
  auth manager, which invents a password, prints it to the log once and stores
  it in plaintext. The FAB provider is now installed and selected, so the
  options work as documented.
- **Changing `admin_password` now takes effect.** The old path only ever
  *created* a user and silently did nothing if one already existed, so editing
  the option later changed nothing. The password is applied on every start.
- Sessions survive a restart: the session secret is persisted alongside the
  Fernet key instead of being regenerated each boot.

## 1.1.0

- New **gym_tracker_ingest** and **coop_tracker_ingest** DAGs: pull each
  tracker's change feed, archive the raw responses to MinIO, and merge them
  into Delta tables via Spark. Hourly, incremental, and idempotent — the
  watermark only advances once the merge has succeeded.
- The bundled Spark client is now **4.1.3**, matching the Spark add-on.

## 1.0.3

- Added an **Open Web UI** button on the add-on page, so Airflow can be opened
  from within Home Assistant.

## 1.0.2

- Fixed the image build failing with "You are running pip as root": provider
  packages are now installed as the `airflow` user (as the base image
  requires). The add-on still runs as root (to write `/data` and `/share`) and
  sets `HOME=/home/airflow` so those packages resolve.

## 1.0.1

- Set the base image directly in the Dockerfile and removed `build.yaml`
  (deprecated by Supervisor 2026.04.0, which no longer passes `BUILD_FROM`).

## 1.0.0

- First release. Apache Airflow 3.3 (LocalExecutor), web UI on host port 8085.
- Uses the Pipeline Postgres add-on for its metadata database.
- Pre-wired connections `minio_default` (S3), `spark_default`, and `pipeline_pg`,
  plus an `example_pipeline` DAG that runs MinIO → Spark → Postgres end to end.
- Bundled Spark client for `SparkSubmitOperator`. DAGs live in
  `/share/pipeline-airflow/dags`. amd64 only.
