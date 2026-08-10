"""Detection Hub — object detection as a service for Home Assistant.

The detector and the HTTP API in front of it, plus the store that remembers what
was seen and the change feed that hands it to the data pipeline. Cameras and the
Home Assistant integration land on top of these.
"""
import hmac
import html
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime, timedelta

from flask import Flask, Response, g, jsonify, render_template, request, send_file

import capture
import detector
import hass
import store

APP_VERSION = "1.6.0"  # keep in sync with the "version" field in config.yaml

OPTIONS_PATH = os.environ.get("DETECTION_HUB_OPTIONS_PATH", "/data/options.json")

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
HA_API_BASE = "http://supervisor/core/api"

# Ingress passes the authenticated Home Assistant user's ID in this header. It
# is also how a request that came through the proxy is told apart from one that
# arrived on the published port — see _enforce_access.
INGRESS_USER_ID_HEADER = "X-Remote-User-ID"

# A phone photo through /api/detect is a couple of MB; 20 puts a ceiling on what
# an unauthenticated caller can make us buffer before the token is even checked.
MAX_IMAGE_BYTES = 20 * 1024 * 1024

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_IMAGE_BYTES

_detector = None
_detector_lock = threading.Lock()
_capture = None

# Per-camera online/offline memory, so a notification fires on the *transition*
# rather than every minute a camera stays down. Seeded on first sighting without
# a notification: a camera still connecting at the first health check should not
# page anyone, and a camera broken from boot is a config error you are already
# watching for — the surprise worth paging on is one that was working and
# stopped.
_camera_online = {}


def _log(msg):
    """One line, timestamped, flushed.

    flask's app.logger defaults to WARNING so info lines vanish, and stdout is
    block-buffered when it is a pipe — an unflushed line can be lost entirely if
    the container is later SIGKILLed rather than stopped cleanly.
    """
    print(f"[Detection Hub] {datetime.now().isoformat()} {msg}", flush=True)


# --- options ------------------------------------------------------------------


def _read_options():
    """Read fresh every time. At this request volume re-parsing a small JSON
    file is free, and it avoids the whole class of "I changed the config and it
    didn't take" bugs a cache would need explicit invalidation to avoid."""
    try:
        with open(OPTIONS_PATH) as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def get_api_token():
    return (_read_options().get("api_token") or "").strip()


def get_allowed_user_ids():
    raw = _read_options().get("restrict_to_user_ids", "") or ""
    return {
        uid.strip()
        for uid in raw.replace("\n", ",").replace(" ", ",").split(",")
        if uid.strip()
    }


def get_confidence():
    """0.6 rather than something looser: at 0.35 a dark post in the test frame
    scored 0.50 as a person. A false 'someone is at the door' at 3am costs more
    than a missed detection that the next frame catches anyway."""
    try:
        value = float(_read_options().get("confidence", 0.6))
    except (TypeError, ValueError):
        return 0.6
    return min(0.99, max(0.05, value))


def get_labels():
    """The classes worth reporting, or None for all 80.

    COCO's full set is noisy for a camera — 'potted plant' and 'chair' fire
    constantly and bury the two labels anyone automates on.
    """
    raw = _read_options().get("labels", "") or ""
    wanted = [name.strip().lower() for name in raw.replace("\n", ",").split(",")]
    return [name for name in wanted if name in detector.COCO_LABELS] or None


def get_model_path():
    return (_read_options().get("model_path") or "").strip() or None


def _int_option(name, default, low, high):
    try:
        return min(high, max(low, int(_read_options().get(name, default))))
    except (TypeError, ValueError):
        return default


def get_cameras():
    return capture.parse_cameras(_read_options().get("cameras", ""))


def get_capture_options():
    return {
        "max_fps": _float_option("max_fps", 2.0, 0.0, 30.0),
        "motion_threshold": _float_option("motion_threshold", 0.005, 0.0, 1.0),
        "cooldown_seconds": _int_option("cooldown_seconds", 30, 0, 3600),
        "collapse_repeats": bool(_read_options().get("collapse_repeats", True)),
    }


def _float_option(name, default, low, high):
    try:
        return min(high, max(low, float(_read_options().get(name, default))))
    except (TypeError, ValueError):
        return default


