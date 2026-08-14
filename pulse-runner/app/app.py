import json
import os
import signal
import sqlite3
import sys
import threading
import time
from datetime import datetime

from flask import Flask, Response, g, jsonify, render_template, request

APP_VERSION = "0.1.0"  # keep in sync with the "version" field in config.yaml

DB_PATH = os.environ.get("PULSE_DB_PATH", "/data/pulse.db")
OPTIONS_PATH = os.environ.get("PULSE_OPTIONS_PATH", "/data/options.json")
AUDIO_DIR = os.environ.get("PULSE_AUDIO_DIR", "/data/audio")

# A level's objects, keyed by type, each with the extra fields it requires
# beyond the common x/y every object has. Declared now for the whole level
# format even though only "block"/"spike" have physics behavior in this
# build — pad/orb/portal/checkpoint/decoration land in later milestones
# without needing the stored format, or this validator, to change shape.
_REQUIRED_OBJECT_FIELDS = {
    "block": (("w", (int, float)), ("h", (int, float))),
    "spike": (("w", (int, float)), ("h", (int, float))),
    "pad": (("variant", str),),
    "orb": (("variant", str),),
    "portal": (("kind", str), ("h", (int, float))),
    "checkpoint": (("order", int),),
    "decoration": (("kind", str),),
}
_PORTAL_KINDS = {"gravity", "mode", "speed"}
_START_MODES = {"cube", "ship", "ball"}  # ship/ball are M4; accepted now so the format doesn't shift later

# Seeded on a fresh database so the level list opens populated rather than
# empty. (name, objects, length_units) — hand-placed, not procedural.
# Coordinates are world units; a block's top surface is y + h, and a spike
# sitting on a block is placed with its own y equal to that surface.
SEED_LEVELS = [
    (
        "Warm-up",
        [
            {"type": "block", "x": 0, "y": 0, "w": 25, "h": 1},
            {"type": "spike", "x": 7, "y": 1, "w": 1, "h": 1},
            {"type": "spike", "x": 15, "y": 1, "w": 1, "h": 1},
            # Gap from 25 to 28 — no block underneath, has to be jumped.
            {"type": "block", "x": 28, "y": 0, "w": 32, "h": 1},
            {"type": "spike", "x": 42, "y": 1, "w": 1, "h": 1},
            {"type": "spike", "x": 48, "y": 1, "w": 1, "h": 1},
        ],
        64,
    ),
    (
        "Spike Run",
        [
            {"type": "block", "x": 0, "y": 0, "w": 20, "h": 1},
            {"type": "spike", "x": 6, "y": 1, "w": 1, "h": 1},
            {"type": "spike", "x": 10, "y": 1, "w": 1, "h": 1},
            {"type": "spike", "x": 15, "y": 1, "w": 1, "h": 1},
            # Gap 1: 20 to 23.
            {"type": "block", "x": 23, "y": 0, "w": 13, "h": 1},
            {"type": "spike", "x": 25, "y": 1, "w": 1, "h": 1},
            {"type": "spike", "x": 28, "y": 1, "w": 1, "h": 1},
            {"type": "spike", "x": 31, "y": 1, "w": 1, "h": 1},
            # Gap 2: 36 to 40, wider — a longer jump.
            {"type": "block", "x": 40, "y": 0, "w": 35, "h": 1},
            {"type": "spike", "x": 43, "y": 1, "w": 1, "h": 1},
            {"type": "spike", "x": 48, "y": 1, "w": 1, "h": 1},
            {"type": "spike", "x": 49, "y": 1, "w": 1, "h": 1},
            {"type": "spike", "x": 60, "y": 1, "w": 1, "h": 1},
        ],
        85,
    ),
]

# Ingress passes the authenticated Home Assistant user's ID in this header.
# No port is published for this add-on, so a request without it is a local
# dev/test client, not a public caller — there is nothing else to check.
INGRESS_USER_ID_HEADER = "X-Remote-User-ID"

