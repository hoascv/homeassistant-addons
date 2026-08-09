# Add-on Watchdog

One page, and one Home Assistant sensor per add-on, answering whether the other
add-ons in this repository are actually working.

The distinction it exists for: Supervisor knows whether a *container* is
running, which is not the same as whether the thing inside it works. The
Pipeline Metastore once shipped with a background thread that died on every
boot while its port stayed open and the add-on read as perfectly healthy. So
each add-on gets a **probe** as well as its Supervisor state, and the two
disagreeing is the signal:

| Status | Meaning |
|---|---|
| `ok` | Started, and its probe answered. |
| `degraded` | **Started, but its service is not answering.** The one worth alerting on. |
| `running` | Started, but nothing can be probed from outside (see below). |
| `stopped` | Not started. Normal for most of the pipeline, which is `boot: manual`. |
| `not installed` | Known to this repository, absent from this machine. |

## What it checks

Per add-on, from the Supervisor API: state, CPU, memory, installed version, and
whether an update is available. Then a probe, over the add-on's own hostname on
its **container** port — not a published host port, so an ingress-only add-on
that publishes nothing is still reachable:

| Add-on | Probe |
|---|---|
| Gym Tracker / Coop Tracker | HTTP `:8099/` — the Flask app answers |
| Pipeline Postgres | TCP `:5432` — accepting connections |
| Pipeline MinIO | HTTP `:9000/minio/health/live` |
| Pipeline Spark | HTTP `:8080/` — the master UI answers |
| Pipeline Metastore | TCP `:9083` — the Thrift port is open |
| Pipeline Airflow | HTTP `:8080/api/v2/monitor/health`, reporting each component |
| Pipeline Notebook | **none** — JupyterLab binds loopback and nginx listens on the ingress port only, so there is nothing a sibling add-on can reach. It reports `running`, never `ok`, because claiming a check that never happened would be worse than admitting there isn't one. |

An HTTP probe treats any reply below 500 as alive. A 302 to a login page or a
401 both mean something is listening and speaking HTTP, which is the question
being asked; failing them would report healthy services as broken.

## Record counts

The two trackers publish `/api/stats` — row counts per tracked table, plus the
database size — and the dashboard shows the total in a **Records** column, with
the per-table breakdown on hover. The same numbers arrive as `records`,
`record_counts` and `db_size_mb` attributes on that add-on's sensor, so you can
graph how the data grows:

```yaml
sensor:
  - platform: template
    sensors:
      workouts_logged:
        value_template: >
          {{ state_attr('sensor.addon_watchdog_gym_tracker', 'record_counts')['workout_logs'] }}
```

Two deliberate limits:

- **The pipeline's Postgres is not counted.** Counting rows there means the
  watchdog holding database credentials, which is a poor trade for a number —
  and it is the last add-on that should be holding them.
- **A degraded add-on is not asked.** It will not answer, and waiting for a
  second timeout would slow every scan.

Counts require Gym Tracker 1.29.0 / Coop Tracker 1.41.0 or later. An older one
returns 404 and simply shows no number; health is judged by the probe, never by
this.

### If an add-on sets `restrict_to_user_ids`

That setting refuses any caller without a matching ingress-user header, and the
watchdog is not a browser session — so it gets **HTTP 403** and the Records
column stays blank. The add-on still reads `ok`, because a 403 does prove
something is alive and enforcing, and the probe detail says what to do.

The documented way past it is that add-on's own `api_token`. Copy it into
`api_tokens`:

```yaml
api_tokens:
  - slug: gym-tracker
    token: "<the api_token from the Gym Tracker add-on>"
  - slug: coop-tracker
    token: "<the api_token from the Coop Tracker add-on>"
```

`slug` is the short slug as it appears in this repository — `gym-tracker`, not
`6753e04e_gym_tracker`. The token is sent as `Authorization: Bearer …` on both
the probe and the stats call. Leave the list empty if no add-on restricts
access; nothing else needs it.

## What a probe cannot ask

Some failures leave every port answering. A standby that has silently stopped
replaying still accepts connections and still serves stale data. Backups that
stopped running three weeks ago leave no trace on any port at all. A probe is
blind to both.

So the add-ons that have something to say report it themselves, into
`/share/pipeline-status/<slug>.json`, which this add-on reads (mapped read-only).
The alternative — giving the watchdog a database login so it could ask — is the
thing this add-on deliberately does not do, and the same split as the trackers'
`/api/stats`: each add-on knows its own state best.