def get_ha_config():
    options = _read_options()
    return {
        "sensors": bool(options.get("ha_sensors_enabled", True)),
        "events": bool(options.get("ha_events_enabled", True)),
        "prefix": (options.get("sensor_prefix") or "detection_hub").strip(),
        "notify_service": (options.get("notify_service") or "").strip(),
        "offline_seconds": _int_option("camera_offline_seconds", 120, 30, 3600),
    }


def get_retention():
    return {
        "detection_days": _int_option("detection_retention_days", 30, 1, 3650),
        "snapshot_days": _int_option("snapshot_retention_days", 7, 1, 365),
        "snapshot_max": _int_option("snapshot_max_count", 2000, 0, 100000),
    }


# --- database -----------------------------------------------------------------


def get_db():
    """One connection per request, closed on teardown.

    sqlite3 connections are not shareable across threads and waitress serves
    from four, so nothing here is cached beyond the request.
    """
    if "db" not in g:
        g.db = store.connect(actor="user")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def get_detector():
    global _detector
    if _detector is None:
        with _detector_lock:
            if _detector is None:
                _detector = detector.Detector(model_path=get_model_path())
    return _detector


# --- access control -----------------------------------------------------------


def _request_has_api_token():
    token = get_api_token()
    if not token:
        return False
    header = request.headers.get("Authorization", "")
    presented = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not presented:
        return False
    # Compared as bytes: compare_digest refuses non-ASCII str, so a token with an
    # accent would raise rather than simply not match. Constant-time, because
    # the comparison is against a secret.
    return hmac.compare_digest(presented.encode("utf-8"), token.encode("utf-8"))


@app.before_request
def _enforce_access():
    """Two doors in, and each needs its own key.

    Through ingress, the Supervisor has already authenticated a Home Assistant
    user and passes their ID; `restrict_to_user_ids` narrows that further.

    Through the published port nothing has authenticated anybody, and the only
    credential that exists there is `api_token` — so it is required, including
    when none is configured. "No credential is set" cannot mean "no check is
    needed"; that reading is what left the trackers' backup endpoints open until
    Coop Tracker 1.44.0.
    """
    if _request_has_api_token():
        return None

    user_id = request.headers.get(INGRESS_USER_ID_HEADER)
    if not user_id:
        return Response(
            json.dumps(
                {
                    "error": "unauthorized",
                    "detail": (
                        "This port requires a bearer token. Set api_token in the "
                        "add-on's Configuration tab and send it as "
                        "'Authorization: Bearer <token>'. Requests through Home "
                        "Assistant's ingress do not need one."
                    ),
                }
            ),
            status=401,
            mimetype="application/json",
            headers={"WWW-Authenticate": "Bearer"},
        )

    allowed = get_allowed_user_ids()
    if not allowed or user_id in allowed:
        return None
    return Response(_access_denied_html(user_id), status=403, mimetype="text/html")


def _access_denied_html(user_id):
    shown = html.escape(user_id)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Detection Hub — access restricted</title>"
        "<style>body{font-family:system-ui,sans-serif;background:#111;color:#eee;"
        "display:flex;min-height:100vh;margin:0;align-items:center;"
        "justify-content:center;padding:1.5rem}"
        ".card{max-width:26rem;text-align:center;line-height:1.5}"
        "code{background:#222;padding:.15rem .4rem;border-radius:5px;"
        "word-break:break-all}</style>"
        "</head><body><div class='card'><h1>👁 Access restricted</h1>"
        "<p>This Detection Hub is limited to specific Home Assistant users. "
        "Your account isn't on the list.</p>"
        f"<p>Your user ID is:<br><code>{shown}</code></p>"
        "<p>Ask whoever set up the add-on to add this ID to "
        "<strong>restrict_to_user_ids</strong> on the add-on's Configuration tab."
        "</p></div></body></html>"
    )


def _api_access_summary():
    token = get_api_token()
    if not token:
        return (
            "API token auth: OFF — no api_token configured, so the published "
            "port (if you mapped one) refuses every request. Ingress is "
            "unaffected. A data pipeline needs this set."
        )
    return (
        f"API token auth: ON — api_token is set ({len(token)} characters). "
        "The published port accepts it and nothing else."
    )


# --- routes -------------------------------------------------------------------


