"""The watchdog's own service: a scan loop, a dashboard, and a JSON endpoint.

Scanning happens on a timer in a background thread rather than when the page is
opened. Two reasons: the sensors have to keep updating whether or not anyone is
looking, and a page load that fans out to eight add-ons would take as long as
the slowest probe.
"""
import json
import os
import threading
import time

from flask import Flask, jsonify, render_template

import diskio
import watchdog

OPTIONS_PATH = "/data/options.json"

app = Flask(__name__)

# The most recent scan, replaced wholesale by the scanner thread. Assignment is
# atomic, so a request either sees the previous snapshot or the new one and
# never a half-written mixture — which is why this is a plain module global and
# not something guarded by a lock.
_snapshot = {"generated": None, "addons": [], "unhealthy": 0, "error": "no scan yet"}
_last_publish = {"pushed": 0, "errors": [], "at": None}

# Device I/O is sampled on its own short interval rather than with the scan: a
# scan takes ~13s and runs once a minute, which would average away exactly the
# short stalls this is meant to catch. Reading one file is cheap enough to do
# every few seconds.
_sampler = None
_io = {"summary": None, "benchmark": None, "saturation": None, "error": None}
_benchmark_running = False


def _log(message):
    """Local time, because the reader is comparing this against the Supervisor
    log next to it — and Supervisor stamps local time. TZ comes from the
    Supervisor environment, so this follows the Home Assistant setting."""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    print(f"{stamp} [Add-on Watchdog] {message}", flush=True)


def load_options():
    defaults = {
        "scan_interval_seconds": 60,
        "probe_timeout_seconds": 5,
        "publish_sensors": True,
        "sensor_prefix": "addon_watchdog",
        "ignore_stopped": True,
        "api_tokens": [],
        "io_sample_seconds": 10,
        "benchmark_size_mb": 1024,
        "benchmark_seconds": 30,
        "benchmark_min_free_gb": 2,
    }
    try:
        with open(OPTIONS_PATH) as handle:
            defaults.update(json.load(handle))
    except (OSError, ValueError) as exc:
        _log(f"could not read {OPTIONS_PATH} ({exc}); using defaults")
    # Flattened once here rather than per scan. Entries without both fields are
    # dropped quietly: a half-filled row in the add-on config UI is a common
    # in-progress state, not something to log about every minute.
    defaults["tokens"] = {
        entry["slug"]: entry["token"]
        for entry in defaults.get("api_tokens") or []
        if isinstance(entry, dict) and entry.get("slug") and entry.get("token")
    }
    return defaults


# Last error logged per source, so a failure that persists is reported once
# rather than every scan. At a scan a minute, logging a stuck 403 unconditionally
# would bury everything else within a day — but staying silent about it is how
# the empty Records column went unexplained in 1.2.0. Logging transitions is the
# middle: it appears when it starts, and again when it clears.
_reported_errors = {}


def log_errors(snapshot, publish_errors=()):
    """Report what could not be retrieved, on change only."""
    current = {}
    for row in snapshot.get("addons", []):
        for field, what in (
            ("error", "supervisor info"),
            ("stats_error", "supervisor stats"),
            ("records_error", "record counts"),
        ):
            if row.get(field):
                current[f"{row['slug']} {what}"] = row[field]
    for err in publish_errors:
        # publish() formats these as "<entity>: <reason>". Splitting them keeps
        # the entity in the key — stable, so a flapping reason is what triggers
        # a re-log — and stops the line repeating the entity id twice.
        entity, _, reason = err.partition(": ")
        current[f"sensor push {entity}"] = reason or err
    if snapshot.get("error"):
        current["supervisor"] = snapshot["error"]

    for key, err in sorted(current.items()):
        if _reported_errors.get(key) != err:
            _log(f"could not retrieve {key}: {err}")
    for key in sorted(_reported_errors):
        if key not in current:
            _log(f"recovered: {key}")

    _reported_errors.clear()
    _reported_errors.update(current)
    return len(current)