| Add-on | Reports |
|---|---|
| Pipeline Postgres 1.3.0+ | whether archiving works, and the last backup's type and time |
| Pipeline Postgres Replica 1.1.0+ | whether it is still in recovery, and how many seconds behind |

A **failing report degrades the add-on**, even when its port answers. Postgres
with a broken backup is a healthy service that is not doing its job, and that is
worth an alert; the detail line on the dashboard and the `report` sensor
attribute say which of the two is wrong.

A report that has not been updated in an hour counts as failing. The producers
refresh every minute (the replica) or after every backup (Postgres), so a report
that stopped moving means the writer stopped — which is the news, not a gap in
it.

Attributes land on that add-on's sensor flattened, so a template can read them:

```yaml
# alert if the replica falls more than five minutes behind
{{ state_attr('sensor.addon_watchdog_pipeline_postgres_replica', 'report_lag_seconds') | int > 300 }}

# or if backups stopped
{{ not state_attr('sensor.addon_watchdog_pipeline_postgres', 'report_ok') }}
```

## Uptime and restarts

The dashboard shows how long each add-on has been up, and how many times it has
restarted while the watchdog was watching. Supervisor exposes neither a
container start time nor a restart counter, so both are derived here — which
has two consequences worth understanding.

**Uptime is a lower bound until a restart is seen.** An add-on already running
when the watchdog first looked has a real start time older than anything
observable, so it shows as **`≥3d`**. Once a restart is observed the clock
becomes exact and the `≥` disappears. The `uptime_known` sensor attribute says
which it is.

**Restarts are detected two ways.** A transition into `started` is the obvious
one. The other is the container's cumulative network counter going *backwards*
while it stayed `started` — those counters reset with the container, so a drop
means it was replaced entirely between two scans. Without that, an add-on that
died and came back inside one 60-second interval would be invisible.

Both are remembered in `/data/watchdog-state.json`, so they survive the
watchdog's own restart.

## Why an add-on restarted

Hover the restart count for the reason. It comes from Supervisor's log, which
is the only place it exists — nothing in the Supervisor API exposes an exit code
or a restart cause. Typical lines:

```
ERROR [supervisor.apps.app] App Pipeline Spark exited with non-zero exit code 137
```

Exit **137** is a SIGKILL, and most often means Supervisor stopped the add-on
and it did not exit within ten seconds — normal during an update. It can also
mean the container was killed for running out of memory, which is not normal;
the timing tells you which.

`supervisor_watchdog` on each sensor reports whether **Home Assistant's own**
watchdog is enabled for that add-on. That is the setting that restarts an
add-on when it stops unexpectedly, and it is the usual source of a restart
nobody asked for.

Reading Supervisor's log may be refused to this add-on's role. If it is,
restarts are still counted and only the explanation is missing — the error is
reported rather than passed off as "no restarts".

## Disk I/O — proving the storage is the constraint

When something "gets slow at certain times of day", the useful question is
whether the storage was the limit or the workload grew. Those need different
evidence, and a maximum alone answers neither: it says what the device could do,
not that you were at it.

The argument this add-on lets you make:

> Device utilisation pinned at 100% and average write wait rose from 3 ms to
> 48 ms, while the pipeline add-ons' own I/O rates were unchanged. The extra
> load came from somewhere else.

**Continuously**, sampled every `io_sample_seconds` (default 10) from
`/proc/diskstats`, which is host-wide and readable without any privilege:

| | |
|---|---|
| **Utilisation %** | share of wall-clock time with at least one request in flight. Near 100% means saturated |
| **Read / write wait** | average milliseconds per operation. This is what rises when the device is the bottleneck |
| **IOPS and MB/s** | how much work was actually asked for |

Both the **mean and the peak** of each minute are published, because a 60-second
mean hides a 10-second stall — and the stall is the thing being hunted.

**Per add-on**, from Supervisor's own `blk_read`/`blk_write`: each add-on's
sensor carries `disk_read_bps` and `disk_write_bps`. This is what shows the
pipeline's demand did *not* rise while the device was saturated.

### The ceiling

Press **Measure ceiling** on the dashboard to run `fio` once: 8 KiB random read
and write, `direct=1` so it measures the disk rather than the page cache. 8 KiB
because that is Postgres' page size, which makes the number directly comparable
to database I/O rather than a sequential MB/s figure nothing here is limited by.

It is **never scheduled**. It writes real data and competes with the live
database for the duration, so it stays a deliberate act. It refuses to start
unless 2 GB is free, and deletes its test file even when it fails.

The result is a **floor** on the true maximum, not the maximum: it was measured
while everything else was running. That is stated on the dashboard, because a
ceiling quoted without that caveat invites an argument about the measurement.