@app.route("/")
def index():
    det = get_detector()
    return render_template(
        "index.html",
        app_version=APP_VERSION,
        status=det.status(),
        confidence=get_confidence(),
        labels=get_labels() or list(detector.COCO_LABELS),
        label_filter_active=get_labels() is not None,
    )


@app.route("/api/detect", methods=["POST"])
def api_detect():
    """An image in, detections out. Stateless — nothing is stored.

    Accepts either a multipart upload under `image` or the raw bytes as the
    body, because the two callers differ: a browser form sends multipart and
    `curl --data-binary` does not, and requiring the wrong one is an unhelpful
    400 for whichever caller guessed.
    """
    # Order matters. Touching request.files makes Werkzeug parse the body as a
    # form, and for the content type curl sends by default with --data-binary
    # (application/x-www-form-urlencoded) that consumes the stream — leaving
    # get_data() empty and the caller staring at "empty image" while holding a
    # perfectly good JPEG. So only look for a file part when the request really
    # is multipart.
    if request.mimetype == "multipart/form-data":
        uploaded = request.files.get("image")
        data = uploaded.read() if uploaded else b""
    else:
        data = request.get_data(parse_form_data=False)

    image, error = detector.decode_image(data)
    if error:
        return jsonify({"error": error}), 400

    confidence = _float_arg("confidence", get_confidence())
    labels = get_labels()
    if request.args.get("labels"):
        requested = [
            name.strip().lower() for name in request.args["labels"].split(",")
        ]
        labels = [name for name in requested if name in detector.COCO_LABELS] or None

    detections, error = get_detector().detect(
        image, confidence=confidence, labels=labels
    )
    if error:
        # The model is missing or unloadable: that is this add-on's own fault,
        # not the caller's, so it is a 503 rather than a 400.
        return jsonify({"error": error}), 503

    body = {
        "detections": detections,
        "count": len(detections),
        "image": {"width": image.shape[1], "height": image.shape[0]},
        "confidence": confidence,
        "latency_ms": get_detector().last_latency_ms,
    }

    # `?camera=<name>` turns this from a calculator into an input source: the
    # detections are recorded under that name and reach the lakehouse on the
    # next ingest, exactly as a camera's would. Without it nothing is stored,
    # which is what keeps the try-it page from filling the database.
    camera = (request.args.get("camera") or "").strip()
    if camera and detections:
        snapshot_id = record(get_db(), camera, detections, image, kind="api")
        body["stored"] = {"camera": camera, "snapshot_id": snapshot_id}

    return jsonify(body)


# --- recording ----------------------------------------------------------------


def record(conn, camera, detections, image, kind="api"):
    """Persist detections and their snapshot, and tell Home Assistant.

    One function for both callers — the HTTP API and a camera thread — so the
    two can never drift into storing different things. The connection is passed
    in because sqlite3 has thread affinity and the camera threads must not
    borrow a request's.
    """
    # None when the image could not be encoded or could not be written — a full
    # disk, a read-only volume. The detection is still worth storing, and the
    # column is nullable for exactly this.
    snapshot_id = None
    jpeg = detector.encode_jpeg(image)
    if jpeg:
        snapshot_id = store.save_snapshot(conn, jpeg, image.shape[1], image.shape[0])

    store.upsert_camera(conn, camera, kind, last_detection_at=store.now())
    store.record_detections(conn, camera, detections, snapshot_id=snapshot_id)
    store.bump_camera_counters(conn, camera, frames_seen=1, frames_detected=1)
    conn.commit()

    ha = get_ha_config()
    if ha["events"]:
        for det in detections:
            error = hass.fire_event(camera, det, snapshot_id)
            if error:
                # Worth one line, not a crash: the detection is already stored,
                # and a camera must keep watching whether or not HA heard.
                _log(f"could not fire event for {camera}/{det['label']}: {error}")
    return snapshot_id


def _on_camera_detections(camera, detections, frame):
    """Called from a camera thread. Owns its own connection, by necessity."""
    conn = store.connect(actor="camera")
    try:
        record(conn, camera, detections, frame, kind="rtsp")
    finally:
        conn.close()


def get_capture():
    global _capture
    if _capture is None:
        _capture = capture.CaptureManager(
            get_detector(), _on_camera_detections, log=_log
        )
    return _capture


# --- the change feed ----------------------------------------------------------


