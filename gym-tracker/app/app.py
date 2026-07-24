import html
import importlib.metadata
import json
import os
import platform
import signal
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, time as dtime, timedelta

from flask import Flask, Response, g, jsonify, render_template, request, send_file

APP_VERSION = "1.2.0"  # keep in sync with the "version" field in config.yaml

DB_PATH = os.environ.get("GYM_DB_PATH", "/data/gym.db")
OPTIONS_PATH = os.environ.get("GYM_OPTIONS_PATH", "/data/options.json")

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
HA_API_BASE = "http://supervisor/core/api"

# Monday..Sunday, index matches datetime.weekday() (Monday == 0). Used to
# resolve the weighin_reminder_weekday option to a comparable index.
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# Seeded on a fresh database so the app opens onto a populated, meaningful
# home screen instead of empty state. All editable afterwards.
SEED_START_DATE = "2026-07-03"
SEED_START_WEIGHT = 99.7
SEED_GOAL = {
    "target_date": "2026-12-28",
    "target_weight_kg": 105.0,
    "target_body_fat_pct": 15.0,
    "start_date": SEED_START_DATE,
    "start_weight_kg": SEED_START_WEIGHT,
}
# (name, default dose) — a small starter supplements library, editable.
SEED_SUPPLEMENTS = [
    ("Creatine", "5 g"),
    ("Protein powder", "30 g"),
]
# The default daily challenge, now typed: each item references a library
# entry (an exercise with a rep target, or a supplement with a dose) rather
# than free text, so the data stays clean and links back to the libraries.
SEED_CHALLENGE = [
    {"type": "supplement", "name": "Creatine", "dose": "5 g"},
    {"type": "exercise", "name": "Push-up", "target_reps": 40},
    {"type": "exercise", "name": "Squat", "target_reps": 40},
]

# (name, equipment, category) — a home-friendly starter library. The user
# extends this as they buy equipment (POST /api/exercises).
PRESET_EXERCISES = [
    ("Push-up", "Bodyweight", "Push"),
    ("Squat", "Bodyweight", "Legs"),
    ("Lunge", "Bodyweight", "Legs"),
    ("Plank", "Bodyweight", "Core"),
    ("Glute bridge", "Bodyweight", "Legs"),
    ("Mountain climber", "Bodyweight", "Core"),
    ("Burpee", "Bodyweight", "Full body"),
    ("Chair dip", "Bodyweight", "Push"),
    ("Pull-up", "Pull-up bar", "Pull"),
    ("Chin-up", "Pull-up bar", "Pull"),
    ("Hanging knee raise", "Pull-up bar", "Core"),
    ("Dumbbell curl", "Dumbbells", "Pull"),
    ("Dumbbell shoulder press", "Dumbbells", "Push"),
    ("Dumbbell floor press", "Dumbbells", "Push"),
    ("One-arm dumbbell row", "Dumbbells", "Pull"),
    ("Goblet squat", "Dumbbells", "Legs"),
    ("Romanian deadlift", "Dumbbells", "Legs"),
    ("Lateral raise", "Dumbbells", "Push"),
]
# Order equipment groups consistently in the API response.
EQUIPMENT_ORDER = ["Bodyweight", "Pull-up bar", "Dumbbells"]

app = Flask(__name__)


def _log(msg):
    # Timestamped and flushed so lines actually appear in the add-on log
    # (Flask's default logger is WARNING-level and would swallow info).
    print(f"[Gym Tracker] {datetime.now().isoformat()} {msg}", flush=True)


def _read_options():
    try:
        with open(OPTIONS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# --- Access control (per-user allowlist over the ingress user-ID header) ---

# Ingress passes the authenticated Home Assistant user's ID in this header.
# HA does not expose the user's admin/owner flag to add-ons, so a per-user
# allowlist is how "restrict who can use this" is enforced. Mirrors the
# Coop Tracker add-on.
INGRESS_USER_ID_HEADER = "X-Remote-User-ID"


def get_allowed_user_ids():
    raw = _read_options().get("restrict_to_user_ids", "") or ""
    return {uid.strip() for uid in raw.replace("\n", ",").replace(" ", ",").split(",") if uid.strip()}


@app.before_request
def _enforce_user_allowlist():
    allowed = get_allowed_user_ids()
    if not allowed:
        return None  # feature off — any authenticated ingress user may access
    user_id = request.headers.get(INGRESS_USER_ID_HEADER)
    if user_id and user_id in allowed:
        return None
    return Response(_access_denied_html(user_id), status=403, mimetype="text/html")


def _access_denied_html(user_id):
    shown = html.escape(user_id) if user_id else "(unknown — not opened through Home Assistant)"
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Gym Tracker — access restricted</title>"
        "<style>body{font-family:system-ui,sans-serif;background:#111;color:#eee;"
        "display:flex;min-height:100vh;margin:0;align-items:center;justify-content:center;padding:1.5rem}"
        ".card{max-width:26rem;text-align:center;line-height:1.5}"
        "code{background:#222;padding:.15rem .4rem;border-radius:5px;word-break:break-all}</style>"
        "</head><body><div class='card'><h1>💪 Access restricted</h1>"
        "<p>This Gym Tracker is limited to specific Home Assistant users. "
        "Your account isn't on the list.</p>"
        f"<p>Your user ID is:<br><code>{shown}</code></p>"
        "<p>Ask whoever set up the add-on to add this ID to "
        "<strong>restrict_to_user_ids</strong> on the add-on's Configuration tab.</p>"
        "</div></body></html>"
    )


# --- Options getters ---


def get_reminders_config():
    opts = _read_options()
    return {
        "notify_service": (opts.get("notify_service") or "").strip(),
        "challenge_enabled": bool(opts.get("challenge_reminder_enabled", False)),
        "challenge_time": opts.get("challenge_reminder_time", "18:00"),
        "weighin_enabled": bool(opts.get("weighin_reminder_enabled", False)),
        "weighin_weekday": (opts.get("weighin_reminder_weekday", "sunday") or "sunday").strip().lower(),
        "weighin_time": opts.get("weighin_reminder_time", "08:00"),
    }


# --- Database ---


def get_db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
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
    return conn


