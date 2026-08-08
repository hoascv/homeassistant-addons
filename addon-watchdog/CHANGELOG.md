# Changelog

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
