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

import watchdog

OPTIONS_PATH = "/data/options.json"

app = Flask(__name__)

# The most recent scan, replaced wholesale by the scanner thread. Assignment is
# atomic, so a request either sees the previous snapshot or the new one and
# never a half-written mixture — which is why this is a plain module global and
# not something guarded by a lock.
_snapshot = {"generated": None, "addons": [], "unhealthy": 0, "error": "no scan yet"}
_last_publish = {"pushed": 0, "errors": [], "at": None}


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
    }
    try:
        with open(OPTIONS_PATH) as handle:
            defaults.update(json.load(handle))
    except (OSError, ValueError) as exc:
        _log(f"could not read {OPTIONS_PATH} ({exc}); using defaults")
    return defaults


def summarise(snapshot, seconds, pushed=None):
    """One line per scan, so the log answers "is this thing still running" on
    its own. A scan costs a Supervisor stats call per add-on — around a second
    each — so the duration is worth carrying: it is the first thing to look at
    if scans start overlapping the interval."""
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
    return line


def scan_once(options):
    global _snapshot, _last_publish
    started = time.monotonic()
    snapshot = watchdog.collect(
        timeout=options["probe_timeout_seconds"],
        ignore_stopped=options["ignore_stopped"],
    )
    _snapshot = snapshot

    if snapshot.get("error"):
        _log(f"scan failed: {snapshot['error']}")
        return snapshot

    pushed = None
    if options["publish_sensors"]:
        pushed, errors = watchdog.publish(
            snapshot,
            prefix=options["sensor_prefix"],
            ignore_stopped=options["ignore_stopped"],
        )
        _last_publish = {"pushed": pushed, "errors": errors, "at": time.time()}
        for err in errors[:3]:
            _log(f"sensor push failed: {err}")

    _log(summarise(snapshot, time.monotonic() - started, pushed))
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


@app.route("/")
def index():
    return render_template(
        "index.html",
        snapshot=_snapshot,
        publish=_last_publish,
        mb=watchdog._mb,
    )


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

    port = int(os.environ.get("PORT", "8099"))
    from waitress import serve

    _log(f"serving on 0.0.0.0:{port} (waitress)")
    serve(app, host="0.0.0.0", port=port, threads=4)


if __name__ == "__main__":
    main()
