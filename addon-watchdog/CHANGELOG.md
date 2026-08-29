# Changelog

## 1.14.2

- **The Journal add-on was invisible here.** Same gap as Knowledge in 1.14.1,
  and caught by the same test: Journal shipped without a `PROBES` entry, so it
  did not appear on the dashboard and nothing reported on it. Probed on `/`.
- That probe expects a **401** and treats it as healthy. Journal is ingress-only
  and refuses any request without Home Assistant's ingress user header, so a 401
  is what a live Journal answers — `probe_http` already counts anything under
  500 as alive. The watchdog gets no further in, and holds no credential that
  would let it: the question is whether the service is up, not what it contains.
- No `/api/stats` entry, deliberately. Journal publishes a streak sensor with
  counts and dates but never content, and there is no records endpoint for a
  watchdog to read.
- **A malformed `fio` result no longer escapes its own error handler.**
  `run_benchmark` catches "fio produced no usable result" for exactly this, but
  an output with `jobs` present and empty raises `IndexError`, which was not in
  the caught set — so it surfaced from the background thread as
  `benchmark raised IndexError` instead. Found by the new benchmark tests.
- Also added `journal` to the CI test matrix. Its suite had never run in CI —
  the same drift 1.14.1 corrected for two other add-ons.
- Tests: coverage of this add-on goes from 71% to 93%. `probe_http`, `publish`,
  the Supervisor call layer, `fetch_stats`, the scan loop, the I/O sampler and
  the benchmark had no tests at all; the first two are what every sensor rests
  on, and `publish` is the only part nothing else would have caught.

## 1.14.1

- **The Knowledge add-on was invisible here.** It shipped without a `PROBES`
  entry, so it did not appear on the dashboard and nothing reported on it —
  exactly the gap the "every add-on has a probe decision" test exists to catch,
  and which it duly caught. Probed on `/` like the other trackers, with
  `/api/stats` read for its record counts.
- Also added `electricity-tracker` and `knowledge` to the CI test matrix. Their
  suites had never run in CI: the matrix listed five add-ons and had not grown
  with the repository, so two add-ons' tests only ever ran on somebody's laptop.

## 1.14.0

- Monitors the new **Electricity Tracker** add-on: probe and `/api/stats`
  record counts, same wiring as Gym Tracker, Coop Tracker and Detection Hub.

## 1.13.0

- Monitors the new **Network Traffic Monitor** add-on: probe and its
  `/share/pipeline-status` report.
- Probed at `/api/health` rather than `/`, same reasoning as Detection Hub:
  its dashboard answers fine with a dead `tcpdump` process behind it, and
  that endpoint returns 503 for exactly that case.

## 1.12.0

- **The version column says when that version was installed** — `2h ago` under
  the number. Supervisor exposes no install date, so this is observed: the scan
  that first sees a version different from the recorded one is the install.
- **Restarts are now counted for the running version only.** They read
  `4 restarts on 1.44.2` and reset on update. An unqualified count spanning
  versions answers the wrong question: Detection Hub showed *13 restarts* beside
  an hour of uptime, nearly all of them belonging to builds that are no longer
  installed, when what the number is read for is whether this build is stable.
- The update's own restart is not counted against the new version. Supervisor
  restarts an add-on to install it — that is the install, not a fault of what
  was installed.
- A version change now also counts as a restart for the **uptime** clock. The
  container is replaced, so its network counter resets, but a busy minute can
  leave the new counter above the old one and hide the drop that would otherwise
  be the only evidence.
- Both ages carry the same `≥` as uptime when the watchdog did not observe the
  event itself. Upgrading records every add-on's version for the first time, so
  each row starts with a zeroed count and a `≥` on its version age.
- New sensor attributes: `version_installed_at`, `version_age_seconds`,
  `version_installed_known`. `restarts` keeps its name and changes meaning — an
  alert on it now fires for a flapping build rather than a much-updated add-on.

## 1.11.0

- Monitors the new **Detection Hub** add-on: probe, status, record counts and
  its `/share/pipeline-status` report.
- Probes it at `/api/health` rather than `/`. Its page answers perfectly well
  with a broken model or a dead capture thread, and any status below 500 counts
  as alive here — so probing the page would have reported a detector that
  detects nothing as healthy. `/api/health` returns 503 for exactly that case.

## 1.10.3