def _get_app_state(conn, key):
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_app_state(conn, key, value):
    conn.execute(
        "INSERT INTO app_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # seeds use column-name access
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS weight_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            weight_kg REAL NOT NULL,
            body_fat_pct REAL,
            notes TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS goal (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            target_date TEXT,
            target_weight_kg REAL,
            target_body_fat_pct REAL,
            start_date TEXT,
            start_weight_kg REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS exercises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            equipment TEXT NOT NULL DEFAULT 'Bodyweight',
            category TEXT,
            is_custom INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0,
            notes TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS supplements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            dose TEXT,
            is_custom INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workout_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            exercise_id INTEGER NOT NULL REFERENCES exercises(id),
            sets INTEGER,
            reps INTEGER,
            weight_kg REAL,
            duration_sec INTEGER,
            notes TEXT,
            source TEXT NOT NULL DEFAULT 'manual',
            challenge_item_id INTEGER
        )
        """
    )
    # Typed challenge items: each references either an exercise (with a
    # sets/reps target) or a supplement (with a dose). `label` is a display
    # fallback; the live name comes from the referenced library row.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS challenge_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0,
            item_type TEXT,
            exercise_id INTEGER,
            supplement_id INTEGER,
            target_sets INTEGER,
            target_reps INTEGER,
            dose TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS challenge_completions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL REFERENCES challenge_items(id),
            day TEXT NOT NULL,
            UNIQUE(item_id, day)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )

    _migrate_columns(conn)
    _seed_defaults(conn)
    conn.commit()
    conn.close()


def _migrate_columns(conn):
    """Bring an older database (1.0.0's untyped challenge, no supplements)
    up to the current schema. CREATE TABLE handles fresh installs; these
    ALTERs handle in-place upgrades."""
    challenge_cols = {row[1] for row in conn.execute("PRAGMA table_info(challenge_items)")}
    for col, decl in (
        ("item_type", "TEXT"),
        ("exercise_id", "INTEGER"),
        ("supplement_id", "INTEGER"),
        ("target_sets", "INTEGER"),
        ("target_reps", "INTEGER"),
        ("dose", "TEXT"),
    ):
        if col not in challenge_cols:
            conn.execute(f"ALTER TABLE challenge_items ADD COLUMN {col} {decl}")
    workout_cols = {row[1] for row in conn.execute("PRAGMA table_info(workout_logs)")}
    if "source" not in workout_cols:
        conn.execute("ALTER TABLE workout_logs ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'")
    if "challenge_item_id" not in workout_cols:
        conn.execute("ALTER TABLE workout_logs ADD COLUMN challenge_item_id INTEGER")


def _seed_defaults(conn):
    """Populate a fresh database so it opens onto a usable state. Each seed
    is guarded on emptiness, so it runs once and never fights the user's
    later edits/deletions."""
    if conn.execute("SELECT COUNT(*) FROM goal").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO goal (id, target_date, target_weight_kg, target_body_fat_pct, "
            "start_date, start_weight_kg) VALUES (1, ?, ?, ?, ?, ?)",
            (
                SEED_GOAL["target_date"],
                SEED_GOAL["target_weight_kg"],
                SEED_GOAL["target_body_fat_pct"],
                SEED_GOAL["start_date"],
                SEED_GOAL["start_weight_kg"],
            ),
        )
    if conn.execute("SELECT COUNT(*) FROM weight_logs").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO weight_logs (ts, weight_kg, body_fat_pct, notes) VALUES (?, ?, NULL, ?)",
            (f"{SEED_START_DATE}T08:00:00", SEED_START_WEIGHT, "Starting weight"),
        )
    if conn.execute("SELECT COUNT(*) FROM exercises").fetchone()[0] == 0:
        for name, equipment, category in PRESET_EXERCISES:
            conn.execute(
                "INSERT INTO exercises (name, equipment, category, is_custom, archived) "
                "VALUES (?, ?, ?, 0, 0)",
                (name, equipment, category),
            )
    if conn.execute("SELECT COUNT(*) FROM supplements").fetchone()[0] == 0:
        for name, dose in SEED_SUPPLEMENTS:
            conn.execute(
                "INSERT INTO supplements (name, dose, is_custom, archived) VALUES (?, ?, 0, 0)",
                (name, dose),
            )
    _seed_typed_challenge(conn)


def _seed_typed_challenge(conn):
    """Seed the default typed challenge, once, referencing the seeded
    library rows. Also converts a 1.0.0 database: any legacy free-text
    (untyped) items are archived so only clean, typed items remain."""
    typed = conn.execute(
        "SELECT COUNT(*) FROM challenge_items WHERE item_type IS NOT NULL AND archived = 0"
    ).fetchone()[0]
    if typed:
        return
    conn.execute("UPDATE challenge_items SET archived = 1 WHERE item_type IS NULL")
    for order, spec in enumerate(SEED_CHALLENGE):
        if spec["type"] == "supplement":
            row = conn.execute(
                "SELECT id FROM supplements WHERE name = ? AND archived = 0", (spec["name"],)
            ).fetchone()
            if row is None:
                continue
            dose = spec.get("dose")
            conn.execute(
                "INSERT INTO challenge_items (label, sort_order, archived, item_type, supplement_id, dose) "
                "VALUES (?, ?, 0, 'supplement', ?, ?)",
                (f"{spec['name']}{' · ' + dose if dose else ''}", order, row["id"], dose),
            )
        else:
            row = conn.execute(
                "SELECT id FROM exercises WHERE name = ? AND archived = 0", (spec["name"],)
            ).fetchone()
            if row is None:
                continue
            reps = spec.get("target_reps")
            conn.execute(
                "INSERT INTO challenge_items (label, sort_order, archived, item_type, exercise_id, target_reps) "
                "VALUES (?, ?, 0, 'exercise', ?, ?)",
                (f"{spec['name']}{' × ' + str(reps) if reps else ''}", order, row["id"], reps),
            )


# --- Notifications (Home Assistant Supervisor Core API) ---


def _ha_api_request(method, path, payload=None, timeout=5):
    if not SUPERVISOR_TOKEN:
        return None, "SUPERVISOR_TOKEN not set (not running under Supervisor)"
    req = urllib.request.Request(f"{HA_API_BASE}{path}", method=method)
    req.add_header("Authorization", f"Bearer {SUPERVISOR_TOKEN}")
    req.add_header("Content-Type", "application/json")
    data = json.dumps(payload).encode() if payload is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
            body = resp.read()
            return (json.loads(body) if body else None), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')}"
    except urllib.error.URLError as e:
        return None, f"connection error: {e.reason}"
    except Exception as e:  # noqa: BLE001 - never let a notify failure crash a caller
        return None, str(e)


def send_notification(message, title="Gym Tracker"):
    service = get_reminders_config()["notify_service"]
    if not service:
        return False, "no notify service configured"
    _, err = _ha_api_request(
        "POST", f"/services/notify/{service}", {"message": message, "title": title}
    )
    return err is None, err


def get_notify_services():
    data, err = _ha_api_request("GET", "/services")
    if err or not data:
        return [], err
    for entry in data:
        if entry.get("domain") == "notify":
            return sorted(entry.get("services", {}).keys()), None
    return [], None


def _parse_hhmm(value):
    try:
        hh, mm = value.split(":")
        return dtime(int(hh), int(mm))
    except (ValueError, AttributeError, TypeError):
        return None


def _opt_int(data, key):
    """Optional integer field: None/'' -> None; bad value raises ValueError(key)."""
    val = data.get(key)
    if val is None or val == "":
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        raise ValueError(key)


def _opt_float(data, key):
    val = data.get(key)
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        raise ValueError(key)


def _resolve_ts(day):
    """Turn an optional YYYY-MM-DD into a stored timestamp. Returns
    (ts, None) or (None, error-message)."""
    day = (day or "").strip()
    if not day:
        return datetime.now().isoformat(timespec="seconds"), None
    try:
        date.fromisoformat(day)
    except ValueError:
        return None, "date must be YYYY-MM-DD"
    return f"{day}T12:00:00", None


# --- Domain helpers ---


def _get_goal(conn):
    row = conn.execute("SELECT * FROM goal WHERE id = 1").fetchone()
    return dict(row) if row else dict(SEED_GOAL, id=1)


def _weight_progress(conn):
    """Everything the home screen needs to render goal progress, derived
    from the weight log plus the goal row."""
    goal = _get_goal(conn)
    logs = [
        dict(r)
        for r in conn.execute(
            "SELECT id, ts, weight_kg, body_fat_pct, notes FROM weight_logs ORDER BY ts ASC, id ASC"
        )
    ]
    latest = logs[-1] if logs else None
    current_weight = latest["weight_kg"] if latest else goal.get("start_weight_kg")
    latest_bf = next(
        (l["body_fat_pct"] for l in reversed(logs) if l["body_fat_pct"] is not None), None
    )
    first_bf = next((l["body_fat_pct"] for l in logs if l["body_fat_pct"] is not None), None)

    lean_mass = (
        round(current_weight * (1 - latest_bf / 100.0), 1)
        if current_weight is not None and latest_bf is not None
        else None
    )

    def pct(start, current, target):
        if start is None or current is None or target is None or start == target:
            return None
        return max(0, min(100, round((current - start) / (target - start) * 100, 1)))

    weight_progress = pct(goal.get("start_weight_kg"), current_weight, goal.get("target_weight_kg"))
    bf_progress = pct(first_bf, latest_bf, goal.get("target_body_fat_pct"))

    days_remaining = None
    if goal.get("target_date"):
        try:
            days_remaining = (date.fromisoformat(goal["target_date"]) - date.today()).days
        except ValueError:
            days_remaining = None

    return {
        "goal": goal,
        "current_weight_kg": current_weight,
        "current_body_fat_pct": latest_bf,
        "lean_mass_kg": lean_mass,
        "weight_to_target_kg": (
            round(goal["target_weight_kg"] - current_weight, 1)
            if current_weight is not None and goal.get("target_weight_kg") is not None
            else None
        ),
        "weight_progress_pct": weight_progress,
        "body_fat_progress_pct": bf_progress,
        "days_remaining": days_remaining,
        "forecast": _weight_forecast(logs, goal),
        "logs": logs,
    }


def _weight_forecast(logs, goal):
    """Least-squares linear trend over the logged weights, projected to the
    goal date — enough to answer 'am I on track?' without any heavy stats
    dependency. Weekly weigh-ins are sparse and roughly linear over a few
    months, so a straight line is the honest model here."""
    target_weight = goal.get("target_weight_kg")
    target_date_str = goal.get("target_date")
    points = []
    for l in logs:
        try:
            d = date.fromisoformat(l["ts"][:10])
        except (ValueError, TypeError):
            continue
        points.append((d, l["weight_kg"]))
    # Need at least two weigh-ins on different days to define a trend.
    if len(points) < 2 or target_weight is None or not target_date_str:
        return {"status": "insufficient", "available": False}
    try:
        target_date = date.fromisoformat(target_date_str)
    except ValueError:
        return {"status": "insufficient", "available": False}

    d0 = points[0][0]
    xs = [(d - d0).days for d, _ in points]
    ys = [w for _, w in points]
    n = len(xs)
    xbar = sum(xs) / n
    ybar = sum(ys) / n
    sxx = sum((x - xbar) ** 2 for x in xs)
    if sxx == 0:  # all weigh-ins on the same day
        return {"status": "insufficient", "available": False}
    slope = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys)) / sxx
    intercept = ybar - slope * xbar

    def fit(d):
        return intercept + slope * (d - d0).days

    current_weight = ys[-1]
    start_weight = goal.get("start_weight_kg")
    if start_weight is None:
        start_weight = ys[0]
    # Direction of improvement: bulking (target above start) vs cutting.
    direction = 1.0 if target_weight >= start_weight else -1.0

    projected = fit(target_date)
    # How far the projection lands past the target, measured the "good" way.
    signed_margin = (projected - target_weight) * direction

    if slope * direction < 0:
        status = "off_track"  # trending away from the target
    elif signed_margin >= 0.3:
        status = "ahead"
    elif signed_margin <= -0.3:
        status = "behind"
    else:
        status = "on_track"

    # When the trend line reaches the target (if it does, in the future).
    projected_date = None
    if slope != 0:
        cross_day = (target_weight - intercept) / slope
        cross = d0 + timedelta(days=round(cross_day))
        if (cross - date.today()).days >= 0 and slope * direction > 0:
            projected_date = cross.isoformat()

    today = date.today()
    days_left = (target_date - today).days
    required_per_week = None
    if days_left > 0:
        required_per_week = round((target_weight - current_weight) / (days_left / 7.0), 2)

    return {
        "available": True,
        "status": status,
        "slope_per_week": round(slope * 7, 2),
        "required_per_week": required_per_week,
        "projected_weight_kg": round(projected, 1),
        "projected_date": projected_date,
        # Two points for drawing the trend line across the chart.
        "trend": [
            {"ts": d0.isoformat(), "weight_kg": round(fit(d0), 1)},
            {"ts": target_date.isoformat(), "weight_kg": round(projected, 1)},
        ],
    }


def _active_challenge_items(conn):
    rows = conn.execute(
        "SELECT ci.id, ci.sort_order, ci.item_type, ci.exercise_id, ci.supplement_id, "
        "ci.target_sets, ci.target_reps, ci.dose, ci.label AS stored_label, "
        "e.name AS exercise_name, s.name AS supplement_name "
        "FROM challenge_items ci "
        "LEFT JOIN exercises e ON e.id = ci.exercise_id "
        "LEFT JOIN supplements s ON s.id = ci.supplement_id "
        "WHERE ci.archived = 0 ORDER BY ci.sort_order ASC, ci.id ASC"
    ).fetchall()
    return [_challenge_item_view(r) for r in rows]


def _exercise_target_text(sets, reps):
    if sets and reps:
        return f"{sets}×{reps}"
    if reps:
        return f"{reps} reps"
    if sets:
        return f"{sets} sets"
    return None


def _challenge_item_view(r):
    """Present a challenge-item row for the API — the display label uses the
    referenced library row's *live* name, so renaming an exercise or
    supplement updates the challenge everywhere."""
    item_type = r["item_type"]
    if item_type == "exercise":
        name = r["exercise_name"] or r["stored_label"]
        target = _exercise_target_text(r["target_sets"], r["target_reps"])
        label = f"{name}{' · ' + target if target else ''}"
    elif item_type == "supplement":
        name = r["supplement_name"] or r["stored_label"]
        label = f"{name}{' · ' + r['dose'] if r['dose'] else ''}"
    else:  # legacy/untyped fallback (should be archived, but stay safe)
        name = r["stored_label"]
        label = r["stored_label"]
    return {
        "id": r["id"],
        "sort_order": r["sort_order"],
        "item_type": item_type,
        "exercise_id": r["exercise_id"],
        "supplement_id": r["supplement_id"],
        "target_sets": r["target_sets"],
        "target_reps": r["target_reps"],
        "dose": r["dose"],
        "name": name,
        "label": label,
    }


def _completions_by_day(conn, item_ids):
    """{day: set(item_id)} for the given active items — the basis for both
    'is today done' and the streak."""
    if not item_ids:
        return {}
    placeholders = ",".join("?" for _ in item_ids)
    rows = conn.execute(
        f"SELECT day, item_id FROM challenge_completions WHERE item_id IN ({placeholders})",
        tuple(item_ids),
    )
    by_day = defaultdict(set)
    for r in rows:
        by_day[r["day"]].add(r["item_id"])
    return by_day


def _challenge_streak(conn):
    items = _active_challenge_items(conn)
    if not items:
        return 0
    active = {i["id"] for i in items}
    by_day = _completions_by_day(conn, list(active))

    def complete(day):
        return active <= by_day.get(day, set())

    today = date.today()
    # An unfinished today shouldn't break yesterday's streak — start
    # counting from today only if it's already complete, else from yesterday.
    cursor = today if complete(today.isoformat()) else today - timedelta(days=1)
    streak = 0
    while complete(cursor.isoformat()):
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def _challenge_complete_on(conn, day_iso):
    items = _active_challenge_items(conn)
    if not items:
        return False
    active = {i["id"] for i in items}
    by_day = _completions_by_day(conn, list(active))
    return active <= by_day.get(day_iso, set())


def _weighed_in_on(conn, day_iso):
    row = conn.execute(
        "SELECT 1 FROM weight_logs WHERE substr(ts, 1, 10) = ? LIMIT 1", (day_iso,)
    ).fetchone()
    return row is not None


# --- Routes: pages ---


@app.route("/")
def index():
    db = get_db()
    reminders = get_reminders_config()
    return render_template(
        "index.html",
        app_version=APP_VERSION,
        goal=_get_goal(db),
        challenge_reminder_enabled=reminders["challenge_enabled"],
        weighin_reminder_enabled=reminders["weighin_enabled"],
    )


# --- Routes: weight ---


@app.route("/api/weight")
def api_weight():
    return jsonify(_weight_progress(get_db()))


@app.route("/api/weight", methods=["POST"])
def api_add_weight():
    data = request.get_json(force=True, silent=True) or {}
    try:
        weight = float(data.get("weight_kg"))
    except (TypeError, ValueError):
        return jsonify({"error": "weight_kg is required and must be a number"}), 400
    if not (0 < weight < 700):
        return jsonify({"error": "weight_kg out of range"}), 400
    body_fat = data.get("body_fat_pct")
    if body_fat is not None and body_fat != "":
        try:
            body_fat = float(body_fat)
        except (TypeError, ValueError):
            return jsonify({"error": "body_fat_pct must be a number"}), 400
        if not (0 <= body_fat <= 100):
            return jsonify({"error": "body_fat_pct out of range"}), 400
    else:
        body_fat = None
    day = (data.get("date") or "").strip()
    if day:
        try:
            date.fromisoformat(day)
            ts = f"{day}T12:00:00"
        except ValueError:
            return jsonify({"error": "date must be YYYY-MM-DD"}), 400
    else:
        ts = datetime.now().isoformat(timespec="seconds")
    notes = (data.get("notes") or "").strip() or None

    db = get_db()
    cur = db.execute(
        "INSERT INTO weight_logs (ts, weight_kg, body_fat_pct, notes) VALUES (?, ?, ?, ?)",
        (ts, weight, body_fat, notes),
    )
    db.commit()
    return jsonify({"status": "created", "id": cur.lastrowid}), 201


@app.route("/api/weight/<int:log_id>", methods=["PUT"])
def api_update_weight(log_id):
    data = request.get_json(force=True, silent=True) or {}
    db = get_db()
    if db.execute("SELECT 1 FROM weight_logs WHERE id = ?", (log_id,)).fetchone() is None:
        return jsonify({"error": "no such weight log"}), 404
    try:
        weight = float(data.get("weight_kg"))
    except (TypeError, ValueError):
        return jsonify({"error": "weight_kg is required and must be a number"}), 400
    if not (0 < weight < 700):
        return jsonify({"error": "weight_kg out of range"}), 400
    body_fat = data.get("body_fat_pct")
    if body_fat is not None and body_fat != "":
        try:
            body_fat = float(body_fat)
        except (TypeError, ValueError):
            return jsonify({"error": "body_fat_pct must be a number"}), 400
    else:
        body_fat = None
    notes = (data.get("notes") or "").strip() or None
    db.execute(
        "UPDATE weight_logs SET weight_kg = ?, body_fat_pct = ?, notes = ? WHERE id = ?",
        (weight, body_fat, notes, log_id),
    )
    db.commit()
    return jsonify({"status": "updated"})


@app.route("/api/weight/<int:log_id>", methods=["DELETE"])
def api_delete_weight(log_id):
    db = get_db()
    cur = db.execute("DELETE FROM weight_logs WHERE id = ?", (log_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "no such weight log"}), 404
    return "", 204


# --- Routes: goal ---


@app.route("/api/goal")
def api_goal():
    return jsonify(_get_goal(get_db()))


@app.route("/api/goal", methods=["PUT"])
def api_update_goal():
    data = request.get_json(force=True, silent=True) or {}
    try:
        target_weight = float(data.get("target_weight_kg"))
        target_bf = float(data.get("target_body_fat_pct"))
    except (TypeError, ValueError):
        return jsonify({"error": "target_weight_kg and target_body_fat_pct must be numbers"}), 400
    target_date = (data.get("target_date") or "").strip()
    try:
        date.fromisoformat(target_date)
    except ValueError:
        return jsonify({"error": "target_date must be YYYY-MM-DD"}), 400

    db = get_db()
    goal = _get_goal(db)
    # Start date/weight are optional in the payload; keep the existing seed
    # values when the client only edits the target.
    start_date = (data.get("start_date") or goal.get("start_date") or "").strip() or None
    start_weight = data.get("start_weight_kg", goal.get("start_weight_kg"))
    try:
        start_weight = float(start_weight) if start_weight is not None else None
    except (TypeError, ValueError):
        return jsonify({"error": "start_weight_kg must be a number"}), 400

    db.execute(
        "INSERT INTO goal (id, target_date, target_weight_kg, target_body_fat_pct, start_date, start_weight_kg) "
        "VALUES (1, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET target_date = excluded.target_date, "
        "target_weight_kg = excluded.target_weight_kg, target_body_fat_pct = excluded.target_body_fat_pct, "
        "start_date = excluded.start_date, start_weight_kg = excluded.start_weight_kg",
        (target_date, target_weight, target_bf, start_date, start_weight),
    )
    db.commit()
    return jsonify({"status": "updated", "goal": _get_goal(db)})


# --- Routes: exercises + workouts ---


@app.route("/api/exercises")
def api_exercises():
    db = get_db()
    rows = [
        dict(r)
        for r in db.execute(
            "SELECT id, name, equipment, category, is_custom, notes FROM exercises "
            "WHERE archived = 0 ORDER BY equipment ASC, name ASC"
        )
    ]
    groups = defaultdict(list)
    for r in rows:
        groups[r["equipment"]].append(r)
    ordered = [eq for eq in EQUIPMENT_ORDER if eq in groups]
    ordered += sorted(eq for eq in groups if eq not in EQUIPMENT_ORDER)
    return jsonify([{"equipment": eq, "exercises": groups[eq]} for eq in ordered])


@app.route("/api/exercises", methods=["POST"])
def api_add_exercise():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    equipment = (data.get("equipment") or "Bodyweight").strip() or "Bodyweight"
    category = (data.get("category") or "").strip() or None
    db = get_db()
    cur = db.execute(
        "INSERT INTO exercises (name, equipment, category, is_custom, archived) VALUES (?, ?, ?, 1, 0)",
        (name, equipment, category),
    )
    db.commit()
    return jsonify({"status": "created", "id": cur.lastrowid}), 201


@app.route("/api/exercises/<int:exercise_id>", methods=["PUT"])
def api_update_exercise(exercise_id):
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    equipment = (data.get("equipment") or "Bodyweight").strip() or "Bodyweight"
    category = (data.get("category") or "").strip() or None
    db = get_db()
    cur = db.execute(
        "UPDATE exercises SET name = ?, equipment = ?, category = ? WHERE id = ?",
        (name, equipment, category, exercise_id),
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "no such exercise"}), 404
    return jsonify({"status": "updated"})


@app.route("/api/exercises/<int:exercise_id>", methods=["DELETE"])
def api_delete_exercise(exercise_id):
    db = get_db()
    if db.execute("SELECT 1 FROM exercises WHERE id = ?", (exercise_id,)).fetchone() is None:
        return jsonify({"error": "no such exercise"}), 404
    # Preserve history and challenge links: archive an exercise that's been
    # logged against or referenced by a challenge item; hard-delete an
    # otherwise-unused one.
    used = db.execute(
        "SELECT 1 FROM workout_logs WHERE exercise_id = ? LIMIT 1", (exercise_id,)
    ).fetchone() or db.execute(
        "SELECT 1 FROM challenge_items WHERE exercise_id = ? AND archived = 0 LIMIT 1", (exercise_id,)
    ).fetchone()
    if used:
        db.execute("UPDATE exercises SET archived = 1 WHERE id = ?", (exercise_id,))
        result = "archived"
    else:
        db.execute("DELETE FROM exercises WHERE id = ?", (exercise_id,))
        result = "deleted"
    db.commit()
    return jsonify({"status": result})


# --- Routes: supplements ---


@app.route("/api/supplements")
def api_supplements():
    db = get_db()
    return jsonify(
        [
            dict(r)
            for r in db.execute(
                "SELECT id, name, dose, is_custom FROM supplements WHERE archived = 0 ORDER BY name ASC"
            )
        ]
    )


@app.route("/api/supplements", methods=["POST"])
def api_add_supplement():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    dose = (data.get("dose") or "").strip() or None
    db = get_db()
    cur = db.execute(
        "INSERT INTO supplements (name, dose, is_custom, archived) VALUES (?, ?, 1, 0)", (name, dose)
    )
    db.commit()
    return jsonify({"status": "created", "id": cur.lastrowid}), 201


@app.route("/api/supplements/<int:supplement_id>", methods=["PUT"])
def api_update_supplement(supplement_id):
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    dose = (data.get("dose") or "").strip() or None
    db = get_db()
    cur = db.execute(
        "UPDATE supplements SET name = ?, dose = ? WHERE id = ?", (name, dose, supplement_id)
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "no such supplement"}), 404
    return jsonify({"status": "updated"})


@app.route("/api/supplements/<int:supplement_id>", methods=["DELETE"])
def api_delete_supplement(supplement_id):
    db = get_db()
    if db.execute("SELECT 1 FROM supplements WHERE id = ?", (supplement_id,)).fetchone() is None:
        return jsonify({"error": "no such supplement"}), 404
    # Archive if a challenge item references it, else hard-delete.
    used = db.execute(
        "SELECT 1 FROM challenge_items WHERE supplement_id = ? AND archived = 0 LIMIT 1", (supplement_id,)
    ).fetchone()
    if used:
        db.execute("UPDATE supplements SET archived = 1 WHERE id = ?", (supplement_id,))
        result = "archived"
    else:
        db.execute("DELETE FROM supplements WHERE id = ?", (supplement_id,))
        result = "deleted"
    db.commit()
    return jsonify({"status": result})


@app.route("/api/workouts")
def api_workouts():
    db = get_db()
    clauses, params = [], []
    exercise_id = request.args.get("exercise_id")
    if exercise_id:
        clauses.append("w.exercise_id = ?")
        params.append(exercise_id)
    day = request.args.get("date")
    if day:
        clauses.append("substr(w.ts, 1, 10) = ?")
        params.append(day)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = [
        dict(r)
        for r in db.execute(
            "SELECT w.id, w.ts, w.exercise_id, w.sets, w.reps, w.weight_kg, w.duration_sec, "
            "w.notes, w.source, e.name AS exercise_name, e.equipment "
            f"FROM workout_logs w JOIN exercises e ON e.id = w.exercise_id {where} "
            "ORDER BY w.ts DESC, w.id DESC LIMIT 200",
            tuple(params),
        )
    ]
    return jsonify(rows)


@app.route("/api/workouts", methods=["POST"])
def api_add_workout():
    data = request.get_json(force=True, silent=True) or {}
    try:
        exercise_id = int(data.get("exercise_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "exercise_id is required"}), 400
    db = get_db()
    if db.execute("SELECT 1 FROM exercises WHERE id = ?", (exercise_id,)).fetchone() is None:
        return jsonify({"error": "no such exercise"}), 400

    try:
        sets, reps, duration = _opt_int(data, "sets"), _opt_int(data, "reps"), _opt_int(data, "duration_sec")
        weight = _opt_float(data, "weight_kg")
    except ValueError as e:
        return jsonify({"error": f"{e} must be a number"}), 400
    ts, err = _resolve_ts(data.get("date"))
    if err:
        return jsonify({"error": err}), 400
    notes = (data.get("notes") or "").strip() or None

    cur = db.execute(
        "INSERT INTO workout_logs (ts, exercise_id, sets, reps, weight_kg, duration_sec, notes, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'manual')",
        (ts, exercise_id, sets, reps, weight, duration, notes),
    )
    db.commit()
    return jsonify({"status": "created", "id": cur.lastrowid}), 201


@app.route("/api/workouts/<int:workout_id>", methods=["PUT"])
def api_update_workout(workout_id):
    data = request.get_json(force=True, silent=True) or {}
    db = get_db()
    existing = db.execute("SELECT ts FROM workout_logs WHERE id = ?", (workout_id,)).fetchone()
    if existing is None:
        return jsonify({"error": "no such workout"}), 404
    try:
        sets, reps, duration = _opt_int(data, "sets"), _opt_int(data, "reps"), _opt_int(data, "duration_sec")
        weight = _opt_float(data, "weight_kg")
    except ValueError as e:
        return jsonify({"error": f"{e} must be a number"}), 400
    # Keep the existing timestamp when no date is supplied.
    if (data.get("date") or "").strip():
        ts, err = _resolve_ts(data.get("date"))
        if err:
            return jsonify({"error": err}), 400
    else:
        ts = existing["ts"]
    notes = (data.get("notes") or "").strip() or None
    db.execute(
        "UPDATE workout_logs SET ts = ?, sets = ?, reps = ?, weight_kg = ?, duration_sec = ?, notes = ? "
        "WHERE id = ?",
        (ts, sets, reps, weight, duration, notes, workout_id),
    )
    db.commit()
    return jsonify({"status": "updated"})


@app.route("/api/workouts/<int:workout_id>", methods=["DELETE"])
def api_delete_workout(workout_id):
    db = get_db()
    cur = db.execute("DELETE FROM workout_logs WHERE id = ?", (workout_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "no such workout"}), 404
    return "", 204


# --- Routes: daily challenge ---


@app.route("/api/challenge")
def api_challenge():
    db = get_db()
    items = _active_challenge_items(db)
    active_ids = [i["id"] for i in items]
    by_day = _completions_by_day(db, active_ids)
    today = date.today()
    today_iso = today.isoformat()
    done_today = by_day.get(today_iso, set())
    for i in items:
        i["done_today"] = i["id"] in done_today

    last_7 = []
    for offset in range(6, -1, -1):
        d = (today - timedelta(days=offset)).isoformat()
        complete = bool(active_ids) and set(active_ids) <= by_day.get(d, set())
        last_7.append({"day": d, "complete": complete})

    return jsonify(
        {
            "today": today_iso,
            "items": items,
            "streak": _challenge_streak(db),
            "complete_today": bool(active_ids) and set(active_ids) <= done_today,
            "last_7_days": last_7,
        }
    )


@app.route("/api/challenge/toggle", methods=["POST"])
def api_challenge_toggle():
    data = request.get_json(force=True, silent=True) or {}
    try:
        item_id = int(data.get("item_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "item_id is required"}), 400
    db = get_db()
    item = db.execute(
        "SELECT id, item_type, exercise_id, target_sets, target_reps "
        "FROM challenge_items WHERE id = ? AND archived = 0",
        (item_id,),
    ).fetchone()
    if item is None:
        return jsonify({"error": "no such challenge item"}), 404
    day = (data.get("day") or "").strip() or date.today().isoformat()
    try:
        date.fromisoformat(day)
    except ValueError:
        return jsonify({"error": "day must be YYYY-MM-DD"}), 400

    existing = db.execute(
        "SELECT id FROM challenge_completions WHERE item_id = ? AND day = ?", (item_id, day)
    ).fetchone()
    if existing:
        db.execute("DELETE FROM challenge_completions WHERE id = ?", (existing["id"],))
        done = False
    else:
        db.execute(
            "INSERT INTO challenge_completions (item_id, day) VALUES (?, ?)", (item_id, day)
        )
        done = True

    # An exercise item ticked off also lands in the workout log (source
    # 'challenge'); un-ticking removes that auto-created entry so history
    # stays in sync. Manual workout entries are never touched.
    if item["item_type"] == "exercise" and item["exercise_id"]:
        if done:
            db.execute(
                "INSERT INTO workout_logs (ts, exercise_id, sets, reps, source, challenge_item_id) "
                "VALUES (?, ?, ?, ?, 'challenge', ?)",
                (f"{day}T12:00:00", item["exercise_id"], item["target_sets"], item["target_reps"], item_id),
            )
        else:
            db.execute(
                "DELETE FROM workout_logs WHERE source = 'challenge' AND challenge_item_id = ? "
                "AND substr(ts, 1, 10) = ?",
                (item_id, day),
            )
    db.commit()
    return jsonify({"status": "ok", "done": done, "streak": _challenge_streak(db)})


@app.route("/api/challenge/items", methods=["GET"])
def api_challenge_items():
    return jsonify(_active_challenge_items(get_db()))


@app.route("/api/challenge/items", methods=["POST"])
def api_add_challenge_item():
    """Add a typed challenge item — a reference to an exercise (with a
    sets/reps target) or a supplement (with a dose). Free text is not
    accepted, so the challenge always links back to a clean library row."""
    data = request.get_json(force=True, silent=True) or {}
    item_type = (data.get("item_type") or "").strip()
    if item_type not in ("exercise", "supplement"):
        return jsonify({"error": "item_type must be 'exercise' or 'supplement'"}), 400
    db = get_db()
    try:
        target_sets, target_reps = _opt_int(data, "target_sets"), _opt_int(data, "target_reps")
    except ValueError as e:
        return jsonify({"error": f"{e} must be a number"}), 400
    next_order = db.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM challenge_items"
    ).fetchone()[0]

    if item_type == "exercise":
        try:
            exercise_id = int(data.get("exercise_id"))
        except (TypeError, ValueError):
            return jsonify({"error": "exercise_id is required"}), 400
        ex = db.execute(
            "SELECT name FROM exercises WHERE id = ? AND archived = 0", (exercise_id,)
        ).fetchone()
        if ex is None:
            return jsonify({"error": "no such exercise"}), 400
        label = f"{ex['name']}"
        target = _exercise_target_text(target_sets, target_reps)
        if target:
            label += f" · {target}"
        cur = db.execute(
            "INSERT INTO challenge_items (label, sort_order, archived, item_type, exercise_id, "
            "target_sets, target_reps) VALUES (?, ?, 0, 'exercise', ?, ?, ?)",
            (label, next_order, exercise_id, target_sets, target_reps),
        )
    else:
        try:
            supplement_id = int(data.get("supplement_id"))
        except (TypeError, ValueError):
            return jsonify({"error": "supplement_id is required"}), 400
        sup = db.execute(
            "SELECT name, dose FROM supplements WHERE id = ? AND archived = 0", (supplement_id,)
        ).fetchone()
        if sup is None:
            return jsonify({"error": "no such supplement"}), 400
        dose = (data.get("dose") or "").strip() or sup["dose"]
        label = f"{sup['name']}{' · ' + dose if dose else ''}"
        cur = db.execute(
            "INSERT INTO challenge_items (label, sort_order, archived, item_type, supplement_id, dose) "
            "VALUES (?, ?, 0, 'supplement', ?, ?)",
            (label, next_order, supplement_id, dose),
        )
    db.commit()
    return jsonify({"status": "created", "id": cur.lastrowid}), 201


@app.route("/api/challenge/items/<int:item_id>", methods=["PUT"])
def api_update_challenge_item(item_id):
    """Edit an item's target (exercise) or dose (supplement). The referenced
    library row itself is changed in the exercises/supplements library."""
    data = request.get_json(force=True, silent=True) or {}
    db = get_db()
    item = db.execute(
        "SELECT * FROM challenge_items WHERE id = ? AND archived = 0", (item_id,)
    ).fetchone()
    if item is None:
        return jsonify({"error": "no such challenge item"}), 404
    try:
        target_sets, target_reps = _opt_int(data, "target_sets"), _opt_int(data, "target_reps")
    except ValueError as e:
        return jsonify({"error": f"{e} must be a number"}), 400

    if item["item_type"] == "exercise":
        name = db.execute("SELECT name FROM exercises WHERE id = ?", (item["exercise_id"],)).fetchone()
        target = _exercise_target_text(target_sets, target_reps)
        label = f"{name['name'] if name else 'Exercise'}{' · ' + target if target else ''}"
        db.execute(
            "UPDATE challenge_items SET target_sets = ?, target_reps = ?, label = ? WHERE id = ?",
            (target_sets, target_reps, label, item_id),
        )
    else:
        name = db.execute("SELECT name FROM supplements WHERE id = ?", (item["supplement_id"],)).fetchone()
        dose = (data.get("dose") or "").strip() or None
        label = f"{name['name'] if name else 'Supplement'}{' · ' + dose if dose else ''}"
        db.execute(
            "UPDATE challenge_items SET dose = ?, label = ? WHERE id = ?", (dose, label, item_id)
        )
    db.commit()
    return jsonify({"status": "updated"})


@app.route("/api/challenge/history")
def api_challenge_history():
    """A per-day completion matrix over the recent past, for backfilling or
    correcting the streak. Newest day first."""
    db = get_db()
    try:
        days = max(1, min(60, int(request.args.get("days", 14))))
    except (TypeError, ValueError):
        days = 14
    items = _active_challenge_items(db)
    active_ids = [i["id"] for i in items]
    by_day = _completions_by_day(db, active_ids)
    today = date.today()
    history = []
    for offset in range(days):
        d = (today - timedelta(days=offset)).isoformat()
        done = by_day.get(d, set())
        history.append(
            {
                "day": d,
                "done": [i["id"] for i in items if i["id"] in done],
                "complete": bool(active_ids) and set(active_ids) <= done,
            }
        )
    return jsonify({"items": items, "days": history})


@app.route("/api/challenge/items/<int:item_id>", methods=["DELETE"])
def api_delete_challenge_item(item_id):
    db = get_db()
    # Archive rather than delete so past streak/history stays intact.
    cur = db.execute(
        "UPDATE challenge_items SET archived = 1 WHERE id = ? AND archived = 0", (item_id,)
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "no such challenge item"}), 404
    return jsonify({"status": "archived"})


# --- Routes: reminders / notifications ---


@app.route("/api/reminders")
def api_reminders():
    db = get_db()
    cfg = get_reminders_config()
    return jsonify(
        {
            "notify_service": cfg["notify_service"],
            "challenge": {
                "enabled": cfg["challenge_enabled"],
                "time": cfg["challenge_time"],
                "last_sent": _get_app_state(db, "challenge_reminder_last_sent"),
            },
            "weighin": {
                "enabled": cfg["weighin_enabled"],
                "weekday": cfg["weighin_weekday"],
                "time": cfg["weighin_time"],
                "last_sent": _get_app_state(db, "weighin_reminder_last_sent"),
            },
        }
    )


@app.route("/api/notify-services")
def api_notify_services():
    services, err = get_notify_services()
    return jsonify({"services": services, "error": err})


@app.route("/api/notify-test", methods=["POST"])
def api_notify_test():
    data = request.get_json(force=True, silent=True) or {}
    message = (data.get("message") or "").strip() or "Test notification from Gym Tracker 💪"
    ok, err = send_notification(message, title="Gym Tracker test")
    if ok:
        return jsonify({"status": "sent"})
    return jsonify({"status": "failed", "error": err}), 502


# --- Routes: backup / restore ---


@app.route("/api/backup")
def api_backup():
    db = get_db()
    db.commit()
    filename = f"gym-tracker-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    return send_file(DB_PATH, as_attachment=True, download_name=filename)


def _is_valid_backup(path):
    try:
        conn = sqlite3.connect(path)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        conn.close()
    except sqlite3.Error:
        return False
    return {"weight_logs", "challenge_items", "exercises"}.issubset(tables)


@app.route("/api/restore", methods=["POST"])
def api_restore():
    uploaded = request.files.get("file")
    if uploaded is None or uploaded.filename == "":
        return jsonify({"error": "no file provided"}), 400
    tmp_path = DB_PATH + ".upload"
    uploaded.save(tmp_path)
    if not _is_valid_backup(tmp_path):
        os.remove(tmp_path)
        return jsonify({"error": "not a valid Gym Tracker backup file"}), 400
    close_db()
    os.replace(tmp_path, DB_PATH)
    init_db()  # backfill any columns/seeds added since the backup was taken
    return jsonify({"status": "restored"}), 200


# --- Routes: debug ---


@app.route("/api/debug")
def api_debug():
    now = datetime.now()
    db = get_db()
    db_ok, db_error = True, None
    try:
        db.execute("SELECT COUNT(*) FROM weight_logs").fetchone()
    except sqlite3.Error as e:
        db_ok, db_error = False, str(e)
    ha_config, ha_error = _ha_api_request("GET", "/config")
    cfg = get_reminders_config()
    return jsonify(
        {
            "app_version": APP_VERSION,
            "container_time": now.isoformat(),
            "container_timezone": time.tzname,
            "supervisor_token_set": bool(SUPERVISOR_TOKEN),
            "ha_api_reachable": ha_error is None,
            "ha_api_error": ha_error,
            "ha_time_zone": (ha_config or {}).get("time_zone") if ha_config else None,
            "options_path": OPTIONS_PATH,
            "options_path_exists": os.path.exists(OPTIONS_PATH),
            "db_path": DB_PATH,
            "db_ok": db_ok,
            "db_error": db_error,
            "reminders": cfg,
            "challenge_reminder_last_sent": _get_app_state(db, "challenge_reminder_last_sent"),
            "weighin_reminder_last_sent": _get_app_state(db, "weighin_reminder_last_sent"),
            "python_version": sys.version.split()[0],
            "flask_version": importlib.metadata.version("flask"),
            "platform": platform.platform(),
            "ingress_user_id": request.headers.get(INGRESS_USER_ID_HEADER),
            "access_restricted": bool(get_allowed_user_ids()),
        }
    )


# --- Background reminder loop ---


def _challenge_reminder_tick(now, conn):
    cfg = get_reminders_config()
    if not (cfg["challenge_enabled"] and cfg["notify_service"]):
        return
    target = _parse_hhmm(cfg["challenge_time"])
    if target is None or now.time() < target:
        return
    today_iso = now.date().isoformat()
    if _get_app_state(conn, "challenge_reminder_last_sent") == today_iso:
        return  # already evaluated today
    _set_app_state(conn, "challenge_reminder_last_sent", today_iso)
    # Only nag if the challenge isn't already fully done for the day.
    if not _challenge_complete_on(conn, today_iso):
        send_notification(
            "Daily challenge time — knock out your creatine, push-ups and squats 💪",
            title="Gym Tracker",
        )


def _weighin_reminder_tick(now, conn):
    cfg = get_reminders_config()
    if not (cfg["weighin_enabled"] and cfg["notify_service"]):
        return
    if cfg["weighin_weekday"] not in WEEKDAYS or WEEKDAYS.index(cfg["weighin_weekday"]) != now.weekday():
        return  # not the configured weekday — leave the guard untouched for next week
    target = _parse_hhmm(cfg["weighin_time"])
    if target is None or now.time() < target:
        return
    today_iso = now.date().isoformat()
    if _get_app_state(conn, "weighin_reminder_last_sent") == today_iso:
        return
    _set_app_state(conn, "weighin_reminder_last_sent", today_iso)
    # Skip if they already stepped on the scale today.
    if not _weighed_in_on(conn, today_iso):
        send_notification(
            "Weekly weigh-in — log your weight in Gym Tracker.", title="Gym Tracker"
        )


def _background_loop():
    if not SUPERVISOR_TOKEN:
        _log("SUPERVISOR_TOKEN not set; reminders disabled (local/dev mode)")
        return
    while True:
        try:
            conn = _db_connect_standalone()
            try:
                now = datetime.now()
                _challenge_reminder_tick(now, conn)
                _weighin_reminder_tick(now, conn)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - keep the loop alive across any single failure
            app.logger.exception("background loop iteration failed")
        time.sleep(60)


# --- Shutdown + entrypoint ---


_shutdown_signal_at = None


def _handle_shutdown_signal(signum, frame):
    global _shutdown_signal_at
    _shutdown_signal_at = time.monotonic()
    _log(f"received signal {signum}, shutting down")
    sys.exit(0)


if __name__ == "__main__":
    from waitress import serve

    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    init_db()
    _log(f"starting Gym Tracker {APP_VERSION}")
    threading.Thread(target=_background_loop, daemon=True).start()
    port = int(os.environ.get("GYM_PORT", "8099"))
    _log(f"serving on 0.0.0.0:{port} (waitress)")
    serve(app, host="0.0.0.0", port=port)