@app.route("/api/export")
def api_export():
    """Full snapshot of every tracked table, plus the seq it corresponds to."""
    payload = store.export(get_db())
    payload["app_version"] = APP_VERSION
    return jsonify(payload)


@app.route("/api/changes")
def api_changes():
    """Everything after a watermark. The steady-state ingest path."""
    try:
        since = int(request.args.get("since", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "since must be a number"}), 400
    try:
        limit = int(request.args.get("limit", 1000))
    except (TypeError, ValueError):
        return jsonify({"error": "limit must be a number"}), 400
    return jsonify(store.changes(get_db(), since=since, limit=limit))


@app.route("/api/stats")
def api_stats():
    """Row counts and size without serialising a row — what the watchdog polls."""
    payload = store.stats(get_db())
    payload["app_version"] = APP_VERSION
    return jsonify(payload)


# --- reading what was seen ----------------------------------------------------


@app.route("/api/detections")
def api_detections():
    return jsonify(
        {
            "detections": store.recent_detections(
                get_db(),
                limit=request.args.get("limit", 50),
                camera=request.args.get("camera"),
            )
        }
    )


@app.route("/api/snapshots/<int:snapshot_id>")
def api_snapshot(snapshot_id):
    """The stored JPEG, read from disk.

    Kept out of the change feed on purpose — a consumer that wants the image
    asks for it by id rather than receiving every one inline. A 404 here also
    covers the ordinary case of a restored backup: the detections come back and
    the pictures, deliberately, do not.
    """
    found = store.snapshot(get_db(), snapshot_id)
    if found is None:
        return jsonify({"error": "no such snapshot"}), 404
    return send_file(found["path"], mimetype="image/jpeg")


@app.route("/api/cameras")
def api_cameras():
    """Stored history merged with what the capture threads currently know."""
    return jsonify({"cameras": _camera_rows(), "metrics": get_capture().metrics()})


def _float_arg(name, default):
    try:
        return min(0.99, max(0.05, float(request.args[name])))
    except (KeyError, TypeError, ValueError):
        return default


@app.route("/api/health")
def api_health():
    """This add-on's own health, since nothing else is watching it."""
    det = get_detector()
    ready = det.available()
    return (
        jsonify(
            {
                "ok": ready,
                "app_version": APP_VERSION,
                "detector_ready": ready,
                "error": det.error,
            }
        ),
        200 if ready else 503,
    )


@app.route("/api/debug")
def api_debug():
    token = get_api_token()
    return jsonify(
        {
            "app_version": APP_VERSION,
            "supervisor_token_set": bool(SUPERVISOR_TOKEN),
            "api_token_set": bool(token),
            "api_token_length": len(token),
            "restrict_to_user_ids_set": bool(get_allowed_user_ids()),
            "ingress_user_id": request.headers.get(INGRESS_USER_ID_HEADER),
            "confidence": get_confidence(),
            "labels": get_labels(),
            "detector": get_detector().status(),
        }
    )


# --- entry point --------------------------------------------------------------


def _handle_shutdown_signal(signum, frame):
    _log(f"received signal {signum}, shutting down")
    sys.exit(0)


def _camera_rows():
    """Live camera state, merging what the threads know with what is stored.

    The threads hold the truth about liveness; the database holds the history.
    Neither alone answers "is the driveway camera working".
    """
    live = {c["id"]: c for c in get_capture().status()}
    rows = []
    conn = store.connect(actor="automation")
    try:
        for stored in store.cameras(conn):
            rows.append({**stored, **live.get(stored["id"], {"alive": False})})
        for camera_id, status in live.items():
            if not any(r["id"] == camera_id for r in rows):
                rows.append(status)
    finally:
        conn.close()
    return rows