- Keeps up with Gym Tracker 1.32.0 / Coop Tracker 1.44.0, which now refuse any
  request that did not arrive through ingress unless it carries their
  `api_token`. The watchdog meets **401** where it used to meet 200, so the
  "needs this add-on's api_token in `api_tokens`" hint — previously attached to
  403 alone — now fires for 401 too. Without this the Records column would go
  blank with no explanation, which is the exact failure that hint exists to
  prevent.
- Health is unaffected: a 401 still proves something is alive and enforcing.

## 1.10.2

- Documentation fixes. **`ignore_stopped` was described backwards**: the default
  `true` means a stopped add-on does *not* count as unhealthy, so the advice to
  "turn it on if you expect everything to be running" said the opposite of what
  it meant — it should be turned *off*.
- The failed-scan note claimed the previous snapshot stays on screen. When
  Supervisor is unreachable the scan returns an empty list and the table does
  blank; only an unexpected exception preserves the last good snapshot. Both
  paths are now described.
- Documented what was already shipping but unwritten: Pipeline Postgres Replica
  in the probe table, the `benchmark_size_mb` / `benchmark_seconds` /
  `benchmark_min_free_gb` options (and that the free-space guard scales with the
  file size, so the "2 GB" figure is only the default), `GET /api/io` and
  `POST /api/benchmark`, the `disk_iops` and `disk_read_latency_ms` sensors,
  that the disk sensors' state is the peak and the mean is an attribute, the
  `other_counts` attribute, and the `GET /supervisor/logs` call in the
  permissions list.
- Said plainly that `not installed` is a dashboard state and never a sensor
  state, and corrected a `config.yaml` comment that claimed sensor publishing is
  off by default three lines above `publish_sensors: true`.

## 1.10.1

- The **Measure ceiling** button no longer navigates away. It was a plain HTML
  form, and a form submit takes the browser to the response — so clicking it
  dumped the reader on a raw `{"error":null,"started":true}` page with only the
  back button to escape. It now POSTs in place, reports a refusal (too little
  free space, already running) where it happened, and reloads once fio has had
  time to finish.
- A benchmark that cannot even check free space — an unreadable or missing data
  directory — is now a reported refusal rather than an exception on a background
  thread, which surfaced only as stderr noise and left a button that appeared to
  do nothing.

## 1.10.0

- **Device I/O, continuously**: utilisation, read/write latency, IOPS and MB/s
  sampled every 10 seconds from `/proc/diskstats`, published as sensors with
  `state_class: measurement` so Home Assistant's recorder keeps the history.
  A slow window is then captured whether or not anyone was watching, which is
  the point — the symptom happens during working hours and is gone by the time
  anyone looks.
- Both mean and **peak** per minute: a 60-second mean averages away the
  10-second stall that is the whole event.
- **Per-add-on disk rates** from Supervisor's `blk_read`/`blk_write`, as sensor
  attributes. Device saturated while these stay flat is the difference between
  "the storage was the limit" and "the pipeline asked for more".
- **Measure ceiling** button runs `fio` once — 8 KiB random, `direct=1`, the
  size Postgres reads and writes in. Never scheduled, refuses below 2 GB free,
  and cleans up its test file even on failure. The result is labelled a floor on
  the true maximum, since it is measured under live load.
- The scan line now carries peak busy and write wait, so the log alone shows a
  slow window.
- Corrected two comments claiming this host is i386; it is amd64.

## 1.9.0

- Uptime per add-on, and how many times it has restarted. Supervisor exposes
  neither a start time nor a restart counter, so both are derived from
  successive observations and kept in `/data/watchdog-state.json`. Uptime shows
  as `≥3d` until a restart is actually observed, because an add-on already
  running when the watchdog first looked started before anything it can see.
- Restarts that happen entirely between two scans are caught by the container's
  cumulative network counter going backwards — those reset with the container,
  and a minute is long enough for an add-on to die and come back unseen.
- The reason, on hover: Supervisor's log is the only place an exit code or a
  restart cause exists, so it is read and matched by add-on display name. If
  that endpoint is refused to this role, restarts are still counted and the
  error is reported rather than passed off as "no restarts".
- `supervisor_watchdog` reports whether Home Assistant's own watchdog is enabled
  for each add-on — the usual source of a restart nobody asked for.

## 1.8.0