app = Flask(__name__)


def _log(msg):
    # Timestamped and flushed so lines actually appear in the add-on log
    # (Flask's default logger is WARNING-level and would swallow info).
    print(f"[Pulse Runner] {datetime.now().isoformat()} {msg}", flush=True)


def _read_options():
    try:
        with open(OPTIONS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _audio_cleanup_interval_seconds():
    try:
        hours = int(_read_options().get("audio_cleanup_interval_hours", 6))
    except (TypeError, ValueError):
        hours = 6
    return max(1, min(168, hours)) * 3600


# --- Access control (per-user allowlist over the ingress user-ID header) ---


def get_allowed_user_ids():
    raw = _read_options().get("restrict_to_user_ids", "") or ""
    return {uid.strip() for uid in raw.replace("\n", ",").replace(" ", ",").split(",") if uid.strip()}


def _current_user_id():
    """Identity used for level ownership. Off-ingress (local/dev, or a test
    that doesn't set the header) collapses to a single shared 'local' user
    rather than None, so ownership checks have something consistent to
    compare against."""
    return request.headers.get(INGRESS_USER_ID_HEADER) or "local"


@app.before_request
def _enforce_access():
    """The only door in is Home Assistant's ingress proxy — this add-on
    publishes no port, so a request with no ingress header is a local
    dev/test client, not a public caller. restrict_to_user_ids narrows
    further once an ingress user is present."""
    user_id = request.headers.get(INGRESS_USER_ID_HEADER)
    if not user_id:
        return None
    allowed = get_allowed_user_ids()
    if not allowed or user_id in allowed:
        return None
    return Response(_access_denied_html(user_id), status=403, mimetype="text/html")


def _access_denied_html(user_id):
    import html

    shown = html.escape(user_id)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Pulse Runner — access restricted</title>"
        "<style>body{font-family:system-ui,sans-serif;background:#111;color:#eee;"
        "display:flex;min-height:100vh;margin:0;align-items:center;justify-content:center;padding:1.5rem}"
        ".card{max-width:26rem;text-align:center;line-height:1.5}"
        "code{background:#222;padding:.15rem .4rem;border-radius:5px;word-break:break-all}</style>"
        "</head><body><div class='card'><h1>⚡ Access restricted</h1>"
        "<p>This Pulse Runner is limited to specific Home Assistant users. "
        "Your account isn't on the list.</p>"
        f"<p>Your user ID is:<br><code>{shown}</code></p>"
        "<p>Ask whoever set up the add-on to add this ID to "
        "<strong>restrict_to_user_ids</strong> on the add-on's Configuration tab.</p>"
        "</div></body></html>"
    )


# --- Database ---