### Alerting on it

```yaml
automation:
  - alias: "Storage is the bottleneck"
    trigger:
      - platform: numeric_state
        entity_id: sensor.addon_watchdog_disk_write_latency_ms
        above: 20
        for: "00:05:00"
    condition:
      - condition: numeric_state
        entity_id: sensor.addon_watchdog_disk_util
        above: 90
    action: ...
```

Latency *and* utilisation together: high latency alone can mean a slow device,
but high latency while pinned at 100% busy means saturation.

## Sensors

With `publish_sensors` on (the default), each add-on gets
`sensor.addon_watchdog_<slug>` whose state is the word from the table above,
with version, update-available, CPU, memory and the probe detail as attributes.

There is also `sensor.addon_watchdog_unhealthy`, whose state is a **count** —
`> 0` is the entire condition an automation needs, and it keeps working when you
add add-ons later:

```yaml
automation:
  - alias: "An add-on is unhealthy"
    trigger:
      - platform: numeric_state
        entity_id: sensor.addon_watchdog_unhealthy
        above: 0
        for: "00:05:00"
    action:
      - service: notify.persistent_notification
        data:
          message: >
            Degraded: {{ state_attr('sensor.addon_watchdog_unhealthy', 'degraded') | join(', ') }}
```

The `for:` matters — a restarting add-on is briefly degraded, and without it
every update you install pages you.

## Configuration

- **scan_interval_seconds** (default 60): how often to scan, measured from the
  start of one scan to the start of the next. A scan is not instant — Supervisor
  takes about a second to compute each add-on's stats, so a full pass over this
  repository runs around 12 seconds — and the log says so if a scan ever outruns
  its interval.
- **probe_timeout_seconds** (default 5): per probe. A hung service should read
  as degraded rather than stall the scan.
- **publish_sensors** (default true): turn off to use the page only.
- **sensor_prefix** (default `addon_watchdog`): the entity-id prefix.
- **ignore_stopped** (default true): whether a stopped add-on counts as
  unhealthy. Most of the pipeline is `boot: manual` and is stopped on purpose;
  counting that would leave the summary permanently non-zero and therefore
  ignored. Turn it on if you expect everything to be running.

## Permissions

`hassio_role: manager`, because listing other add-ons and reading their stats
needs it — there is no narrower role that can see past this add-on itself. The
add-on only ever reads: it calls `GET /addons`, `GET /addons/<slug>/info` and
`GET /addons/<slug>/stats`, and the single write it makes is pushing its own
sensors to Home Assistant. It cannot start, stop, or update anything, and there
is no option that would make it try.

## Endpoints

- `/` — the dashboard, refreshing every 60 seconds.
- `/api/status` — the same snapshot as JSON, with a fixed schema.
- `/api/health` — the watchdog's own health, since nothing else is watching it:
  when it last scanned and how long ago.

## When something can't be retrieved

Anything the watchdog fails to fetch is logged with its reason — Supervisor
info, Supervisor stats, record counts, and sensor pushes each reported
separately, named by add-on:

```
could not retrieve gym-tracker record counts: HTTP 403 (needs this add-on's api_token in api_tokens)
could not retrieve pipeline-spark supervisor stats: HTTP 502: bad gateway
recovered: gym-tracker record counts
```

Reported **on change only**. A scan a minute means an unchanging failure logged
every time would bury everything else within a day, so it appears when it
starts, again if the reason changes, and once more when it clears. The per-scan
summary keeps carrying the count (`3 retrieval error(s)`), so a persistent
problem stays visible without repeating itself.

None of this affects health. A failed retrieval is a missing number, not a sick
add-on — that judgement belongs to the probe.

## Reading the log

Every line is stamped in local time, so it lines up with the Supervisor log.
One line is written per scan whether or not anything is wrong — a watchdog that
goes quiet when healthy cannot be told apart from one that has wedged:

```
2026-08-08 10:44:08 [Add-on Watchdog] scanned in 12.3s: 1 degraded, 1 not installed,
  1 ok, 1 running, 1 stopped | degraded: pipeline-postgres | 1 update(s) available | 4 sensors
```

## Notes

- Scanning happens on a timer in a background thread, not when the page is
  opened — the sensors have to keep updating whether or not anyone is looking.
- Only the add-ons in this repository are listed, deliberately. An add-on from
  somewhere else never appears, even when Supervisor reports it.
- A failed scan leaves the previous snapshot on screen and shows the error,
  rather than blanking the page.