def summarise(snapshot, seconds, pushed=None, failures=0):
    """One line per scan, so the log answers "is this thing still running" on
    its own. A scan costs a Supervisor stats call per add-on — around a second
    each — so the duration is worth carrying: it is the first thing to look at
    if scans start overlapping the interval.

    The failure count is carried every scan even though the detail is only
    logged on change: without it, a persistent problem would vanish from the
    log entirely after its first appearance."""
    counts = {}
    for row in snapshot["addons"]:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    parts = [f"{n} {status}" for status, n in sorted(counts.items())]
    line = f"scanned in {seconds:.1f}s: " + ", ".join(parts)

    degraded = [r["slug"] for r in snapshot["addons"] if r.get("status") == "degraded"]
    if degraded:
        line += f" | degraded: {', '.join(degraded)}"
    if snapshot.get("updates"):
        line += f" | {snapshot['updates']} update(s) available"
    if pushed is not None:
        line += f" | {pushed} sensors"
    if failures:
        line += f" | {failures} retrieval error(s)"
    io = _io.get("summary")
    if io and io.get("util_percent_peak") is not None:
        line += (f" | disk {io['util_percent_peak']}% peak busy"
                 f", {io.get('write_latency_ms_peak') or 0}ms write wait")
    return line


def scan_once(options):
    global _snapshot, _last_publish
    started = time.monotonic()
    snapshot = watchdog.collect(
        timeout=options["probe_timeout_seconds"],
        ignore_stopped=options["ignore_stopped"],
        tokens=options["tokens"],
    )
    _snapshot = snapshot

    if snapshot.get("error"):
        log_errors(snapshot)
        return snapshot

    # Take the window now so the dashboard and the sensors describe the same
    # interval as the scan line in the log.
    if _sampler:
        _io["summary"] = _sampler.summary()

    pushed, errors = None, []
    if options["publish_sensors"]:
        io_pushed, io_errors = publish_io(options["sensor_prefix"])
        pushed, errors = watchdog.publish(
            snapshot,
            prefix=options["sensor_prefix"],
            ignore_stopped=options["ignore_stopped"],
        )
        pushed += io_pushed
        errors += io_errors
        _last_publish = {"pushed": pushed, "errors": errors, "at": time.time()}

    failures = log_errors(snapshot, errors)
    _log(summarise(snapshot, time.monotonic() - started, pushed, failures))
    return snapshot


def scanner(options):
    interval = options["scan_interval_seconds"]
    while True:
        started = time.monotonic()
        try:
            scan_once(options)
        except Exception as exc:  # noqa: BLE001 - the loop outlives any one scan
            _log(f"scan raised {type(exc).__name__}: {exc}")
        # Sleep the remainder rather than the whole interval: a scan takes a
        # second per add-on for its Supervisor stats call, so sleeping the full
        # interval on top would make a 60s setting mean 72s and drift further
        # with every add-on added.
        elapsed = time.monotonic() - started
        if elapsed >= interval:
            _log(f"scan took {elapsed:.1f}s, longer than the {interval}s interval")
        time.sleep(max(1.0, interval - elapsed))


def io_sampler(options):
    """Poll the device counters on a short interval, forever."""
    global _sampler
    _sampler = diskio.Sampler(interval=options["io_sample_seconds"])
    _io["benchmark"] = diskio.load_benchmark()
    while True:
        try:
            _sampler.poll()
            if _sampler.error and _io.get("error") != _sampler.error:
                _log(f"disk sampling: {_sampler.error}")
            _io["error"] = _sampler.error
        except Exception as exc:  # noqa: BLE001 - the loop outlives one sample
            _log(f"disk sample raised {type(exc).__name__}: {exc}")
        time.sleep(options["io_sample_seconds"])


def publish_io(prefix):
    """Device sensors, which is the whole point: Home Assistant's recorder keeps
    the history, so the slow window is captured whether or not anyone is
    watching, and a threshold alert can fire on it.

    state_class measurement is what makes the recorder keep long-term
    statistics rather than only recent states.
    """
    summary = _io.get("summary")
    if not summary:
        return 0, []

    _io["saturation"] = diskio.utilisation_against_ceiling(summary, _io.get("benchmark"))
    common = {"device": summary.get("device"), "samples": summary.get("samples")}
    sensors = [
        ("disk_util", summary.get("util_percent_peak"), "%", "Disk busy (peak)",
         "mdi:harddisk", {"mean_percent": summary.get("util_percent"),
                          "saturation_vs_benchmark_percent": _io["saturation"]}),
        ("disk_iops", summary.get("iops_peak"), "IOPS", "Disk IOPS (peak)",
         "mdi:speedometer", {"mean_iops": summary.get("iops")}),
        ("disk_read_latency_ms", summary.get("read_latency_ms_peak"), "ms",
         "Disk read latency (peak)", "mdi:timer-outline",
         {"mean_ms": summary.get("read_latency_ms")}),
        ("disk_write_latency_ms", summary.get("write_latency_ms_peak"), "ms",
         "Disk write latency (peak)", "mdi:timer-outline",
         {"mean_ms": summary.get("write_latency_ms")}),
    ]

    pushed, errors = 0, []
    for name, value, unit, label, icon, extra in sensors:
        if value is None:
            continue
        entity = f"sensor.{prefix}_{name}"
        _, err = watchdog.push_sensor(entity, str(value), {
            "friendly_name": label, "icon": icon,
            "unit_of_measurement": unit, "state_class": "measurement",
            **common, **extra,
        })
        if err:
            errors.append(f"{entity}: {err}")
        else:
            pushed += 1
    return pushed, errors


