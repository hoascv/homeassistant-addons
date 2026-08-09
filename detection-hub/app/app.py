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
from datetime import datetime

from flask import Flask, Response, g, jsonify, render_template, request

import detector
import store

APP_VERSION = "1.1.0"  # keep in sync with the "version" field in config.yaml

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
        db = get_db()
        snapshot_id = None
        jpeg = detector.encode_jpeg(image)
        if jpeg:
            snapshot_id = store.save_snapshot(db, jpeg, image.shape[1], image.shape[0])
        store.upsert_camera(db, camera, "api", state="ok", last_detection_at=None)
        store.record_detections(db, camera, detections, snapshot_id=snapshot_id)
        store.bump_camera_counters(db, camera, frames_seen=1, frames_detected=1)
        db.commit()
        body["stored"] = {"camera": camera, "snapshot_id": snapshot_id}

    return jsonify(body)


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
    """The stored JPEG. Kept out of the change feed on purpose — a consumer that
    wants the image asks for it by id rather than receiving every one inline."""
    row = store.snapshot(get_db(), snapshot_id)
    if row is None:
        return jsonify({"error": "no such snapshot"}), 404
    return Response(bytes(row["image"]), mimetype="image/jpeg")


@app.route("/api/cameras")
def api_cameras():
    return jsonify({"cameras": store.cameras(get_db())})


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


def _background_loop():
    """Housekeeping, once a minute.

    Retention is the whole job for now: a camera generates far more rows than a
    person logging eggs, and `/data` is inside Home Assistant's backups. Any one
    iteration failing must not end the loop — a pruning error is not a reason to
    stop detecting.
    """
    while True:
        try:
            conn = store.connect(actor="automation")
            try:
                removed = store.prune(conn, **get_retention())
                conn.commit()
                if any(removed.values()):
                    _log("pruned " + ", ".join(f"{n} {t}" for t, n in removed.items() if n))
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

    port = int(os.environ.get("DETECTION_HUB_PORT", "8099"))
    _log(f"serving on 0.0.0.0:{port} (waitress)")
    serve(app, host="0.0.0.0", port=port, threads=4)