def _camera_is_online(row, offline_seconds, now=None):
    """Online means: a live thread, in the ok state, that produced a frame
    recently.

    The staleness check is the one that catches a hung stream — a camera whose
    thread is alive and never threw, but whose frames stopped arriving. Without
    it a frozen feed reads as healthy, which is the failure a watchdog exists to
    catch rather than miss.
    """
    if not row.get("alive") or row.get("state") != "ok":
        return False
    last = row.get("last_frame_at")
    if not last:
        return False
    try:
        seen = datetime.strptime(last, "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return False
    return (now or datetime.now()) - seen <= timedelta(seconds=offline_seconds)


def _check_camera_health(cameras, ha, now=None):
    """Notify on a camera crossing between online and offline. Returns the
    transitions, so the loop can log them and a test can assert on them."""
    transitions = []
    for row in cameras:
        camera_id = row["id"]
        online = _camera_is_online(row, ha["offline_seconds"], now=now)
        previous = _camera_online.get(camera_id)
        _camera_online[camera_id] = online

        if previous is None or previous == online:
            continue  # first sighting, or no change — neither is news

        transitions.append((camera_id, online))
        if online:
            message, log = f"{camera_id} camera back online", "recovered"
        else:
            detail = (row.get("detail") or "no recent frames").strip()
            message, log = f"{camera_id} camera offline — {detail}", "offline"
        _log(f"camera {camera_id}: {log}"
             + ("" if not ha["notify_service"] else " (notifying)"))
        if ha["notify_service"]:
            error = hass.notify(ha["notify_service"], message)
            if error:
                _log(f"could not notify about {camera_id}: {error}")
    return transitions


def _publish(conn):
    """Sensors, the camera watchdog, and the add-on's own status file."""
    ha = get_ha_config()
    cameras = _camera_rows()
    metrics = get_capture().metrics()
    detector_status = get_detector().status()

    if ha["sensors"]:
        midnight = datetime.now().strftime("%Y-%m-%dT00:00:00")
        errors = hass.publish_sensors(
            ha["prefix"], store.counts_since(conn, midnight), cameras, detector_status
        )
        for error in errors:
            _log(f"could not publish sensor {error}")

    _check_camera_health(cameras, ha)

    # The watchdog's own view. `ok` is false when the detector is broken or when
    # a configured camera is not running — the two failures an HTTP probe of
    # this add-on cannot see, since the web UI answers either way.
    configured = len(get_cameras())
    online = metrics.get("cameras_ok", 0)
    if detector_status["error"]:
        ok, detail = False, f"detector unavailable: {detector_status['error']}"
    elif configured and online < configured:
        ok, detail = False, f"{online}/{configured} cameras streaming"
    elif configured:
        ok, detail = True, f"{configured} camera(s) streaming"
    else:
        ok, detail = True, "detector ready; no cameras configured"
    hass.write_status(ok, detail, metrics)


def _background_loop():
    """Housekeeping, once a minute.

    Retention, sensors and the status file. Any one iteration failing must not
    end the loop — a pruning error is not a reason to stop detecting, and a
    Home Assistant that is briefly unreachable is not a reason to stop either.
    """
    while True:
        try:
            conn = store.connect(actor="automation")
            try:
                removed = store.prune(conn, **get_retention())
                conn.commit()
                if any(removed.values()):
                    _log("pruned " + ", ".join(f"{n} {t}" for t, n in removed.items() if n))
                # After the commit, never inside it.
                store.checkpoint(conn)
                _publish(conn)
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - the loop outlives any one failure
            _log(f"background loop iteration failed: {type(exc).__name__}: {exc}")
        time.sleep(60)


if __name__ == "__main__":
    from waitress import serve

    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    _log(f"Detection Hub {APP_VERSION} starting")
    _log(_api_access_summary())
    store.init_db()
    threading.Thread(target=_background_loop, daemon=True).start()

    # Load the model at startup rather than on the first request: a 15ms model
    # takes a moment to read from disk, and a broken one should be in the log
    # before anybody asks, not discovered by whoever makes the first call.
    status = get_detector().status() if get_detector().load() else get_detector().status()
    if status["error"]:
        _log(f"detector UNAVAILABLE: {status['error']}")
    else:
        _log(f"detector ready: {os.path.basename(status['model_path'])} "
             f"at {status['input_size']}x{status['input_size']}")

    cameras = get_cameras()
    if cameras:
        get_capture().configure(
            cameras,
            confidence=get_confidence(),
            labels=get_labels(),
            **get_capture_options(),
        )
        _log(f"watching {len(cameras)} camera(s): {', '.join(c['id'] for c in cameras)}")
    else:
        _log("no cameras configured — the API and the try-it page still work")

    port = int(os.environ.get("DETECTION_HUB_PORT", "8099"))
    _log(f"serving on 0.0.0.0:{port} (waitress)")
    serve(app, host="0.0.0.0", port=port, threads=4)