def start_benchmark(options):
    """Run fio once, in the background. Never scheduled: it writes real data and
    competes with the live database, so it stays a deliberate act."""
    global _benchmark_running
    if _benchmark_running:
        return False, "a benchmark is already running"
    _benchmark_running = True

    def _run():
        global _benchmark_running
        try:
            _log("benchmark: starting (this competes with everything else on the disk)")
            result, err = diskio.run_benchmark(
                size_mb=options["benchmark_size_mb"],
                seconds=options["benchmark_seconds"],
                min_free_gb=options["benchmark_min_free_gb"],
            )
            if err:
                _log(f"benchmark failed: {err}")
                _io["benchmark_error"] = err
            else:
                _io["benchmark"] = result
                _io["benchmark_error"] = None
                _log(f"benchmark: randread {result['randread']['iops']} IOPS, "
                     f"randwrite {result['randwrite']['iops']} IOPS")
        finally:
            _benchmark_running = False

    threading.Thread(target=_run, daemon=True).start()
    return True, None


def uptime(seconds):
    """Compact and rounded — nobody reading a status page wants 4 days, 3 hours,
    17 minutes and 4 seconds."""
    if seconds is None:
        return "—"
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 172800:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


@app.route("/")
def index():
    return render_template(
        "index.html",
        snapshot=_snapshot,
        publish=_last_publish,
        mb=watchdog._mb,
        uptime=uptime,
        io=_io,
        benchmark_running=_benchmark_running,
    )


@app.route("/api/benchmark", methods=["POST"])
def api_benchmark():
    """Measure the device ceiling, on demand only."""
    started, err = start_benchmark(load_options())
    return jsonify({"started": started, "error": err}), (202 if started else 409)


@app.route("/api/io")
def api_io():
    """Device I/O as the sensors see it, plus the ceiling if one was measured."""
    return jsonify({
        "summary": _io.get("summary"),
        "benchmark": _io.get("benchmark"),
        "benchmark_error": _io.get("benchmark_error"),
        "saturation_percent": _io.get("saturation"),
        "running": _benchmark_running,
        "error": _io.get("error"),
    })


@app.route("/api/status")
def api_status():
    """The same snapshot the page renders — for anything that would rather not
    scrape HTML."""
    return jsonify(_snapshot)


@app.route("/api/health")
def api_health():
    """The watchdog's own health, which nothing else is watching."""
    generated = _snapshot.get("generated")
    return jsonify(
        {
            "ok": generated is not None and _snapshot.get("error") is None,
            "last_scan": generated,
            "age_seconds": None if generated is None else round(time.time() - generated, 1),
            "sensors_pushed": _last_publish.get("pushed"),
        }
    )


def main():
    options = load_options()
    _log(
        f"scanning every {options['scan_interval_seconds']}s, "
        f"probe timeout {options['probe_timeout_seconds']}s, "
        f"sensors {'on' if options['publish_sensors'] else 'off'}"
    )
    if not watchdog.SUPERVISOR_TOKEN:
        _log("SUPERVISOR_TOKEN not set — nothing can be read; is hassio_api on?")

    threading.Thread(target=scanner, args=(options,), daemon=True).start()
    threading.Thread(target=io_sampler, args=(options,), daemon=True).start()

    port = int(os.environ.get("PORT", "8099"))
    from waitress import serve

    _log(f"serving on 0.0.0.0:{port} (waitress)")
    serve(app, host="0.0.0.0", port=port, threads=4)


if __name__ == "__main__":
    main()