def get_db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _db_connect_standalone():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audio_tracks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            title        TEXT NOT NULL,
            filename     TEXT NOT NULL,
            content_type TEXT NOT NULL,
            duration_ms  INTEGER,
            is_builtin   INTEGER NOT NULL DEFAULT 0,
            uploaded_by  TEXT,
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS levels (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT NOT NULL,
            author         TEXT,
            created_by     TEXT,
            scroll_speed   REAL NOT NULL DEFAULT 8,
            start_mode     TEXT NOT NULL DEFAULT 'cube',
            background     TEXT NOT NULL DEFAULT 'grid-blue',
            length_units   REAL NOT NULL DEFAULT 64,
            audio_track_id INTEGER REFERENCES audio_tracks(id) ON DELETE SET NULL,
            objects_json   TEXT NOT NULL DEFAULT '[]',
            is_official    INTEGER NOT NULL DEFAULT 0,
            sort_order     INTEGER NOT NULL DEFAULT 0,
            format_version INTEGER NOT NULL DEFAULT 1,
            created_at     TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_levels_sort_order ON levels(sort_order)")
    _seed_official_levels_if_empty(conn)
    conn.commit()
    conn.close()


def _seed_official_levels_if_empty(conn):
    count = conn.execute("SELECT COUNT(*) AS n FROM levels").fetchone()["n"]
    if count:
        return
    for sort_order, (name, objects, length_units) in enumerate(SEED_LEVELS, start=1):
        conn.execute(
            "INSERT INTO levels "
            "(name, author, created_by, scroll_speed, start_mode, background, "
            " length_units, objects_json, is_official, sort_order) "
            "VALUES (?, ?, NULL, 8, 'cube', 'grid-blue', ?, ?, 1, ?)",
            (name, "Pulse Runner", length_units, json.dumps(objects), sort_order),
        )


# --- Level validation (pure, no Flask/DB — testable directly) ---


def validate_level_payload(data):
    """Raises ValueError with a human-readable message on the first problem
    found. Returns nothing on success."""
    if not isinstance(data, dict):
        raise ValueError("level must be an object")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name is required")
    start_mode = data.get("start_mode", "cube")
    if start_mode not in _START_MODES:
        raise ValueError(f"start_mode must be one of {sorted(_START_MODES)}")
    scroll_speed = data.get("scroll_speed", 8)
    if not isinstance(scroll_speed, (int, float)) or scroll_speed <= 0:
        raise ValueError("scroll_speed must be a positive number")
    length_units = data.get("length_units", 64)
    if not isinstance(length_units, (int, float)) or length_units <= 0:
        raise ValueError("length_units must be a positive number")
    background = data.get("background", "grid-blue")
    if not isinstance(background, str) or not background.strip():
        raise ValueError("background must be a non-empty string")
    validate_level_objects(data.get("objects", []))


def validate_level_objects(objects):
    if not isinstance(objects, list):
        raise ValueError("objects must be a list")
    for i, obj in enumerate(objects):
        if not isinstance(obj, dict):
            raise ValueError(f"object {i} must be an object")
        obj_type = obj.get("type")
        if obj_type not in _REQUIRED_OBJECT_FIELDS:
            raise ValueError(f"object {i} has unknown type {obj_type!r}")
        for field in ("x", "y"):
            if not isinstance(obj.get(field), (int, float)):
                raise ValueError(f"object {i} ({obj_type}) missing numeric '{field}'")
        for field, kind in _REQUIRED_OBJECT_FIELDS[obj_type]:
            val = obj.get(field)
            if not isinstance(val, kind) or (kind is str and not val.strip()):
                raise ValueError(f"object {i} ({obj_type}) missing/invalid '{field}'")
        if obj_type == "portal" and obj.get("kind") not in _PORTAL_KINDS:
            raise ValueError(f"object {i} (portal) kind must be one of {sorted(_PORTAL_KINDS)}")


# --- Routes: pages ---


@app.route("/")
def index():
    return render_template("index.html", app_version=APP_VERSION)


# --- Routes: levels ---


def _level_summary(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "author": row["author"],
        "start_mode": row["start_mode"],
        "background": row["background"],
        "is_official": bool(row["is_official"]),
        "sort_order": row["sort_order"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
    }


def _level_detail(row):
    detail = _level_summary(row)
    detail.update(
        {
            "scroll_speed": row["scroll_speed"],
            "length_units": row["length_units"],
            "audio_track_id": row["audio_track_id"],
            "format_version": row["format_version"],
            "objects": json.loads(row["objects_json"]),
            "updated_at": row["updated_at"],
        }
    )
    return detail


@app.route("/api/levels")
def api_levels():
    db = get_db()
    rows = db.execute("SELECT * FROM levels ORDER BY is_official DESC, sort_order, id").fetchall()
    return jsonify([_level_summary(r) for r in rows])


@app.route("/api/levels/<int:level_id>")
def api_level_detail(level_id):
    db = get_db()
    row = db.execute("SELECT * FROM levels WHERE id = ?", (level_id,)).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(_level_detail(row))


@app.route("/api/levels", methods=["POST"])
def api_create_level():
    data = request.get_json(silent=True) or {}
    try:
        validate_level_payload(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    db = get_db()
    user_id = _current_user_id()
    cur = db.execute(
        "INSERT INTO levels "
        "(name, author, created_by, scroll_speed, start_mode, background, "
        " length_units, objects_json, is_official, sort_order) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0)",
        (
            data["name"].strip(),
            data.get("author") or user_id,
            user_id,
            data.get("scroll_speed", 8),
            data.get("start_mode", "cube"),
            data.get("background", "grid-blue"),
            data.get("length_units", 64),
            json.dumps(data.get("objects", [])),
        ),
    )
    db.commit()
    row = db.execute("SELECT * FROM levels WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(_level_detail(row)), 201


def _load_owned_level(db, level_id, user_id):
    """Returns the row, or (None, error_response) if it can't be edited by
    this user — not found, or found but owned by someone else / official."""
    row = db.execute("SELECT * FROM levels WHERE id = ?", (level_id,)).fetchone()
    if row is None:
        return None, (jsonify({"error": "not found"}), 404)
    if row["is_official"] or row["created_by"] != user_id:
        return None, (jsonify({"error": "not your level"}), 403)
    return row, None


@app.route("/api/levels/<int:level_id>", methods=["PUT"])
def api_update_level(level_id):
    db = get_db()
    row, err = _load_owned_level(db, level_id, _current_user_id())
    if err:
        return err
    data = request.get_json(silent=True) or {}
    try:
        validate_level_payload(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    db.execute(
        "UPDATE levels SET name = ?, author = ?, scroll_speed = ?, start_mode = ?, "
        "background = ?, length_units = ?, objects_json = ?, updated_at = datetime('now') "
        "WHERE id = ?",
        (
            data["name"].strip(),
            data.get("author") or row["author"],
            data.get("scroll_speed", 8),
            data.get("start_mode", "cube"),
            data.get("background", "grid-blue"),
            data.get("length_units", 64),
            json.dumps(data.get("objects", [])),
            level_id,
        ),
    )
    db.commit()
    updated = db.execute("SELECT * FROM levels WHERE id = ?", (level_id,)).fetchone()
    return jsonify(_level_detail(updated))


@app.route("/api/levels/<int:level_id>", methods=["DELETE"])
def api_delete_level(level_id):
    db = get_db()
    _row, err = _load_owned_level(db, level_id, _current_user_id())
    if err:
        return err
    db.execute("DELETE FROM levels WHERE id = ?", (level_id,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/debug")
def api_debug():
    db = get_db()
    count = db.execute("SELECT COUNT(*) AS n FROM levels").fetchone()["n"]
    return jsonify({"app_version": APP_VERSION, "level_count": count})


# --- Background: sweep uploaded audio files no level references anymore ---


def _cleanup_orphaned_audio_tick(conn):
    if not os.path.isdir(AUDIO_DIR):
        return
    referenced = {r["filename"] for r in conn.execute("SELECT filename FROM audio_tracks")}
    for name in os.listdir(AUDIO_DIR):
        if name not in referenced:
            try:
                os.remove(os.path.join(AUDIO_DIR, name))
            except OSError:
                pass


def _background_loop():
    while True:
        try:
            conn = _db_connect_standalone()
            try:
                _cleanup_orphaned_audio_tick(conn)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - keep the loop alive across any single failure
            app.logger.exception("background loop iteration failed")
        time.sleep(_audio_cleanup_interval_seconds())


# --- Shutdown + entrypoint ---


def _handle_shutdown_signal(signum, frame):
    _log(f"received signal {signum}, shutting down")
    sys.exit(0)


if __name__ == "__main__":
    from waitress import serve

    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    init_db()
    _log(f"starting Pulse Runner {APP_VERSION}")
    threading.Thread(target=_background_loop, daemon=True).start()
    port = int(os.environ.get("PULSE_PORT", "8099"))
    _log(f"serving on 0.0.0.0:{port} (waitress)")
    serve(app, host="0.0.0.0", port=port)
