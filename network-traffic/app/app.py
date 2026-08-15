"""The add-on's own service: a capture supervisor, an upload lifecycle loop,
and a small status dashboard.

Both loops run on their own daemon threads from main(), the same shape
addon-watchdog's scanner()/io_sampler() use: capture keeps tcpdump alive,
lifecycle turns what it produces into MinIO objects, and neither has to wait
on the other or on a page load.
"""
from __future__ import annotations

import json
import os
import threading
import time

from flask import Flask, Response, jsonify, render_template, request

import capture
import lifecycle
import status

OPTIONS_PATH = "/data/options.json"

# Ingress passes the authenticated Home Assistant user's ID in this header —
# the same convention the trackers and Detection Hub use.
INGRESS_USER_ID_HEADER = "X-Remote-User-ID"

app = Flask(__name__)

_options = {}
_capture: capture.Capture | None = None
_lifecycle: lifecycle.Lifecycle | None = None
_started_at = time.time()


def _log(message):
    """Local time, so this lines up with the Supervisor log next to it."""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    print(f"{stamp} [Network Traffic Monitor] {message}", flush=True)


def load_options():
    defaults = {
        "capture_interfaces": "any",
        "bpf_filter": "",
        "rotate_seconds": 300,
        "retention_files": 12,
        "snap_length": 0,
        "minio_endpoint": "http://172.30.32.1:9000",
        "minio_access_key": "minioadmin",
        "minio_secret_key": "changeme",
        "minio_bucket": "raw",
        "minio_prefix": "network_traffic",
        "datalake_retention_days": 7,
        "capture_label": "",
        "restrict_to_user_ids": "",
    }
    try:
        with open(OPTIONS_PATH) as handle:
            defaults.update(json.load(handle))
    except (OSError, ValueError) as exc:
        _log(f"could not read {OPTIONS_PATH} ({exc}); using defaults")
    return defaults


def get_allowed_user_ids():
    raw = (_options or {}).get("restrict_to_user_ids", "") or ""
    return {uid.strip() for uid in raw.replace("\n", ",").replace(" ", ",").split(",") if uid.strip()}


def _access_denied_html(user_id):
    shown = user_id or "unknown"
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Access restricted</title></head><body>"
        "<h1>Access restricted</h1>"
        "<p>This Network Traffic Monitor is limited to specific Home Assistant "
        "users. Your account isn't on the list.</p>"
        f"<p>Your user ID is:<br><code>{shown}</code></p>"
        "<p>Ask whoever set up the add-on to add this ID to "
        "<strong>restrict_to_user_ids</strong> on the add-on's Configuration tab.</p>"
        "</body></html>"
    )


@app.before_request
def _enforce_access():
    """Only one door: ingress. Unlike the trackers, nothing publishes a host
    port here — every byte this add-on produces goes straight to MinIO, not
    out over an API — so there is no api_token to check against. A request
    without Home Assistant's ingress header did not come through the
    Supervisor's proxy at all; refusing it is what keeps another add-on on
    the same Docker network from reading capture status just because nothing
    publishes a host port here to make that harder.

    A missing header still gets a plain 401 rather than a redirect, which is
    enough for the Add-on Watchdog's probe to count this as alive without
    needing any credential of its own — any status under 500 does.
    """
    user_id = request.headers.get(INGRESS_USER_ID_HEADER)
    if not user_id:
        return Response(
            json.dumps({"error": "unauthorized", "detail": "requires Home Assistant ingress"}),
            status=401,
            mimetype="application/json",
        )
    allowed = get_allowed_user_ids()
    if not allowed or user_id in allowed:
        return None
    return Response(_access_denied_html(user_id), status=403, mimetype="text/html")


def _report_status():
    """Written on the same interval as the lifecycle loop's own poll, so a
    dead capture process or a growing upload backlog reaches the Add-on
    Watchdog within one cycle — the page answering fine is not evidence
    tcpdump is still running, the exact gap this convention exists to close.
    """
    cap = _capture.status() if _capture else {}
    life = _lifecycle.status() if _lifecycle else {}
    ok = bool(cap.get("running"))
    details = []
    if not ok:
        details.append("tcpdump is not running")
    if life.get("last_error"):
        details.append(life["last_error"])
    status.write_status(ok, "; ".join(details) or "capturing", metrics={**cap, **life})


def status_loop(poll_seconds=5):
    while True:
        try:
            _report_status()
        except Exception as exc:  # noqa: BLE001 - the loop outlives one report
            _log(f"status report raised {type(exc).__name__}: {exc}")
        time.sleep(poll_seconds)


def uptime(seconds):
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
        cap=_capture.status() if _capture else {},
        life=_lifecycle.status() if _lifecycle else {},
        options=_options,
        uptime_seconds=int(time.time() - _started_at),
        uptime=uptime,
    )


@app.route("/api/status")
def api_status():
    return jsonify({
        "capture": _capture.status() if _capture else {},
        "lifecycle": _lifecycle.status() if _lifecycle else {},
        "started_at": _started_at,
    })


@app.route("/api/health")
def api_health():
    """What the Add-on Watchdog's probe hits — not `/`, because the
    dashboard answers perfectly well with a dead tcpdump process behind it.
    """
    ok = bool(_capture and _capture.status().get("running"))
    detail = "capturing" if ok else "tcpdump is not running"
    return jsonify({"ok": ok, "detail": detail}), (200 if ok else 503)


def main():
    global _options, _capture, _lifecycle
    _options = load_options()
    _log(
        f"capturing {_options['capture_interfaces']!r}, rotating every "
        f"{_options['rotate_seconds']}s, keeping {_options['retention_files']} files locally, "
        f"shipping to {_options['minio_endpoint']}/{_options['minio_bucket']}/{_options['minio_prefix']}"
    )

    _capture = capture.Capture(_options, log=_log)
    _lifecycle = lifecycle.Lifecycle(_options, log=_log)

    threading.Thread(target=_capture.run_forever, daemon=True).start()
    threading.Thread(target=_lifecycle.run_forever, daemon=True).start()
    threading.Thread(target=status_loop, daemon=True).start()

    port = int(os.environ.get("PORT", "8099"))
    from waitress import serve

    _log(f"serving on 0.0.0.0:{port} (waitress)")
    serve(app, host="0.0.0.0", port=port, threads=4)


if __name__ == "__main__":
    main()
