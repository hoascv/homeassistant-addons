# Changelog

## 2.10.3

- **The metastore URI in the code was the one that does not work.** The error
  message in `lakehouse.py`, both catalog notebooks and one test all told you to
  set `thrift://172.30.32.1:9083` — the gateway form that, on at least one host,
  is answered by something that is not the metastore. All now say
  `thrift://<prefix>-pipeline-metastore:9083`, and say why.
- Documentation fixes: the docs claimed the add-on runs `airflow standalone`,
  which `run.sh` deliberately does not — standalone forces the auth manager to
  `SimpleAuthManager` and silently discards the configured `admin_password`.
  "The pipeline code is three files" sat above a five-row table. The change
  feed's `actor` column was undocumented.
- Corrected the opening line: seven add-ons, not four.

## 2.10.2

- `iobench.sh` told two different failures apart. "device: unknown —
  /proc/diskstats unavailable" was printed both when procfs really was masked
  *and* when the path simply had no backing device in it — a `--dir` on tmpfs or
  an overlay. Those need opposite responses: the first cannot be fixed by any
  `--dir`, the second is fixed by pointing at disk-backed storage, and the
  message now names which it is.
- Device columns print `-` rather than `0.0` when there is no device. Zero read
  as a measurement — "the disk did nothing" — when it meant "not measured".

## 2.10.1

- Fix `iobench.sh` refusing to run on any LVM system. `df` wraps onto a second
  line when the device name is long — `/dev/mapper/ubuntu--vg-ubuntu--lv` and
  anything like it — so reading a fixed field of line 2 found the empty first
  line, computed 0 MB free, and declined to start on a healthy machine. Free
  space now comes from the last line, counting fields from the right.
