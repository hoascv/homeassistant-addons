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