- Reports what a probe cannot ask: whether Postgres' backups are actually
  running, and whether the replica is still replaying. Both add-ons write a
  small status file under `/share/pipeline-status`, which this add-on now reads
  (mapped read-only) — rather than being given a database login, which it still
  does not have.
- A failing report degrades the add-on even when its port answers, since a
  healthy service that has stopped doing its job is exactly the case worth
  alerting on. A report older than an hour counts as failing: the writer having
  stopped is the news.
- The report and its metrics arrive as flattened sensor attributes
  (`report_lag_seconds`, `report_archiving_ok`, …) so a template can alert on
  them directly.

## 1.7.0

- Reports the new Pipeline Postgres Replica add-on, probing its standby port.
- The "every add-on has a probe" test now reads the sibling add-on directories
  instead of comparing `PROBES` against a list derived from `PROBES` — the old
  assertion could not fail, so a new add-on could have been added to the
  repository and silently never appear on the dashboard.

## 1.6.0

- The Records column now counts every table in a tracker's database, and the
  hover splits it into `tracked` and `other`. Coop Tracker previously read as
  75 records in a 10.5 MB file, which invited exactly the wrong conclusion; the
  image tables it was omitting are now visible and can be watched as they grow.
- A tracker without the split (before Gym Tracker 1.31.0 / Coop Tracker 1.43.0)
  still reports its tracked total rather than nothing.

## 1.5.0

- Anything that cannot be retrieved is now logged with its reason. Supervisor
  info, Supervisor stats, record counts and sensor pushes are reported
  separately and named by add-on; several of these were previously swallowed,
  including the 403 that left the Records column blank with no explanation
  anywhere.
- Reported on change only — a scan a minute would otherwise bury the log in one
  unchanging error — with recoveries logged too, and a running count carried on
  the per-scan summary line so a persistent problem stays visible.
- `/api/status` gained `stats_error` and `records_error` per add-on. None of it
  affects health: a failed retrieval is a missing number, not a sick add-on.

## 1.4.0

- Dropped `armhf`, `armv7` and `i386` from `arch`, which Supervisor reports as
  deprecated. `aarch64` and `amd64` remain.

## 1.3.0

- New `api_tokens` option. An add-on with `restrict_to_user_ids` set refuses
  any caller without a matching ingress-user header, so both trackers answered
  403 and their record counts stayed blank — 1.2.0 shipped a Records column
  that could not fill in. The token is sent as `Authorization: Bearer …` on
  both the probe and the stats call.
- Until a token is configured, the probe detail says so rather than leaving an
  unexplained empty column. A 403 still reads `ok`: it proves something is
  alive and enforcing, which is what the probe asked.

## 1.2.0

- The dashboard has a Records column: how many rows each tracker holds and how
  big its database is, with the per-table breakdown on hover. The same numbers
  ride along as `records`, `record_counts` and `db_size_mb` attributes on that
  add-on's sensor.
- Counts come from the trackers' own `/api/stats` (Gym Tracker 1.29.0, Coop
  Tracker 1.41.0). An older tracker returns 404 and simply shows no number —
  health is judged by the probe, never by this.
- The pipeline's Postgres is deliberately not counted. It would mean the
  watchdog holding database credentials, which is a poor trade for a number.
- A degraded add-on is not asked for counts: it will not answer, and waiting
  for a second timeout would slow every scan.

## 1.1.0

- Every log line now carries a local timestamp, matching the Supervisor log it
  sits next to.
- One line per scan, so the log shows the watchdog is alive rather than going
  silent after three startup lines: how long the scan took, a count per status,
  any degraded add-ons by name, updates available, and sensors published.
- The scan interval now means what it says. A scan costs a Supervisor stats
  call per add-on — about a second each, so roughly 12s for this repository —
  and that was previously added *on top* of the interval rather than counted
  within it. A scan that outruns its interval says so.

## 1.0.0

- First release. An ingress dashboard and one Home Assistant sensor per add-on,
  reporting the health of the other add-ons in this repository.
- Supervisor state, CPU, memory, installed version and update-available for
  each, plus a service probe over the add-on's own hostname — so an add-on
  whose container runs while the service inside it is dead reports `degraded`
  rather than healthy.
- `sensor.addon_watchdog_unhealthy` carries a count and the list of degraded
  add-ons, for a single automation that keeps working as add-ons are added.
- Stopped add-ons are not counted as unhealthy by default: most of the pipeline
  is `boot: manual` and is stopped on purpose.