- The `awk` program no longer uses a line-continued string literal — valid POSIX,
  but concatenation is read identically by `mawk` (Debian's default), `gawk`,
  `busybox awk` and BSD `awk`.
- Verified to produce identical results under `dash`, `ksh` and `bash`.
- Documents the portability position, including what is still untested: the
  `/proc/diskstats` columns have never run on a real Linux host.
- 2.10.0 shipped without its DOCS section: the insertion was anchored on a
  heading that exists in the Spark add-on's docs, not this one, so the replace
  silently did nothing.

## 2.10.0

- **`jobs/iobench.sh`** — storage under a refresh-shaped write load: bulk
  sequential writes, small fsynced commits, a read back, and both at once, with
  device utilisation and per-operation wait for each. Answers whether the storage
  is the limit or the workload grew, which a maximum on its own cannot.
- POSIX `sh` and coreutils only, so it runs on a server with no Python and no way
  to install one; published to `/share/pipeline-airflow/lib/` and meant to be
  copied elsewhere, since identical workloads on different storage are the
  cleanest comparison available.
- Degrades rather than refuses: `/proc/diskstats` gives utilisation and latency
  where it exists, and the workload timings stand where it does not. It reports
  which timer it used and whether O_DIRECT was available, because a run that
  includes the page cache is not comparable to one that does not.
- **`notebooks/simulate_io.ipynb`** drives it, saves runs by label, and compares
  a quiet baseline against one taken during the slow window.
- The `lib/` publish step now includes shell tools, not only `*.py`.

## 2.9.1

- `register()` prints one line per table. A table the DAGs have not written yet
  announced "reading …" and then "not in the lakehouse yet, skipping", which
  reads like a failure rather than an absence.

## 2.9.0

- `register()` prints what it is doing, table by table. Registering seventeen
  tables is a Delta log read and two DDL statements each, minutes of work
  against S3 and the metastore, and it previously ran silently — indistinguishable
  from a hang.
- It also skips what the catalog already has, so a second run costs one
  statement instead of repeating all of it. `refresh=True` forces the work,
  `verbose=False` silences the output.

## 2.8.0

- `lakehouse.catalog_diagnostics(spark)` reports every conf that decides
  whether SQL-by-name works, and `explore_catalog.ipynb` prints them when it
  finds no catalog. "No Hive catalog" has two causes needing opposite fixes —
  `metastore_uris` never set, or set and not reported — and the notebook now
  says which rather than leaving it to be guessed at.

## 2.7.1

- Fix `catalog_available()` reporting no Hive catalog on a correctly configured
  cluster. It tested `spark.sql.catalogImplementation`, a *static* SQL conf
  fixed when the Connect server started, and a Spark Connect session does not
  reliably surface those to `spark.conf.get` — so the answer could be "no
  catalog" with the metastore wired up and working, and `register()` would
  refuse. The metastore URI is written at the same moment by the Spark add-on
  and is an ordinary conf, so it is now consulted as a second, independent
  signal.

## 2.7.0

- New notebook, `explore_catalog.ipynb`: the lakehouse by name rather than by
  path — `spark.sql("SELECT * FROM gym_tracker.workout_logs_typed")`. It
  registers the tables, walks the catalog, and carries the SQL equivalents of
  the analyses in `explore_lakehouse.ipynb`, including the two traps that
  survive the move to SQL (a null `sets`, and `_actor` being null for
  everything the bootstrap loaded).
- Every cell is guarded on `catalog_available()`, so without the optional
  Pipeline Metastore add-on the notebook explains what is missing instead of
  failing.

## 2.6.0

- `lakehouse.register(spark)` gives the Delta tables names in the optional
  Pipeline Metastore add-on, so they can be queried as
  `gym_tracker.workout_logs` instead of an `s3a://` path. Registration is
  metadata only — nothing is moved or rewritten — and each table also gets a
  `<name>_typed` view applying the same schema and live-row filter that
  `table()` applies in Python.
- `lakehouse.catalog_available(spark)` reports whether a session has a Hive
  catalog at all. Both notebooks use it to add a metastore section that skips
  itself, with an explanation, when the add-on isn't installed — which is the
  default.
- Everything else is unchanged: the DAGs still address tables by path, and
  `table()` / `tables()` behave exactly as before with or without a catalog.

## 2.5.0

- **`jobs/lakehouse.py`** — reading the Delta tables back as something you can
  analyse. The merge stores each payload as a JSON string deliberately; this
  declares the schema once per table, so `table(spark, "gym_tracker",
  "workout_logs")` gives typed columns, live rows only, and the change metadata
  (`_seq`, `_changed_at`, `_actor`, `_deleted_at`) alongside. `include_deleted=True`
  answers the question a last-modified column never could: what was logged and
  then taken back.
- Two helpers that encode traps found while writing it: `total_reps()` counts a
  missing `sets` as one — the app stores none for single-set entries, so a plain
  `sets * reps` silently nulls whole days — and `held_seconds()` keeps duration
  exercises out of the same hole.
- **`notebooks/explore_lakehouse.ipynb`** — training volume by day and by
  exercise, challenge adherence and what gets un-ticked, weight against the goal,
  Garmin sleep and resting heart rate, and the coop's eggs and costs. Every cell
  was executed against the real backup before shipping.
- `tables()` no longer returns an empty dict when nothing can be read. One table
  missing means the DAG hasn't loaded it; *all* of them missing means something
  systemic — no Delta jars, wrong root — and saying so beats sending you to look
  for the wrong problem.

## 2.4.1

- Fix the JupyterLab instructions. 2.4.0 said the seeded notebook would be found
  under `/share/pipeline-airflow/notebooks/`, but that add-on pins its file
  browser to `c.ServerApp.root_dir = '/config/notebooks'`, so nothing under
  `/share` can ever appear in the tree — mapping it `rw` is necessary and not
  sufficient. A symlink `init_command` puts it there, and the docs now say so.
  (Python in a notebook could always read `/share` directly; only the file
  browser was confined.)

## 2.4.0

- **The pipeline code can be edited and tested from JupyterLab.** `jobs/` is
  published to `/share/pipeline-airflow/lib/` on every start, so a JupyterLab
  add-on on the same machine imports exactly what the scheduler runs — refreshed
  each boot, so the two cannot drift. A starter notebook is seeded once into
  `/share/pipeline-airflow/notebooks/`, covering the feed, querying the Delta
  tables, and running a merge against a scratch path. `DOCS.md` gives the
  `init_commands` for the client packages; no JVM is needed, because the Spark
  Connect client is pure Python.
- **`manage_bundled_dags`** (default `true`). Set it `false` to own
  `trackers_ingest.py` in `/share` — it is otherwise overwritten on every start,
  which is what lets fixes reach an existing installation, and equally what
  discards anything edited there. Still seeded when absent, so dev mode on a
  fresh install isn't empty.
- **A test suite**, run with `./scripts/dev-setup.sh && .venv/bin/python -m pytest`:
  the HTTP contract and every failure message, the bootstrap-vs-paging decision,
  address discovery across every hostname shape, and the Delta MERGE itself —
  including that replaying a batch changes nothing, which is the property the
  watermark design rests on. The Spark tests skip themselves without a working
  JVM and run in CI, which now installs Java for them.
- The feed logic moved from the DAG into `jobs/trackers_feed.py` so it can be
  imported at all: the DAG module pulls in Airflow and builds its DAGs on
  import, so nothing in it was testable. No behaviour changed.

## 2.3.1

- Create the `lakehouse` bucket, not only `raw`. Spark writes objects but never
  creates buckets, so a MinIO without it failed inside the merge with
  `UnknownStoreException: s3a://lakehouse/...` — after the batch had already
  been fetched and archived, and with an error naming a path rather than the
  missing bucket.

## 2.3.0

- **The startup line for each tracker token now reports its length**, to be
  compared with the tracker's own `API token auth: ON — api_token is set (N
  characters)`. A 403 from a tracker means the two tokens differ, and until now
  neither side said anything that could be compared without writing a secret
  down. Two numbers in two logs settle it.
- The token and base URL are trimmed before being exported, so the reported
  length is the length actually presented — the DAG strips the token anyway, and
  a value that merely looked a character longer would send you hunting a
  difference that wasn't there.

## 2.2.0

- **The trackers are found rather than configured, and need no published host
  port.** Add-ons reach each other by hostname on the Supervisor network, and
  add-ons from one repository share a prefix, so this container being
  `<prefix>-pipeline-airflow` is enough to know the trackers are
  `<prefix>-gym-tracker` and `<prefix>-coop-tracker`. The prefix comes from our
  own hostname, so it follows the repository being re-added — the prefix is its
  hash — or a local install, with nothing to keep in sync. Outside Supervisor
  the derivation returns nothing and the configured default is used.
- Consequently the tracker API need not be exposed on the LAN at all, where an
  `api_token` would be the only thing guarding it.
- `gym_tracker_base_url` / `coop_tracker_base_url` now default to blank, meaning
  "work it out". Set one only to point somewhere else.

## 2.1.1

- **A tracker that can't be reached now says which address was tried.** The
  failure was a bare `URLError: <urlopen error [Errno 111] Connection refused>`,
  which withholds the one fact you need. It now names the URL and points at the
  cause: the tracker add-on has no host port published, or `<source>_base_url`
  names the wrong one.
- **A rejected token is told apart from an unreachable add-on.** A 401 or 403
  means the request arrived and the token was wrong — a different fix from not
  arriving at all — so it says so, rather than being lumped in with other HTTP
  errors.

## 2.1.0

- **The tracker credentials are add-on options now**: `gym_tracker_base_url`,
  `gym_tracker_api_token`, `coop_tracker_base_url`, `coop_tracker_api_token`.
  They are supplied to the DAGs as Airflow Variables through the environment
  secrets backend, which Airflow consults *before* the metastore — so they win
  over a UI Variable of the same name.

  This exists because a Variable created in the web UI kept coming back as
  missing: the execution API answered 404 for a key the UI listed quite happily.
  A key with a stray character looks identical in that list and can never be
  looked up. Options can't fail that way, and it puts the whole stack's
  configuration in one place. An option left blank is not exported at all, so it
  never shadows a Variable you set on purpose.
- A stop requested by Supervisor now exits 0. Every normal stop was logged as
  `exited with non-zero exit code 1`, which made a real crash indistinguishable
  from a restart.

## 2.0.1

- Fix the 2.0.0 build. Removing the JDK and the Spark client took the `USER
  root` line with it, so `apt-get update` ran as the `airflow` user and failed
  with `Permission denied` on `/var/lib/apt/lists/partial`. 2.0.0 could not be
  installed at all.

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
