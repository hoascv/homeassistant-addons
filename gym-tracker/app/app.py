import html
import importlib.metadata
import json
import os
import re
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

import garmin_client

APP_VERSION = "1.18.1"  # keep in sync with the "version" field in config.yaml

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
DEFAULT_CHALLENGE_NAME = "Daily challenge"
SEED_START_WEIGHT = 99.7
SEED_GOAL = {
    "target_date": "2026-12-28",
    "target_weight_kg": 105.0,
    "target_body_fat_pct": 15.0,
    "start_date": SEED_START_DATE,
    "start_weight_kg": SEED_START_WEIGHT,
}
# (name, dose_amount, dose_unit, quantity, timing, brand) — a small starter
# supplements library, editable. Quantity is units per serving (e.g. 2
# capsules); dose amount/unit is the size of one unit.
SEED_SUPPLEMENTS = [
    ("Creatine", 5, "g", 1, "Anytime", None),
    ("Protein powder", 30, "g", 1, "Post-workout", None),
]
# Suggested timing tags surfaced in the UI (free text still allowed).
SUPPLEMENT_TIMINGS = ["Morning", "Midday", "Pre-workout", "Post-workout", "Evening", "With meal", "Anytime"]
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


def get_garmin_config():
    opts = _read_options()
    try:
        interval = int(opts.get("garmin_sync_interval_hours", 6))
    except (TypeError, ValueError):
        interval = 6
    try:
        hr_window = int(opts.get("garmin_hr_window_minutes", 30))
    except (TypeError, ValueError):
        hr_window = 30
    try:
        backfill = int(opts.get("garmin_backfill_days", GARMIN_BACKFILL_DAYS))
    except (TypeError, ValueError):
        backfill = GARMIN_BACKFILL_DAYS
    return {
        "auto_sync": bool(opts.get("garmin_auto_sync", True)),
        "interval_hours": max(1, interval),
        # Window used for an entry that carries no duration of its own.
        "hr_window_minutes": min(180, max(5, hr_window)),
        # How far back holes are chased. Garmin keeps your history
        # indefinitely, so this is only a limit on how much we ask for.
        "backfill_days": min(730, max(7, backfill)),
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
            notes TEXT,
            device TEXT,
            ts_exact INTEGER NOT NULL DEFAULT 0
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
            notes TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS supplements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            dose TEXT,
            dose_amount REAL,
            dose_unit TEXT,
            quantity REAL,
            timing TEXT,
            brand TEXT,
            is_custom INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
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
            challenge_item_id INTEGER,
            ts_exact INTEGER NOT NULL DEFAULT 0
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
            dose TEXT,
            challenge_id INTEGER REFERENCES challenges(id),
            created_at TEXT,
            updated_at TEXT,
            archived_at TEXT,
            moved_from INTEGER REFERENCES challenge_items(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS challenge_completions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL REFERENCES challenge_items(id),
            day TEXT NOT NULL,
            -- When it was actually ticked, not just which day. Needed to line
            -- an exercise up with the heart rate Garmin recorded for it.
            ts TEXT,
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
    # A challenge is a named set of items you tick off daily. Several can run
    # at once, and one can be time-boxed: start_date/end_date bound the period
    # it counts over, so a 30-day challenge finishes rather than running on.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS challenges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT,
            updated_at TEXT,
            repeat_of INTEGER REFERENCES challenges(id),
            schedule_kind TEXT NOT NULL DEFAULT 'daily',
            schedule_interval INTEGER,
            schedule_weekdays TEXT
        )
        """
    )
    # Exercise pictures, kept out of the exercises table so the ordinary
    # queries never drag a blob around — and in the database rather than on
    # disk, because a backup is the database file: images saved beside it
    # would vanish on restore.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS exercise_images (
            exercise_id INTEGER PRIMARY KEY REFERENCES exercises(id),
            image BLOB NOT NULL,
            mime TEXT NOT NULL,
            updated_at TEXT
        )
        """
    )
    # Every version of the goal, appended. The goal itself is a single row
    # that edits overwrite, so without this a change to your target leaves no
    # trace that it ever changed.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS goal_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            changed_at TEXT NOT NULL,
            source TEXT NOT NULL,
            target_date TEXT,
            target_weight_kg REAL,
            target_body_fat_pct REAL,
            start_date TEXT,
            start_weight_kg REAL
        )
        """
    )
    # Garmin Connect: one wellness row per day (sleep / stress / Body Battery),
    # keyed by day so a re-sync upserts rather than duplicating.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS garmin_daily (
            day TEXT PRIMARY KEY,
            sleep_seconds INTEGER,
            sleep_deep_seconds INTEGER,
            sleep_light_seconds INTEGER,
            sleep_rem_seconds INTEGER,
            sleep_awake_seconds INTEGER,
            sleep_score INTEGER,
            stress_avg INTEGER,
            stress_max INTEGER,
            body_battery_high INTEGER,
            body_battery_low INTEGER,
            body_battery_charged INTEGER,
            body_battery_drained INTEGER,
            synced_at TEXT
        )
        """
    )
    # Days Garmin was asked about and had nothing for. Kept out of
    # garmin_daily on purpose: a day with no data must never be stored as a
    # day of zeros. Tracking the attempts here is what stops the backfill
    # re-asking about the same empty days forever.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS garmin_day_probe (
            day TEXT PRIMARY KEY,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_attempt TEXT,
            device_upload TEXT
        )
        """
    )
    # Garmin activities, keyed by Garmin's own activity id (natural dedup key).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS garmin_activities (
            activity_id INTEGER PRIMARY KEY,
            start_time TEXT,
            activity_type TEXT,
            name TEXT,
            duration_sec INTEGER,
            distance_m REAL,
            calories INTEGER,
            avg_hr INTEGER,
            max_hr INTEGER,
            synced_at TEXT
        )
        """
    )

    _migrate_columns(conn)
    _drop_placeholder_heart_rates(conn)
    _seed_defaults(conn)
    conn.commit()
    conn.close()


def _drop_placeholder_heart_rates(conn):
    """Clear heart rates that were read against a midday placeholder.

    Before ts_exact existed, an entry filed at {day}T12:00:00 had its heart
    rate read over 11:30–12:00 — a real measurement of the wrong window. Those
    readings sit in the resting range and are indistinguishable from correct
    ones once stored, which makes them worse than no reading at all. They can
    never be corrected either: the real time was never recorded.

    Runs once; entries logged since are unaffected, and the sync will not
    refill these because it requires ts_exact = 1.
    """
    if _get_app_state(conn, "placeholder_hr_cleared"):
        return
    cur = conn.execute(
        "UPDATE workout_logs SET hr_avg = NULL, hr_max = NULL, hr_min = NULL, hr_samples = NULL, "
        "hr_synced_at = NULL, hr_attempts = 0, hr_upload = NULL, "
        "session_start = NULL, session_end = NULL "
        "WHERE ts_exact = 0 AND hr_avg IS NOT NULL"
    )
    _set_app_state(conn, "placeholder_hr_cleared", datetime.now().isoformat(timespec="seconds"))
    if cur.rowcount:
        _log(f"cleared {cur.rowcount} heart rate(s) read from a placeholder timestamp")


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
    weight_cols = {row[1] for row in conn.execute("PRAGMA table_info(weight_logs)")}
    if "device" not in weight_cols:
        conn.execute("ALTER TABLE weight_logs ADD COLUMN device TEXT")
    if "ts_exact" not in weight_cols:
        conn.execute("ALTER TABLE weight_logs ADD COLUMN ts_exact INTEGER NOT NULL DEFAULT 0")
        # Every dated entry used to be stored at midday, so that is exactly the
        # set whose time is unknown; anything else was a real clock reading.
        conn.execute("UPDATE weight_logs SET ts_exact = 1 WHERE ts NOT LIKE '%T12:00:00'")
        # ...except the seeded starting weight, which is a stand-in rather than
        # a weigh-in anyone actually took at 08:00.
        conn.execute(
            "UPDATE weight_logs SET ts_exact = 0 WHERE ts = ? AND notes = 'Starting weight'",
            (f"{SEED_START_DATE}T08:00:00",),
        )

    workout_cols = {row[1] for row in conn.execute("PRAGMA table_info(workout_logs)")}
    if "source" not in workout_cols:
        conn.execute("ALTER TABLE workout_logs ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'")
    if "challenge_item_id" not in workout_cols:
        conn.execute("ALTER TABLE workout_logs ADD COLUMN challenge_item_id INTEGER")
    if "ts_exact" not in workout_cols:
        conn.execute("ALTER TABLE workout_logs ADD COLUMN ts_exact INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE workout_logs SET ts_exact = 1 WHERE ts NOT LIKE '%T12:00:00'")
    # Heart rate for the window the exercise was done in, filled from Garmin
    # once the watch has uploaded. hr_attempts / hr_upload bound the retries
    # the same way garmin_day_probe does for whole days.
    for col, decl in (
        ("hr_avg", "INTEGER"),
        ("hr_max", "INTEGER"),
        ("hr_min", "INTEGER"),
        ("hr_samples", "INTEGER"),
        ("hr_synced_at", "TEXT"),
        ("hr_attempts", "INTEGER NOT NULL DEFAULT 0"),
        ("hr_upload", "TEXT"),
    ):
        if col not in workout_cols:
            conn.execute(f"ALTER TABLE workout_logs ADD COLUMN {col} {decl}")

    # When definitions were created and last changed. Left NULL on existing
    # rows: their real creation time was never recorded and inventing one
    # would be worse than admitting it is unknown.
    for table in ("exercises", "supplements", "challenge_items"):
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for col in ("created_at", "updated_at"):
            if col not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")

    # Items used to belong to a single implicit challenge; give them a real
    # one so several can coexist. The default's start date is backdated to the
    # earliest completion so its statistics cover the history that exists.
    challenge_cols = {row[1] for row in conn.execute("PRAGMA table_info(challenges)")}
    if "repeat_of" not in challenge_cols:
        # Which challenge this one is another run of.
        conn.execute("ALTER TABLE challenges ADD COLUMN repeat_of INTEGER")
    if "schedule_kind" not in challenge_cols:
        # Which days a challenge is actually due. Existing ones are daily,
        # which is exactly how they behaved before.
        conn.execute(
            "ALTER TABLE challenges ADD COLUMN schedule_kind TEXT NOT NULL DEFAULT 'daily'"
        )
        conn.execute("ALTER TABLE challenges ADD COLUMN schedule_interval INTEGER")
        conn.execute("ALTER TABLE challenges ADD COLUMN schedule_weekdays TEXT")

    item_cols = {row[1] for row in conn.execute("PRAGMA table_info(challenge_items)")}
    if "challenge_id" not in item_cols:
        conn.execute("ALTER TABLE challenge_items ADD COLUMN challenge_id INTEGER")
    if "session_start" not in workout_cols:
        # The extent of the training session an entry belongs to, so heart rate
        # is read over the session rather than per exercise.
        conn.execute("ALTER TABLE workout_logs ADD COLUMN session_start TEXT")
        conn.execute("ALTER TABLE workout_logs ADD COLUMN session_end TEXT")

    if "moved_from" not in item_cols:
        # Which item this one continues, when it was moved between challenges.
        conn.execute("ALTER TABLE challenge_items ADD COLUMN moved_from INTEGER")
    if "archived_at" not in item_cols:
        # When an item stopped being part of its challenge. Needed to judge a
        # past day by the items that existed then; updated_at can't stand in
        # for it because any edit moves that.
        conn.execute("ALTER TABLE challenge_items ADD COLUMN archived_at TEXT")
    orphans = conn.execute(
        "SELECT COUNT(*) FROM challenge_items WHERE challenge_id IS NULL"
    ).fetchone()[0]
    if orphans:
        existing = conn.execute(
            "SELECT id FROM challenges ORDER BY id ASC LIMIT 1"
        ).fetchone()
        if existing:
            default_id = existing["id"]
        else:
            first_day = conn.execute(
                "SELECT MIN(day) AS d FROM challenge_completions"
            ).fetchone()["d"]
            cur = conn.execute(
                "INSERT INTO challenges (name, start_date, end_date, sort_order, archived, "
                "created_at, updated_at) VALUES (?, ?, NULL, 0, 0, ?, ?)",
                (DEFAULT_CHALLENGE_NAME, first_day or SEED_START_DATE, _now_ts(), _now_ts()),
            )
            default_id = cur.lastrowid
        conn.execute(
            "UPDATE challenge_items SET challenge_id = ? WHERE challenge_id IS NULL", (default_id,)
        )

    completion_cols = {row[1] for row in conn.execute("PRAGMA table_info(challenge_completions)")}
    if "ts" not in completion_cols:
        conn.execute("ALTER TABLE challenge_completions ADD COLUMN ts TEXT")

    supplement_cols = {row[1] for row in conn.execute("PRAGMA table_info(supplements)")}
    added_supplement_cols = False
    for col, decl in (
        ("dose_amount", "REAL"),
        ("dose_unit", "TEXT"),
        ("quantity", "REAL"),
        ("timing", "TEXT"),
        ("brand", "TEXT"),
    ):
        if col not in supplement_cols:
            conn.execute(f"ALTER TABLE supplements ADD COLUMN {col} {decl}")
            added_supplement_cols = True
    if added_supplement_cols:
        # Backfill structured dosage by parsing the old free-text `dose`
        # (e.g. "5 g" -> amount 5, unit "g") for rows not yet migrated.
        for row in conn.execute(
            "SELECT id, dose FROM supplements WHERE dose_amount IS NULL AND dose IS NOT NULL"
        ).fetchall():
            amount, unit = _parse_dose_text(row["dose"])
            conn.execute(
                "UPDATE supplements SET dose_amount = ?, dose_unit = ? WHERE id = ?",
                (amount, unit, row["id"]),
            )


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
    # The goal's first version, so its history starts from a known baseline.
    if conn.execute("SELECT COUNT(*) FROM goal_history").fetchone()[0] == 0:
        row = conn.execute("SELECT * FROM goal WHERE id = 1").fetchone()
        if row:
            # On an existing database the current goal is all there is to go
            # on, and when it was last changed is unknowable — 'migrated' says
            # changed_at is when tracking began, not when the goal moved.
            seeded = dict(row) == dict(SEED_GOAL, id=1)
            _record_goal_history(conn, dict(row), "seed" if seeded else "migrated")
    if conn.execute("SELECT COUNT(*) FROM weight_logs").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO weight_logs (ts, weight_kg, body_fat_pct, notes) VALUES (?, ?, NULL, ?)",
            (f"{SEED_START_DATE}T08:00:00", SEED_START_WEIGHT, "Starting weight"),
        )
    if conn.execute("SELECT COUNT(*) FROM exercises").fetchone()[0] == 0:
        for name, equipment, category in PRESET_EXERCISES:
            conn.execute(
                "INSERT INTO exercises (name, equipment, category, is_custom, archived, "
                "created_at, updated_at) VALUES (?, ?, ?, 0, 0, ?, ?)",
                (name, equipment, category, _now_ts(), _now_ts()),
            )
    if conn.execute("SELECT COUNT(*) FROM supplements").fetchone()[0] == 0:
        for name, amount, unit, quantity, timing, brand in SEED_SUPPLEMENTS:
            conn.execute(
                "INSERT INTO supplements (name, dose, dose_amount, dose_unit, quantity, timing, brand, "
                "is_custom, archived, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)",
                (name, _supplement_dose_text(amount, unit, quantity), amount, unit, quantity, timing,
                 brand, _now_ts(), _now_ts()),
            )
    _seed_typed_challenge(conn)


def _default_challenge_id(conn, start_date=None):
    """The challenge new items land in when none is named: the first active
    one, created on demand so a fresh database always has somewhere to put
    them. It has no end date — the open-ended daily habit."""
    row = conn.execute(
        "SELECT id FROM challenges WHERE archived = 0 ORDER BY sort_order ASC, id ASC LIMIT 1"
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO challenges (name, start_date, end_date, sort_order, archived, created_at, "
        "updated_at) VALUES (?, ?, NULL, 0, 0, ?, ?)",
        (DEFAULT_CHALLENGE_NAME, start_date or date.today().isoformat(), _now_ts(), _now_ts()),
    )
    return cur.lastrowid


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
    challenge_id = _default_challenge_id(conn, SEED_START_DATE)
    for order, spec in enumerate(SEED_CHALLENGE):
        if spec["type"] == "supplement":
            row = conn.execute(
                "SELECT id FROM supplements WHERE name = ? AND archived = 0", (spec["name"],)
            ).fetchone()
            if row is None:
                continue
            dose = spec.get("dose")
            conn.execute(
                "INSERT INTO challenge_items (label, sort_order, archived, item_type, supplement_id, "
                "dose, challenge_id, created_at, updated_at) "
                "VALUES (?, ?, 0, 'supplement', ?, ?, ?, ?, ?)",
                (f"{spec['name']}{' · ' + dose if dose else ''}", order, row["id"], dose,
                 challenge_id, _now_ts(), _now_ts()),
            )
        else:
            row = conn.execute(
                "SELECT id FROM exercises WHERE name = ? AND archived = 0", (spec["name"],)
            ).fetchone()
            if row is None:
                continue
            reps = spec.get("target_reps")
            conn.execute(
                "INSERT INTO challenge_items (label, sort_order, archived, item_type, exercise_id, "
                "target_reps, challenge_id, created_at, updated_at) "
                "VALUES (?, ?, 0, 'exercise', ?, ?, ?, ?, ?)",
                (f"{spec['name']}{' × ' + str(reps) if reps else ''}", order, row["id"], reps,
                 challenge_id, _now_ts(), _now_ts()),
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


def _redate_ts(existing_ts, existing_exact, day):
    """Work out the timestamp for an edited entry. Returns (ts, exact, error).

    Changing the date re-resolves the timestamp; leaving it alone keeps the
    original, so editing a note on something logged at 07:41 today doesn't
    quietly restamp it with the time you did the editing.
    """
    day = (day or "").strip()
    if not day or day == (existing_ts or "")[:10]:
        return existing_ts, existing_exact, None
    ts, exact, err = _resolve_ts(day)
    if err:
        return None, None, err
    return ts, exact, None


def _resolve_ts(day):
    """Turn an optional YYYY-MM-DD into a stored timestamp. Returns
    (ts, exact, None) or (None, None, error-message).

    `exact` records whether the time part is real. Logging against today reads
    the clock; an entry filed against an earlier day cannot know when it
    actually happened, so it keeps a midday placeholder and is marked inexact.
    Anything that reads the clock — heart rate, time-of-day analysis — must
    check this rather than trust the timestamp.
    """
    day = (day or "").strip()
    now = datetime.now()
    if not day:
        return now.isoformat(timespec="seconds"), 1, None
    try:
        date.fromisoformat(day)
    except ValueError:
        return None, None, "date must be YYYY-MM-DD"
    if day == now.date().isoformat():
        return now.isoformat(timespec="seconds"), 1, None
    return f"{day}T12:00:00", 0, None


# --- Garmin Connect sync ---

# How many trailing days each sync refreshes. A rolling window (rather than
# "since last sync") means transient gaps or late-arriving data self-heal.
GARMIN_SYNC_DAYS = 7
# ...but a watch that goes unsynced for longer than that window would otherwise
# lose those days for good, so each sync also chases holes further back. This is
# the default reach; `garmin_backfill_days` overrides it, and raising it is how
# you pull in history from before the add-on was installed.
GARMIN_BACKFILL_DAYS = 60
# Holes filled per sync, nearest-first: a fortnight off the charger heals on the
# next sync, while older history fills in over the following few.
GARMIN_BACKFILL_MAX = 10
# How many times a hole is re-asked about before it waits for the watch to
# upload again.
GARMIN_PROBE_ATTEMPTS = 3
# A day counts as filled once it has all three daily metrics. A day missing one
# of them is chased like a hole, so a metric that starts working (or arrives
# late from the watch) fills in across the history rather than only in the
# refresh window.
GARMIN_DAY_METRICS = ("sleep_seconds", "sleep_score", "stress_avg", "body_battery_high")


def _garmin_upsert_day(conn, day, fields):
    """Store the metrics Garmin actually returned for `day`. Returns True if
    anything was stored.

    Only non-null metrics are written. When the watch hasn't uploaded yet
    Garmin answers with the fields present but empty, and writing those would
    erase a day already synced — so a missing metric leaves the stored one
    alone instead of nulling it.
    """
    present = {k: v for k, v in (fields or {}).items() if v is not None}
    if not present:
        return False
    cols = ["day", "synced_at"] + list(present.keys())
    vals = [day, datetime.now().isoformat(timespec="seconds")] + list(present.values())
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "day")
    conn.execute(
        f"INSERT INTO garmin_daily ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(day) DO UPDATE SET {updates}",
        vals,
    )
    return True


def _garmin_record_probe(conn, day, device_upload):
    conn.execute(
        "INSERT INTO garmin_day_probe (day, attempts, last_attempt, device_upload) "
        "VALUES (?, 1, ?, ?) "
        "ON CONFLICT(day) DO UPDATE SET attempts = attempts + 1, "
        "last_attempt = excluded.last_attempt, device_upload = excluded.device_upload",
        (day, datetime.now().isoformat(timespec="seconds"), device_upload),
    )


def _garmin_day_is_filled(conn, day):
    row = conn.execute(
        f"SELECT {', '.join(GARMIN_DAY_METRICS)} FROM garmin_daily WHERE day = ?", (day,)
    ).fetchone()
    return bool(row) and all(row[c] is not None for c in GARMIN_DAY_METRICS)


def _garmin_sync_one_day(conn, client, day, device_upload):
    """Fetch one day and store whatever came back. A day left without all its
    metrics stays on the probe list, so it keeps being chased — that covers a
    day the watch hasn't finished uploading as well as a metric that was
    failing to parse and later starts working."""
    fields = garmin_client.fetch_day(client, day)
    stored = _garmin_upsert_day(conn, day, fields)
    if _garmin_day_is_filled(conn, day):
        conn.execute("DELETE FROM garmin_day_probe WHERE day = ?", (day,))
    else:
        _garmin_record_probe(conn, day, device_upload)
    return stored


def _garmin_backfill_days(conn, today, device_upload, horizon=None):
    """Days behind the refresh window that are still missing data, nearest day
    first, capped at GARMIN_BACKFILL_MAX.

    A hole is re-asked about a few times and then left alone until the watch
    uploads again — an upload is the only thing that can turn a permanently
    empty day (one the watch wasn't worn) into a day with data, so it is what
    re-opens the retries.
    """
    have = {
        r["day"]
        for r in conn.execute(
            f"SELECT day, {', '.join(GARMIN_DAY_METRICS)} FROM garmin_daily"
        )
        if all(r[c] is not None for c in GARMIN_DAY_METRICS)
    }
    probes = {r["day"]: r for r in conn.execute("SELECT * FROM garmin_day_probe")}
    out = []
    for offset in range(GARMIN_SYNC_DAYS, (horizon or GARMIN_BACKFILL_DAYS) + 1):
        day = (today - timedelta(days=offset)).isoformat()
        if day in have:
            continue
        probe = probes.get(day)
        if (
            probe
            and probe["attempts"] >= GARMIN_PROBE_ATTEMPTS
            and (probe["device_upload"] or "") == (device_upload or "")
        ):
            continue
        out.append(day)
        if len(out) >= GARMIN_BACKFILL_MAX:
            break
    return out


def _garmin_upsert_activity(conn, a):
    payload = {**a, "synced_at": datetime.now().isoformat(timespec="seconds")}
    conn.execute(
        "INSERT INTO garmin_activities "
        "(activity_id, start_time, activity_type, name, duration_sec, distance_m, calories, avg_hr, max_hr, synced_at) "
        "VALUES (:activity_id, :start_time, :activity_type, :name, :duration_sec, :distance_m, :calories, :avg_hr, :max_hr, :synced_at) "
        "ON CONFLICT(activity_id) DO UPDATE SET "
        "start_time = excluded.start_time, activity_type = excluded.activity_type, name = excluded.name, "
        "duration_sec = excluded.duration_sec, distance_m = excluded.distance_m, calories = excluded.calories, "
        "avg_hr = excluded.avg_hr, max_hr = excluded.max_hr, synced_at = excluded.synced_at",
        payload,
    )


# How far back logged exercises are chased for heart rate, and how many times
# each is re-asked about before it waits for the watch to upload again.
GARMIN_HR_BACKFILL_DAYS = 21
GARMIN_HR_ATTEMPTS = 3
# Fewer samples than this in the window is noise, not a heart rate.
GARMIN_HR_MIN_SAMPLES = 2


# Exercises logged more than this far apart are separate sessions. Logging five
# exercises after one workout should be one heart-rate window, not five
# overlapping ones over the same period.
SESSION_GAP_MINUTES = 90


def _session_groups(rows, gap_minutes=SESSION_GAP_MINUTES):
    """Split entries (ordered by ts, real times only) into training sessions on
    the gaps between them."""
    sessions, current, previous = [], [], None
    for row in rows:
        try:
            at = datetime.fromisoformat(row["ts"])
        except (ValueError, TypeError):
            continue
        if previous is not None and (at - previous).total_seconds() > gap_minutes * 60:
            sessions.append(current)
            current = []
        current.append(row)
        previous = at
    if current:
        sessions.append(current)
    return sessions


def _session_extent(session, default_minutes):
    """(start, end) of a session: back from the first exercise by its own
    duration, forward to when the last one was logged."""
    first, last = session[0], session[-1]
    window = _hr_window(first, default_minutes)
    if window is None:
        return None
    start, _ = window
    try:
        end = datetime.fromisoformat(last["ts"])
    except (ValueError, TypeError):
        return None
    return start, max(end, start)


def _exact_workouts_on(conn, day):
    return conn.execute(
        "SELECT id, ts, duration_sec FROM workout_logs "
        "WHERE substr(ts, 1, 10) = ? AND ts_exact = 1 ORDER BY ts ASC, id ASC",
        (day,),
    ).fetchall()


def _hr_window(row, default_minutes):
    """The window an exercise was performed in: it ends when the entry was
    logged (you log after finishing) and runs back by the entry's own duration,
    or by the configured default when it has none."""
    try:
        end = datetime.fromisoformat(row["ts"])
    except (ValueError, TypeError):
        return None
    seconds = row["duration_sec"] if row["duration_sec"] else default_minutes * 60
    return end - timedelta(seconds=int(seconds)), end


def _hr_summary(series, start, end):
    """avg / max / min over the samples inside [start, end]."""
    lo, hi = start.timestamp(), end.timestamp()
    beats = [bpm for at, bpm in series if lo <= at <= hi]
    if len(beats) < GARMIN_HR_MIN_SAMPLES:
        return None
    return {
        "hr_avg": round(sum(beats) / len(beats)),
        "hr_max": max(beats),
        "hr_min": min(beats),
        "hr_samples": len(beats),
    }


def _garmin_hr_candidates(conn, today):
    """Logged exercises still without a heart rate, newest first.

    An entry is re-asked about a few times and then left alone until the watch
    uploads again — the same rule the daily backfill uses, for the same reason:
    only an upload can turn an empty window into real samples.
    """
    since = (today - timedelta(days=GARMIN_HR_BACKFILL_DAYS)).isoformat()
    return conn.execute(
        "SELECT id, ts, duration_sec, hr_attempts, hr_upload FROM workout_logs "
        # ts_exact = 0 means the time is a midday placeholder, so there is no
        # real window to read a heart rate from. Filling one in would be
        # inventing data.
        "WHERE hr_avg IS NULL AND ts_exact = 1 AND substr(ts, 1, 10) >= ? "
        "ORDER BY ts DESC LIMIT 200",
        (since,),
    ).fetchall()


def _garmin_sync_heart_rates(conn, client, today, device_upload):
    """Fill in heart rate for logged exercises. Returns how many were filled.

    One heart-rate call per day covers every entry logged that day.
    """
    filled = 0
    by_day = {}
    # Read once: get_garmin_config() parses options.json off disk.
    window_minutes = get_garmin_config()["hr_window_minutes"]

    wanted = {}
    for row in _garmin_hr_candidates(conn, today):
        if row["hr_attempts"] >= GARMIN_HR_ATTEMPTS and (row["hr_upload"] or "") == (
            device_upload or ""
        ):
            continue
        wanted.setdefault(row["ts"][:10], set()).add(row["id"])

    for day, ids in wanted.items():
        # Sessions are built from every exercise logged that day, not only the
        # ones missing a heart rate, so a session's extent is the real one.
        for session in _session_groups(_exact_workouts_on(conn, day)):
            member_ids = [row["id"] for row in session]
            if not ids.intersection(member_ids):
                continue
            extent = _session_extent(session, window_minutes)
            if extent is None:
                continue
            if day not in by_day:
                by_day[day] = garmin_client.fetch_heart_rate_series(client, day)
            summary = _hr_summary(by_day[day], *extent)
            placeholders = ",".join("?" for _ in member_ids)
            start_iso = extent[0].isoformat(timespec="seconds")
            end_iso = extent[1].isoformat(timespec="seconds")
            if summary:
                # Written to every exercise in the session: they share the
                # window, so they share the reading.
                conn.execute(
                    f"UPDATE workout_logs SET hr_avg = ?, hr_max = ?, hr_min = ?, hr_samples = ?, "
                    f"hr_synced_at = ?, hr_attempts = hr_attempts + 1, hr_upload = ?, "
                    f"session_start = ?, session_end = ? WHERE id IN ({placeholders})",
                    (
                        summary["hr_avg"], summary["hr_max"], summary["hr_min"], summary["hr_samples"],
                        datetime.now().isoformat(timespec="seconds"), device_upload,
                        start_iso, end_iso, *member_ids,
                    ),
                )
                filled += len(member_ids)
            else:
                conn.execute(
                    f"UPDATE workout_logs SET hr_attempts = hr_attempts + 1, hr_upload = ?, "
                    f"session_start = ?, session_end = ? WHERE id IN ({placeholders})",
                    (device_upload, start_iso, end_iso, *member_ids),
                )
    return filled


def _garmin_do_sync(conn):
    """Refresh the trailing GARMIN_SYNC_DAYS of wellness data, chase any holes
    behind that window, pull activities over the whole span, and fill in heart
    rate for logged exercises (idempotent upserts). Records the last-sync
    timestamp; on failure records the error and re-raises."""
    try:
        client = garmin_client.get_client()
        today = date.today()
        # Fetched once per sync: every day stored or probed is stamped with the
        # watch upload it was fetched under.
        device_upload = garmin_client.device_last_upload(client)
        start = today - timedelta(days=GARMIN_SYNC_DAYS - 1)
        days = 0
        for offset in range(GARMIN_SYNC_DAYS):
            d = (start + timedelta(days=offset)).isoformat()
            if _garmin_sync_one_day(conn, client, d, device_upload):
                days += 1
        backfilled = 0
        holes = _garmin_backfill_days(
            conn, today, device_upload, get_garmin_config()["backfill_days"]
        )
        for d in holes:
            if _garmin_sync_one_day(conn, client, d, device_upload):
                backfilled += 1
        # One activities call covering the refresh window and any holes: they
        # are keyed by Garmin's own id, so a wider range just re-upserts.
        act_start = min([start.isoformat()] + holes)
        activities = garmin_client.fetch_activities(client, act_start, today.isoformat())
        for a in activities:
            _garmin_upsert_activity(conn, a)
        heart_rates = _garmin_sync_heart_rates(conn, client, today, device_upload)
        conn.commit()
        _set_app_state(conn, "garmin_last_sync", datetime.now().isoformat(timespec="seconds"))
        _set_app_state(conn, "garmin_device_last_upload", device_upload or "")
        _set_app_state(conn, "garmin_last_error", "")
        return {
            "days": days,
            "backfilled": backfilled,
            "activities": len(activities),
            "heart_rates": heart_rates,
        }
    except Exception as e:  # noqa: BLE001 - surface the message, never crash the caller
        _set_app_state(conn, "garmin_last_error", str(e))
        raise


# --- Domain helpers ---


def _now_ts():
    return datetime.now().isoformat(timespec="seconds")


def _record_goal_history(conn, goal, source):
    """Append the goal as it now stands. `source` says why: 'seed' for the
    starting goal, 'migrated' for the state found when history started being
    kept (its changed_at is when tracking began, not when the goal changed),
    'edit' for a real change."""
    conn.execute(
        "INSERT INTO goal_history (changed_at, source, target_date, target_weight_kg, "
        "target_body_fat_pct, start_date, start_weight_kg) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            _now_ts(),
            source,
            goal.get("target_date"),
            goal.get("target_weight_kg"),
            goal.get("target_body_fat_pct"),
            goal.get("start_date"),
            goal.get("start_weight_kg"),
        ),
    )


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
            "SELECT id, ts, weight_kg, body_fat_pct, notes, device FROM weight_logs ORDER BY ts ASC, id ASC"
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
        "forecast": _forecast(logs, goal),
        "logs": logs,
    }


def _forecast(logs, goal):
    """Weight forecast plus the body-fat trend that shares its target date.
    Body fat gets projected even when the weight trend can't be defined (they
    fail independently: bf is logged less often than weight)."""
    forecast = _weight_forecast(logs, goal)
    target_date = None
    if goal.get("target_date"):
        try:
            target_date = date.fromisoformat(goal["target_date"])
        except ValueError:
            target_date = None
    if target_date is None:
        return {**forecast, "bf_available": False}
    return {**forecast, **_body_fat_forecast(logs, goal, target_date)}


def _linear_trend(points):
    """Least-squares fit over (date, value) points. Returns (fit_fn, d0, slope)
    where fit_fn(date) -> value, or None if a line can't be defined (fewer than
    two points, or all on the same day)."""
    if len(points) < 2:
        return None
    d0 = points[0][0]
    xs = [(d - d0).days for d, _ in points]
    ys = [v for _, v in points]
    n = len(xs)
    xbar = sum(xs) / n
    ybar = sum(ys) / n
    sxx = sum((x - xbar) ** 2 for x in xs)
    if sxx == 0:
        return None
    slope = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys)) / sxx
    intercept = ybar - slope * xbar
    return (lambda d: intercept + slope * (d - d0).days), d0, slope


def _body_fat_forecast(logs, goal, target_date):
    """Body-fat linear trend projected to the same goal date as the weight
    forecast, so both can be drawn on one chart. Independent of the weight
    trend (body fat is logged less often), but shares the target date."""
    bf_points = []
    for l in logs:
        bf = l["body_fat_pct"]
        if bf is None:
            continue
        try:
            d = date.fromisoformat(l["ts"][:10])
        except (ValueError, TypeError):
            continue
        bf_points.append((d, bf))
    fit = _linear_trend(bf_points)
    if fit is None:
        return {"bf_available": False}
    fit_fn, d0, slope = fit
    projected = fit_fn(target_date)
    return {
        "bf_available": True,
        "bf_slope_per_week": round(slope * 7, 2),
        "bf_projected_pct": round(projected, 1),
        # Two points for drawing the body-fat trend across the same chart.
        "bf_trend": [
            {"ts": d0.isoformat(), "body_fat_pct": round(fit_fn(d0), 1)},
            {"ts": target_date.isoformat(), "body_fat_pct": round(projected, 1)},
        ],
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


def _requested_challenge_id():
    """The challenge_id query argument as an int. Returns (value, error) so a
    junk value answers 400 rather than raising out of the view."""
    raw = request.args.get("challenge_id")
    if raw in (None, ""):
        return None, None
    try:
        return int(raw), None
    except (TypeError, ValueError):
        return None, "challenge_id must be a number"


def _active_challenge_items(conn, challenge_id=None):
    """Active items, optionally for one challenge. Without a challenge id this
    spans every challenge, which is what the reminder needs."""
    where = "WHERE ci.archived = 0"
    params = ()
    if challenge_id is not None:
        where += " AND ci.challenge_id = ?"
        params = (challenge_id,)
    rows = conn.execute(
        "SELECT ci.id, ci.sort_order, ci.item_type, ci.exercise_id, ci.supplement_id, "
        "ci.target_sets, ci.target_reps, ci.dose, ci.label AS stored_label, ci.challenge_id, "
        "e.name AS exercise_name, s.name AS supplement_name, i.updated_at AS image_v "
        "FROM challenge_items ci "
        "LEFT JOIN exercises e ON e.id = ci.exercise_id "
        "LEFT JOIN supplements s ON s.id = ci.supplement_id "
        "LEFT JOIN exercise_images i ON i.exercise_id = ci.exercise_id "
        f"{where} ORDER BY ci.sort_order ASC, ci.id ASC",
        params,
    ).fetchall()
    return [_challenge_item_view(r) for r in rows]


def _challenges(conn, include_archived=False):
    where = "" if include_archived else "WHERE archived = 0"
    return [
        dict(r)
        for r in conn.execute(
            f"SELECT * FROM challenges {where} ORDER BY sort_order ASC, id ASC"
        )
    ]


def _challenge_membership(conn, challenge_id, challenge=None):
    """Every item that has ever belonged to the challenge, paired with the days
    it was a member for: (row, joined_day, left_day). Either end can be None,
    meaning open.

    This is what stops a day being judged against items that didn't exist yet.
    Without it, adding an item today makes every past day incomplete.
    """
    rows = conn.execute(
        "SELECT ci.id, ci.sort_order, ci.item_type, ci.exercise_id, ci.supplement_id, "
        "ci.target_sets, ci.target_reps, ci.dose, ci.label AS stored_label, ci.challenge_id, "
        "ci.archived, ci.created_at, ci.archived_at, "
        "e.name AS exercise_name, s.name AS supplement_name, i.updated_at AS image_v "
        "FROM challenge_items ci "
        "LEFT JOIN exercises e ON e.id = ci.exercise_id "
        "LEFT JOIN supplements s ON s.id = ci.supplement_id "
        "LEFT JOIN exercise_images i ON i.exercise_id = ci.exercise_id "
        "WHERE ci.challenge_id = ? ORDER BY ci.sort_order ASC, ci.id ASC",
        (challenge_id,),
    ).fetchall()
    # Ticking an earlier day is a statement that the item belonged to the
    # challenge then — backfilling history has to count, so membership starts
    # at the earlier of "created" and "first ticked".
    first_tick = {}
    ids = [r["id"] for r in rows]
    if ids:
        placeholders = ",".join("?" for _ in ids)
        first_tick = {
            r["item_id"]: r["first_day"]
            for r in conn.execute(
                f"SELECT item_id, MIN(day) AS first_day FROM challenge_completions "
                f"WHERE item_id IN ({placeholders}) GROUP BY item_id",
                tuple(ids),
            )
        }
    # Items that were there when the challenge was set up count from its start
    # date, even if the challenge itself was backdated. Only an item added
    # *later* has a join date of its own — that is the whole point, so that
    # adding one today cannot make yesterday incomplete.
    if challenge is None:
        challenge = conn.execute(
            "SELECT * FROM challenges WHERE id = ?", (challenge_id,)
        ).fetchone()
    setup_day = None
    if challenge is not None:
        setup_day = (challenge["created_at"] or challenge["start_date"] or "")[:10] or None

    out = []
    for row in rows:
        # No creation time recorded (items predating 1.12.0): treat as having
        # been there from the start, which is what they were.
        created = (row["created_at"] or "")[:10] or None
        if created and setup_day and created <= setup_day:
            created = None
        ticked = first_tick.get(row["id"])
        # A tick on an earlier day is an explicit statement that the item
        # applied then, so it pulls a known join date back — but it can never
        # introduce one for an item that was there from the start.
        if created is None:
            joined = None
        else:
            joined = min(created, ticked) if ticked else created
        left = (row["archived_at"] or "")[:10] or None
        if row["archived"] and left is None:
            # Archived before archive times were recorded — it can't be placed
            # in time, so it is left out rather than allowed to rewrite days.
            continue
        out.append((row, joined, left))
    return out


def _members_on(membership, day):
    ids = set()
    for row, joined, left in membership:
        if joined and day < joined:
            continue
        if left and day > left:
            continue
        ids.add(row["id"])
    return ids


def _challenge_day_range(ch, today=None):
    """The days a challenge counts over: from its start to today, or to its end
    date once that has passed. A challenge that hasn't started yet is empty."""
    today = today or date.today()
    try:
        start = date.fromisoformat(ch["start_date"])
    except (ValueError, TypeError):
        return None, None
    last = today
    if ch.get("end_date"):
        try:
            last = min(today, date.fromisoformat(ch["end_date"]))
        except ValueError:
            pass
    return start, last


SCHEDULE_KINDS = ("daily", "interval", "weekdays")


def _parse_weekdays(raw):
    """"0,2,4" -> {0, 2, 4}. Mon=0 … Sun=6, matching date.weekday()."""
    out = set()
    for part in str(raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError:
            continue
        if 0 <= value <= 6:
            out.add(value)
    return out


def _challenge_scheduled_on(ch, day):
    """Whether the challenge is due on `day`.

    Anything unrecognised falls back to daily: a challenge that quietly stopped
    asking for anything would be worse than one that asks too often.
    """
    kind = (ch.get("schedule_kind") or "daily") if hasattr(ch, "get") else "daily"
    if kind == "weekdays":
        wanted = _parse_weekdays(ch.get("schedule_weekdays"))
        return day.weekday() in wanted if wanted else True
    if kind == "interval":
        try:
            every = int(ch.get("schedule_interval") or 0)
            start = date.fromisoformat(ch["start_date"])
        except (TypeError, ValueError):
            return True
        if every < 2:
            return True
        # Anchored on the start date, so the pattern moves with it.
        return (day - start).days % every == 0
    return True


def _challenge_next_due(ch, from_day=None):
    """The next day the challenge is due, or None if it ends first. Looks two
    weeks ahead, which covers every schedule this supports."""
    cursor = (from_day or date.today()) + timedelta(days=1)
    end = None
    if ch.get("end_date"):
        try:
            end = date.fromisoformat(ch["end_date"])
        except ValueError:
            end = None
    for _ in range(14):
        if end and cursor > end:
            return None
        if _challenge_scheduled_on(ch, cursor):
            return cursor.isoformat()
        cursor += timedelta(days=1)
    return None


def _challenge_finished(ch, today=None):
    today = today or date.today()
    if not ch.get("end_date"):
        return False
    try:
        return date.fromisoformat(ch["end_date"]) < today
    except ValueError:
        return False


def _challenge_days(conn, ch, membership=None):
    """One entry per calendar day the challenge has run: how many of the items
    that were part of it *that day* were ticked, whether that completed the day,
    and whether the day was one the challenge was due at all.

    Rest days are still emitted so the adherence chart can show them; every
    figure derived from this filters on `scheduled`.
    """
    membership = membership if membership is not None else _challenge_membership(conn, ch["id"])
    by_day = _completions_by_day(conn, [row["id"] for row, _, _ in membership])
    start, last = _challenge_day_range(ch)
    days = []
    if start is None:
        return days
    cursor = start
    while cursor <= last:
        iso = cursor.isoformat()
        members = _members_on(membership, iso)
        done = len(by_day.get(iso, set()) & members)
        scheduled = _challenge_scheduled_on(ch, cursor)
        days.append(
            {
                "day": iso,
                "done": done,
                "total": len(members),
                # Only meaningful on a day the challenge was actually due.
                "complete": scheduled and bool(members) and done == len(members),
                "scheduled": scheduled,
            }
        )
        cursor += timedelta(days=1)
    return days


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
    keys = r.keys()
    return {
        "id": r["id"],
        "sort_order": r["sort_order"],
        "item_type": item_type,
        "exercise_id": r["exercise_id"],
        # Present only where the query joined it; absent is simply "no picture".
        "image_v": r["image_v"] if "image_v" in keys else None,
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


def _challenge_streak(conn, challenge_id=None):
    if challenge_id is None:
        challenge_id = _default_challenge_id(conn)
    challenge = conn.execute("SELECT * FROM challenges WHERE id = ?", (challenge_id,)).fetchone()
    membership = _challenge_membership(conn, challenge_id, challenge)
    if not membership:
        return 0
    by_day = _completions_by_day(conn, [row["id"] for row, _, _ in membership])

    def complete(day):
        members = _members_on(membership, day)
        return bool(members) and members <= by_day.get(day, set())

    # _challenge_day_range works on a mapping; a sqlite3.Row has no .get().
    start, last = _challenge_day_range(dict(challenge)) if challenge else (None, date.today())
    if start is None or last < start:
        return 0  # hasn't started
    # Counted from the challenge's last day, which for one that has finished is
    # its end date — otherwise a challenge that ended on a perfect run would
    # report a streak of zero, today being past it.
    challenge_map = dict(challenge) if challenge else {}

    def due(day):
        return _challenge_scheduled_on(challenge_map, day)

    # Rest days are stepped over: they neither extend the streak nor break it.
    cursor = last
    while cursor >= start and not due(cursor):
        cursor -= timedelta(days=1)
    if cursor >= start and not complete(cursor.isoformat()):
        # An unfinished due day doesn't break the run behind it.
        cursor -= timedelta(days=1)
    streak = 0
    while cursor >= start:
        if not due(cursor):
            cursor -= timedelta(days=1)
            continue
        if not complete(cursor.isoformat()):
            break
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def _longest_streak(days):
    """Longest run of completed due days. A rest day is skipped rather than
    treated as a miss, so a schedule can't cap the streak at its interval."""
    best = run = 0
    for entry in days:
        if not entry.get("scheduled", True):
            continue
        run = run + 1 if entry["complete"] else 0
        best = max(best, run)
    return best


def _challenge_complete_on(conn, day_iso, challenge_id=None):
    if challenge_id is None:
        challenge_id = _default_challenge_id(conn)
    membership = _challenge_membership(conn, challenge_id)
    members = _members_on(membership, day_iso)
    if not members:
        return False
    by_day = _completions_by_day(conn, [row["id"] for row, _, _ in membership])
    return members <= by_day.get(day_iso, set())


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
    ts, ts_exact, err = _resolve_ts(data.get("date"))
    if err:
        return jsonify({"error": err}), 400
    notes = (data.get("notes") or "").strip() or None
    device = (data.get("device") or "").strip() or None

    db = get_db()
    cur = db.execute(
        "INSERT INTO weight_logs (ts, weight_kg, body_fat_pct, notes, device, ts_exact) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (ts, weight, body_fat, notes, device, ts_exact),
    )
    db.commit()
    return jsonify({"status": "created", "id": cur.lastrowid}), 201


@app.route("/api/weight/<int:log_id>", methods=["PUT"])
def api_update_weight(log_id):
    data = request.get_json(force=True, silent=True) or {}
    db = get_db()
    existing = db.execute(
        "SELECT ts, ts_exact FROM weight_logs WHERE id = ?", (log_id,)
    ).fetchone()
    if existing is None:
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
    device = (data.get("device") or "").strip() or None
    ts, ts_exact, err = _redate_ts(existing["ts"], existing["ts_exact"], data.get("date"))
    if err:
        return jsonify({"error": err}), 400
    db.execute(
        "UPDATE weight_logs SET weight_kg = ?, body_fat_pct = ?, notes = ?, device = ?, "
        "ts = ?, ts_exact = ? WHERE id = ?",
        (weight, body_fat, notes, device, ts, ts_exact, log_id),
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
    updated = _get_goal(db)
    # Only a real change is an event; re-saving the same numbers is not.
    if any(updated.get(k) != goal.get(k) for k in (
        "target_date", "target_weight_kg", "target_body_fat_pct", "start_date", "start_weight_kg"
    )):
        _record_goal_history(db, updated, "edit")
    db.commit()
    return jsonify({"status": "updated", "goal": updated})


@app.route("/api/goal/history")
def api_goal_history():
    rows = get_db().execute(
        "SELECT * FROM goal_history ORDER BY changed_at ASC, id ASC"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


# --- Routes: exercises + workouts ---


@app.route("/api/exercises")
def api_exercises():
    db = get_db()
    rows = [
        dict(r)
        for r in db.execute(
            "SELECT e.id, e.name, e.equipment, e.category, e.is_custom, e.notes, "
            "i.updated_at AS image_v FROM exercises e "
            "LEFT JOIN exercise_images i ON i.exercise_id = e.id "
            "WHERE e.archived = 0 ORDER BY e.equipment ASC, e.name ASC"
        )
    ]
    groups = defaultdict(list)
    for r in rows:
        groups[r["equipment"]].append(r)
    ordered = [eq for eq in EQUIPMENT_ORDER if eq in groups]
    ordered += sorted(eq for eq in groups if eq not in EQUIPMENT_ORDER)
    return jsonify([{"equipment": eq, "exercises": groups[eq]} for eq in ordered])


# Sniffed from the bytes rather than trusted from the upload's own claim.
_IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)
# The browser resizes before uploading, so anything approaching this is either
# a client that didn't, or not really an image.
MAX_IMAGE_BYTES = 1_000_000


def _sniff_image(data):
    """The image's real type, or None if it isn't one we serve."""
    for signature, mime in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@app.route("/api/exercises/<int:exercise_id>/image", methods=["POST"])
def api_set_exercise_image(exercise_id):
    db = get_db()
    if db.execute("SELECT 1 FROM exercises WHERE id = ?", (exercise_id,)).fetchone() is None:
        return jsonify({"error": "no such exercise"}), 404
    uploaded = request.files.get("file")
    if uploaded is None or uploaded.filename == "":
        return jsonify({"error": "no file provided"}), 400
    data = uploaded.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        return jsonify({"error": "image is too large"}), 400
    mime = _sniff_image(data)
    if mime is None:
        return jsonify({"error": "not a JPEG, PNG or WebP image"}), 400
    db.execute(
        "INSERT INTO exercise_images (exercise_id, image, mime, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(exercise_id) DO UPDATE SET image = excluded.image, mime = excluded.mime, "
        "updated_at = excluded.updated_at",
        (exercise_id, data, mime, _now_ts()),
    )
    db.commit()
    return jsonify({"status": "saved", "bytes": len(data)})


@app.route("/api/exercises/<int:exercise_id>/image")
def api_get_exercise_image(exercise_id):
    row = get_db().execute(
        "SELECT image, mime FROM exercise_images WHERE exercise_id = ?", (exercise_id,)
    ).fetchone()
    if row is None:
        return jsonify({"error": "no image"}), 404
    # The client asks with ?v=<updated_at>, so a stored image can be cached
    # hard and still change the moment it is replaced.
    return Response(
        row["image"],
        mimetype=row["mime"],
        headers={"Cache-Control": "private, max-age=31536000"},
    )


@app.route("/api/exercises/<int:exercise_id>/image", methods=["DELETE"])
def api_delete_exercise_image(exercise_id):
    db = get_db()
    cur = db.execute("DELETE FROM exercise_images WHERE exercise_id = ?", (exercise_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "no image"}), 404
    return jsonify({"status": "removed"})


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
        "INSERT INTO exercises (name, equipment, category, is_custom, archived, created_at, "
        "updated_at) VALUES (?, ?, ?, 1, 0, ?, ?)",
        (name, equipment, category, _now_ts(), _now_ts()),
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
        "UPDATE exercises SET name = ?, equipment = ?, category = ?, updated_at = ? WHERE id = ?",
        (name, equipment, category, _now_ts(), exercise_id),
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
        db.execute(
            "UPDATE exercises SET archived = 1, updated_at = ? WHERE id = ?",
            (_now_ts(), exercise_id),
        )
        result = "archived"
    else:
        db.execute("DELETE FROM exercises WHERE id = ?", (exercise_id,))
        result = "deleted"
    db.commit()
    return jsonify({"status": result})


# --- Supplements ---


def _fmt_num(n):
    """Trim a whole-number float to an int for display (5.0 -> '5')."""
    if n is None:
        return None
    if isinstance(n, float) and n.is_integer():
        return str(int(n))
    return str(n)


def _parse_dose_text(text):
    """Best-effort split of a free-text dose like '5 g' into (5.0, 'g')."""
    if not text:
        return None, None
    m = re.match(r"^\s*([0-9]*\.?[0-9]+)\s*(.*)$", str(text))
    if not m:
        return None, (str(text).strip() or None)
    return float(m.group(1)), (m.group(2).strip() or None)


def _supplement_dose_text(amount, unit, quantity):
    """Human-readable dose, e.g. '5 g', '2× 500 mg', '1 scoop'."""
    amount_s = _fmt_num(amount)
    unit = (unit or "").strip()
    if amount_s and unit:
        base = f"{amount_s} {unit}"
    elif amount_s:
        base = amount_s
    elif unit:
        base = unit
    else:
        base = None
    if quantity not in (None, "", 1, 1.0):
        q = _fmt_num(quantity)
        return f"{q}× {base}" if base else q
    return base


def _supplement_view(r):
    return {
        "id": r["id"],
        "name": r["name"],
        "dose_amount": r["dose_amount"],
        "dose_unit": r["dose_unit"],
        "quantity": r["quantity"],
        "timing": r["timing"],
        "brand": r["brand"],
        "dose": _supplement_dose_text(r["dose_amount"], r["dose_unit"], r["quantity"]),
        "is_custom": r["is_custom"],
    }


def _supplement_fields_from_request(data):
    """Parse + validate the structured supplement fields from a request
    body. Returns (fields_dict, error_message)."""
    name = (data.get("name") or "").strip()
    if not name:
        return None, "name is required"
    try:
        dose_amount = _opt_float(data, "dose_amount")
        quantity = _opt_float(data, "quantity")
    except ValueError as e:
        return None, f"{e} must be a number"
    if dose_amount is not None and dose_amount < 0:
        return None, "dose_amount must be positive"
    if quantity is not None and quantity < 0:
        return None, "quantity must be positive"
    return (
        {
            "name": name,
            "dose_amount": dose_amount,
            "dose_unit": (data.get("dose_unit") or "").strip() or None,
            "quantity": quantity,
            "timing": (data.get("timing") or "").strip() or None,
            "brand": (data.get("brand") or "").strip() or None,
        },
        None,
    )


# --- Routes: supplements ---


@app.route("/api/supplements")
def api_supplements():
    db = get_db()
    rows = db.execute(
        "SELECT id, name, dose, dose_amount, dose_unit, quantity, timing, brand, is_custom "
        "FROM supplements WHERE archived = 0 ORDER BY name ASC"
    )
    return jsonify([_supplement_view(r) for r in rows])


@app.route("/api/supplement-timings")
def api_supplement_timings():
    return jsonify(SUPPLEMENT_TIMINGS)


@app.route("/api/supplements", methods=["POST"])
def api_add_supplement():
    data = request.get_json(force=True, silent=True) or {}
    fields, err = _supplement_fields_from_request(data)
    if err:
        return jsonify({"error": err}), 400
    dose = _supplement_dose_text(fields["dose_amount"], fields["dose_unit"], fields["quantity"])
    db = get_db()
    cur = db.execute(
        "INSERT INTO supplements (name, dose, dose_amount, dose_unit, quantity, timing, brand, "
        "is_custom, archived, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)",
        (fields["name"], dose, fields["dose_amount"], fields["dose_unit"], fields["quantity"],
         fields["timing"], fields["brand"], _now_ts(), _now_ts()),
    )
    db.commit()
    return jsonify({"status": "created", "id": cur.lastrowid}), 201


@app.route("/api/supplements/<int:supplement_id>", methods=["PUT"])
def api_update_supplement(supplement_id):
    data = request.get_json(force=True, silent=True) or {}
    fields, err = _supplement_fields_from_request(data)
    if err:
        return jsonify({"error": err}), 400
    dose = _supplement_dose_text(fields["dose_amount"], fields["dose_unit"], fields["quantity"])
    db = get_db()
    cur = db.execute(
        "UPDATE supplements SET name = ?, dose = ?, dose_amount = ?, dose_unit = ?, quantity = ?, "
        "timing = ?, brand = ?, updated_at = ? WHERE id = ?",
        (fields["name"], dose, fields["dose_amount"], fields["dose_unit"], fields["quantity"],
         fields["timing"], fields["brand"], _now_ts(), supplement_id),
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
        db.execute(
            "UPDATE supplements SET archived = 1, updated_at = ? WHERE id = ?",
            (_now_ts(), supplement_id),
        )
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
            "w.notes, w.source, w.ts_exact, w.hr_avg, w.hr_max, w.hr_min, w.hr_samples, "
            "e.name AS exercise_name, e.equipment "
            f"FROM workout_logs w JOIN exercises e ON e.id = w.exercise_id {where} "
            "ORDER BY w.ts DESC, w.id DESC LIMIT 200",
            tuple(params),
        )
    ]
    return jsonify(rows)


@app.route("/api/sessions")
def api_sessions():
    """Training sessions: exercises logged close together, grouped, with the
    time under load and the heart rate over the whole session.

    Only entries with a real time can be grouped — a midday placeholder says
    nothing about what was done alongside what.
    """
    db = get_db()
    try:
        days = max(1, min(90, int(request.args.get("days", 14))))
    except (TypeError, ValueError):
        days = 14
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    rows = db.execute(
        "SELECT w.id, w.ts, w.duration_sec, w.sets, w.reps, w.hr_avg, w.hr_max, w.hr_min, "
        "w.session_start, w.session_end, e.name AS exercise_name "
        "FROM workout_logs w JOIN exercises e ON e.id = w.exercise_id "
        "WHERE w.ts_exact = 1 AND substr(w.ts, 1, 10) >= ? ORDER BY w.ts ASC, w.id ASC",
        (since,),
    ).fetchall()

    by_day = defaultdict(list)
    for row in rows:
        by_day[row["ts"][:10]].append(row)

    out = []
    for day, day_rows in by_day.items():
        for session in _session_groups(day_rows):
            first, last = session[0], session[-1]
            start = first["session_start"] or first["ts"]
            end = last["session_end"] or last["ts"]
            try:
                minutes = round(
                    (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 60
                )
            except (ValueError, TypeError):
                minutes = None
            hr = next((r for r in session if r["hr_avg"] is not None), None)
            out.append(
                {
                    "day": day,
                    "start": start,
                    "end": end,
                    "minutes": minutes,
                    "exercises": [
                        {
                            "id": r["id"],
                            "name": r["exercise_name"],
                            "sets": r["sets"],
                            "reps": r["reps"],
                        }
                        for r in session
                    ],
                    "reps": sum((r["sets"] or 1) * (r["reps"] or 0) for r in session),
                    # One reading for the session: every exercise in it shares
                    # the same window, so they share the same numbers.
                    "hr_avg": hr["hr_avg"] if hr else None,
                    "hr_max": hr["hr_max"] if hr else None,
                }
            )
    out.sort(key=lambda x: x["start"], reverse=True)
    return jsonify(out)


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
    ts, ts_exact, err = _resolve_ts(data.get("date"))
    if err:
        return jsonify({"error": err}), 400
    notes = (data.get("notes") or "").strip() or None

    cur = db.execute(
        "INSERT INTO workout_logs (ts, exercise_id, sets, reps, weight_kg, duration_sec, notes, "
        "source, ts_exact) VALUES (?, ?, ?, ?, ?, ?, ?, 'manual', ?)",
        (ts, exercise_id, sets, reps, weight, duration, notes, ts_exact),
    )
    db.commit()
    return jsonify({"status": "created", "id": cur.lastrowid}), 201


@app.route("/api/workouts/<int:workout_id>", methods=["PUT"])
def api_update_workout(workout_id):
    data = request.get_json(force=True, silent=True) or {}
    db = get_db()
    existing = db.execute(
        "SELECT ts, ts_exact FROM workout_logs WHERE id = ?", (workout_id,)
    ).fetchone()
    if existing is None:
        return jsonify({"error": "no such workout"}), 404
    try:
        sets, reps, duration = _opt_int(data, "sets"), _opt_int(data, "reps"), _opt_int(data, "duration_sec")
        weight = _opt_float(data, "weight_kg")
    except ValueError as e:
        return jsonify({"error": f"{e} must be a number"}), 400
    ts, ts_exact, err = _redate_ts(existing["ts"], existing["ts_exact"], data.get("date"))
    if err:
        return jsonify({"error": err}), 400
    notes = (data.get("notes") or "").strip() or None
    db.execute(
        "UPDATE workout_logs SET ts = ?, ts_exact = ?, sets = ?, reps = ?, weight_kg = ?, "
        "duration_sec = ?, notes = ? WHERE id = ?",
        (ts, ts_exact, sets, reps, weight, duration, notes, workout_id),
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


def _schedule_view(ch):
    return {
        "kind": ch.get("schedule_kind") or "daily",
        "interval": ch.get("schedule_interval"),
        "weekdays": sorted(_parse_weekdays(ch.get("schedule_weekdays"))),
    }


def _challenge_view(conn, ch):
    """One challenge as the app shows it: its items with today's state, its
    streak, and where it is in a fixed-length run."""
    items = _active_challenge_items(conn, ch["id"])
    ids = [i["id"] for i in items]
    by_day = _completions_by_day(conn, ids)
    today = date.today()
    today_iso = today.isoformat()
    done_today = by_day.get(today_iso, set())
    for i in items:
        i["done_today"] = i["id"] in done_today

    last_7 = []
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        d = day.isoformat()
        last_7.append(
            {
                "day": d,
                "complete": bool(ids) and set(ids) <= by_day.get(d, set()),
                "scheduled": _challenge_scheduled_on(ch, day),
            }
        )

    start, _ = _challenge_day_range(ch)
    total_days = None
    day_number = None
    if start and ch.get("end_date"):
        try:
            end = date.fromisoformat(ch["end_date"])
            total_days = (end - start).days + 1
            day_number = min(max((today - start).days + 1, 0), total_days)
        except ValueError:
            total_days = None
    return {
        "id": ch["id"],
        "name": ch["name"],
        "start_date": ch["start_date"],
        "end_date": ch["end_date"],
        "finished": _challenge_finished(ch),
        "not_started": bool(start and today < start),
        "day_number": day_number,
        "total_days": total_days,
        "today": today_iso,
        "schedule": _schedule_view(ch),
        # Rest days still show the card, with nothing owed.
        "due_today": _challenge_scheduled_on(ch, today),
        "next_due": _challenge_next_due(ch, today),
        "items": items,
        "streak": _challenge_streak(conn, ch["id"]),
        "complete_today": bool(ids) and set(ids) <= done_today,
        "last_7_days": last_7,
    }


def _challenge_weight(conn, ch):
    """Weigh-ins over the challenge's own period, so its adherence can be read
    against what the scale did. Reported side by side, never as cause: a few
    weigh-ins over a few weeks can't establish that one moved the other.
    """
    start, last = _challenge_day_range(ch)
    empty = {"points": [], "start_kg": None, "end_kg": None, "delta_kg": None, "delta_bf": None}
    if start is None:
        return empty
    rows = conn.execute(
        "SELECT substr(ts, 1, 10) AS day, weight_kg, body_fat_pct FROM weight_logs "
        "WHERE substr(ts, 1, 10) BETWEEN ? AND ? ORDER BY ts ASC, id ASC",
        (start.isoformat(), last.isoformat()),
    ).fetchall()
    points = [
        {"day": r["day"], "weight_kg": r["weight_kg"], "body_fat_pct": r["body_fat_pct"]}
        for r in rows
    ]
    if not points:
        return empty
    first_bf = next((p["body_fat_pct"] for p in points if p["body_fat_pct"] is not None), None)
    last_bf = next(
        (p["body_fat_pct"] for p in reversed(points) if p["body_fat_pct"] is not None), None
    )
    return {
        "points": points,
        "start_kg": points[0]["weight_kg"],
        "end_kg": points[-1]["weight_kg"],
        "delta_kg": round(points[-1]["weight_kg"] - points[0]["weight_kg"], 1),
        "delta_bf": (
            round(last_bf - first_bf, 1)
            if first_bf is not None and last_bf is not None and len(points) > 1
            else None
        ),
    }


def _challenge_stats(conn, ch):
    """Per-challenge statistics: how often it is completed, the streaks, the
    day-by-day record, which items actually get done, and the volume logged
    through it."""
    membership = _challenge_membership(conn, ch["id"])
    ids = {row["id"] for row, _, _ in membership}
    days = _challenge_days(conn, ch, membership)
    by_day = _completions_by_day(conn, list(ids))
    # Rest days are in `days` for the chart, but every figure counts due days.
    due_days = [d for d in days if d["scheduled"]]
    elapsed = len(due_days)
    complete_days = sum(1 for d in due_days if d["complete"])

    # Each item is scored over the days it was actually part of the challenge,
    # so one added late isn't marked down for the days before it existed.
    per_item = []
    for row, joined, left in membership:
        member_days = [
            d["day"]
            for d in due_days
            if (not joined or d["day"] >= joined) and (not left or d["day"] <= left)
        ]
        hits = sum(1 for day in member_days if row["id"] in by_day.get(day, set()))
        view = _challenge_item_view(row)
        per_item.append(
            {
                "id": row["id"],
                "label": view["label"],
                "item_type": row["item_type"],
                "archived": bool(row["archived"]),
                "days_member": len(member_days),
                "days_done": hits,
                "rate_pct": round(hits / len(member_days) * 100, 1) if member_days else None,
            }
        )

    volume = {"sessions": 0, "reps": 0, "hr_avg": None, "hr_max": None}
    start, last = _challenge_day_range(ch)
    if ids and start is not None:
        placeholders = ",".join("?" for _ in ids)
        # Bounded by the challenge's own period, like every other figure here —
        # otherwise a finished challenge keeps accruing volume.
        row = conn.execute(
            "SELECT COUNT(*) AS sessions, "
            "SUM(COALESCE(sets, 1) * COALESCE(reps, 0)) AS reps, "
            "AVG(hr_avg) AS hr_avg, MAX(hr_max) AS hr_max "
            f"FROM workout_logs WHERE challenge_item_id IN ({placeholders}) "
            "AND substr(ts, 1, 10) BETWEEN ? AND ?",
            tuple(ids) + (start.isoformat(), last.isoformat()),
        ).fetchone()
        volume = {
            "sessions": row["sessions"] or 0,
            "reps": int(row["reps"]) if row["reps"] else 0,
            "hr_avg": round(row["hr_avg"]) if row["hr_avg"] is not None else None,
            "hr_max": row["hr_max"],
        }

    return {
        "id": ch["id"],
        "name": ch["name"],
        "start_date": ch["start_date"],
        "end_date": ch["end_date"],
        "finished": _challenge_finished(ch),
        "weight": _challenge_weight(conn, ch),
        "schedule": _schedule_view(ch),
        "days_elapsed": elapsed,
        "days_complete": complete_days,
        "completion_pct": round(complete_days / elapsed * 100, 1) if elapsed else None,
        "current_streak": _challenge_streak(conn, ch["id"]),
        "longest_streak": _longest_streak(days),
        "item_count": sum(1 for row, _, _ in membership if not row["archived"]),
        # Capped: the adherence chart only ever draws a window of this.
        "days": days[-180:],
        "items": per_item,
        "volume": volume,
    }


@app.route("/api/challenges")
def api_challenges():
    db = get_db()
    return jsonify([_challenge_view(db, ch) for ch in _challenges(db)])


@app.route("/api/challenges/stats")
def api_challenges_stats():
    db = get_db()
    return jsonify([_challenge_stats(db, ch) for ch in _challenges(db)])


@app.route("/api/challenges", methods=["POST"])
def api_add_challenge():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()[:80]
    if not name:
        return jsonify({"error": "name is required"}), 400
    start = (data.get("start_date") or "").strip() or date.today().isoformat()
    end = (data.get("end_date") or "").strip() or None
    err = _validate_challenge_dates(start, end)
    if err:
        return jsonify({"error": err}), 400
    kind, interval, weekdays, err = _resolve_schedule(data)
    if err:
        return jsonify({"error": err}), 400
    db = get_db()
    order = db.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM challenges").fetchone()["n"]
    cur = db.execute(
        "INSERT INTO challenges (name, start_date, end_date, sort_order, archived, created_at, "
        "updated_at, schedule_kind, schedule_interval, schedule_weekdays) "
        "VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?)",
        (name, start, end, order, _now_ts(), _now_ts(), kind, interval, weekdays),
    )
    db.commit()
    return jsonify({"status": "created", "id": cur.lastrowid}), 201


@app.route("/api/challenges/<int:challenge_id>", methods=["PUT"])
def api_update_challenge(challenge_id):
    data = request.get_json(force=True, silent=True) or {}
    db = get_db()
    existing = db.execute("SELECT * FROM challenges WHERE id = ?", (challenge_id,)).fetchone()
    if existing is None:
        return jsonify({"error": "no such challenge"}), 404
    name = (data.get("name") or "").strip()[:80] or existing["name"]
    start = (data.get("start_date") or "").strip() or existing["start_date"]
    # An empty string clears the end date; a missing key leaves it alone.
    end = data.get("end_date", existing["end_date"])
    end = (end or "").strip() or None
    err = _validate_challenge_dates(start, end)
    if err:
        return jsonify({"error": err}), 400
    kind, interval, weekdays, err = _resolve_schedule(data, dict(existing))
    if err:
        return jsonify({"error": err}), 400
    db.execute(
        "UPDATE challenges SET name = ?, start_date = ?, end_date = ?, updated_at = ?, "
        "schedule_kind = ?, schedule_interval = ?, schedule_weekdays = ? WHERE id = ?",
        (name, start, end, _now_ts(), kind, interval, weekdays, challenge_id),
    )
    db.commit()
    return jsonify({"status": "updated"})


@app.route("/api/challenges/<int:challenge_id>/repeat", methods=["POST"])
def api_repeat_challenge(challenge_id):
    """Start another run of a challenge: same items, fresh dates.

    A finished 30-day challenge repeats as another 30 days from today unless
    told otherwise. The original is left exactly as it was — this is a new
    challenge with its own record, not a reset of the old one.
    """
    data = request.get_json(force=True, silent=True) or {}
    db = get_db()
    source = db.execute("SELECT * FROM challenges WHERE id = ?", (challenge_id,)).fetchone()
    if source is None:
        return jsonify({"error": "no such challenge"}), 404

    start = (data.get("start_date") or "").strip() or date.today().isoformat()
    if "end_date" in data:
        end = (data.get("end_date") or "").strip() or None
    else:
        # Keep the original's length, so "30-day" stays 30 days.
        end = None
        if source["end_date"]:
            try:
                length = (
                    date.fromisoformat(source["end_date"]) - date.fromisoformat(source["start_date"])
                ).days
                end = (date.fromisoformat(start) + timedelta(days=length)).isoformat()
            except ValueError:
                end = None
    err = _validate_challenge_dates(start, end)
    if err:
        return jsonify({"error": err}), 400
    name = (data.get("name") or "").strip()[:80] or source["name"]

    now = _now_ts()
    order = db.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM challenges").fetchone()["n"]
    kind, interval, weekdays, err = _resolve_schedule(data, dict(source))
    if err:
        return jsonify({"error": err}), 400
    cur = db.execute(
        "INSERT INTO challenges (name, start_date, end_date, sort_order, archived, created_at, "
        "updated_at, repeat_of, schedule_kind, schedule_interval, schedule_weekdays) "
        "VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)",
        (name, start, end, order, now, now, challenge_id, kind, interval, weekdays),
    )
    new_id = cur.lastrowid
    for item in db.execute(
        "SELECT * FROM challenge_items WHERE challenge_id = ? AND archived = 0 "
        "ORDER BY sort_order ASC, id ASC",
        (challenge_id,),
    ).fetchall():
        db.execute(
            "INSERT INTO challenge_items (label, sort_order, archived, item_type, exercise_id, "
            "supplement_id, target_sets, target_reps, dose, challenge_id, created_at, updated_at) "
            "VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item["label"], item["sort_order"], item["item_type"], item["exercise_id"],
                item["supplement_id"], item["target_sets"], item["target_reps"], item["dose"],
                new_id, now, now,
            ),
        )
    db.commit()
    return jsonify({"status": "created", "id": new_id}), 201


@app.route("/api/challenges/<int:challenge_id>", methods=["DELETE"])
def api_delete_challenge(challenge_id):
    db = get_db()
    if db.execute("SELECT 1 FROM challenges WHERE id = ?", (challenge_id,)).fetchone() is None:
        return jsonify({"error": "no such challenge"}), 404
    # Archived, never deleted: its items and their completions are history.
    db.execute(
        "UPDATE challenges SET archived = 1, updated_at = ? WHERE id = ?", (_now_ts(), challenge_id)
    )
    db.commit()
    return jsonify({"status": "archived"})


def _resolve_schedule(data, existing=None):
    """Pull a schedule out of a request body. Returns (kind, interval, weekdays,
    error). Falls back to the existing schedule, then to daily."""
    base = existing or {}
    kind = (data.get("schedule_kind") or base.get("schedule_kind") or "daily").strip()
    if kind not in SCHEDULE_KINDS:
        return None, None, None, f"schedule_kind must be one of {', '.join(SCHEDULE_KINDS)}"
    if kind == "interval":
        raw = data.get("schedule_interval", base.get("schedule_interval"))
        try:
            interval = int(raw)
        except (TypeError, ValueError):
            return None, None, None, "schedule_interval must be a number"
        if not 2 <= interval <= 30:
            # 1 is just "daily" spelled oddly, and beyond a month it stops
            # being a habit.
            return None, None, None, "schedule_interval must be between 2 and 30"
        return kind, interval, None, None
    if kind == "weekdays":
        raw = data.get("schedule_weekdays", base.get("schedule_weekdays"))
        if isinstance(raw, (list, tuple)):
            raw = ",".join(str(v) for v in raw)
        days = _parse_weekdays(raw)
        if not days:
            return None, None, None, "schedule_weekdays must name at least one day (0=Mon…6=Sun)"
        return kind, None, ",".join(str(d) for d in sorted(days)), None
    return "daily", None, None, None


def _validate_challenge_dates(start, end):
    try:
        start_d = date.fromisoformat(start)
    except (ValueError, TypeError):
        return "start_date must be YYYY-MM-DD"
    if end:
        try:
            end_d = date.fromisoformat(end)
        except (ValueError, TypeError):
            return "end_date must be YYYY-MM-DD"
        if end_d < start_d:
            return "end_date must not be before start_date"
    return None


@app.route("/api/challenge")
def api_challenge():
    """The first active challenge, kept for callers that predate several."""
    db = get_db()
    active = _challenges(db)
    if not active:
        return jsonify({"today": date.today().isoformat(), "items": [], "streak": 0,
                        "complete_today": False, "last_7_days": []})
    return jsonify(_challenge_view(db, active[0]))


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
        # Ticking today records the moment; ticking an earlier day can't know
        # when it happened, so it keeps the day's midday placeholder.
        now = datetime.now()
        today = day == now.date().isoformat()
        ts = now.isoformat(timespec="seconds") if today else f"{day}T12:00:00"
        ts_exact = 1 if today else 0
        db.execute(
            "INSERT INTO challenge_completions (item_id, day, ts) VALUES (?, ?, ?)",
            (item_id, day, ts),
        )
        done = True

    # An exercise item ticked off also lands in the workout log (source
    # 'challenge'); un-ticking removes that auto-created entry so history
    # stays in sync. Manual workout entries are never touched.
    if item["item_type"] == "exercise" and item["exercise_id"]:
        if done:
            db.execute(
                "INSERT INTO workout_logs (ts, exercise_id, sets, reps, source, challenge_item_id, "
                "ts_exact) VALUES (?, ?, ?, ?, 'challenge', ?, ?)",
                (ts, item["exercise_id"], item["target_sets"], item["target_reps"], item_id, ts_exact),
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
    challenge_id, err = _requested_challenge_id()
    if err:
        return jsonify({"error": err}), 400
    return jsonify(_active_challenge_items(get_db(), challenge_id))


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
    challenge_id = data.get("challenge_id")
    if challenge_id in (None, ""):
        challenge_id = _default_challenge_id(db)
    else:
        try:
            challenge_id = int(challenge_id)
        except (TypeError, ValueError):
            return jsonify({"error": "challenge_id must be a number"}), 400
        if db.execute(
            "SELECT 1 FROM challenges WHERE id = ? AND archived = 0", (challenge_id,)
        ).fetchone() is None:
            return jsonify({"error": "no such challenge"}), 400
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
            "target_sets, target_reps, challenge_id, created_at, updated_at) "
            "VALUES (?, ?, 0, 'exercise', ?, ?, ?, ?, ?, ?)",
            (label, next_order, exercise_id, target_sets, target_reps, challenge_id,
             _now_ts(), _now_ts()),
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
            "INSERT INTO challenge_items (label, sort_order, archived, item_type, supplement_id, "
            "dose, challenge_id, created_at, updated_at) "
            "VALUES (?, ?, 0, 'supplement', ?, ?, ?, ?, ?)",
            (label, next_order, supplement_id, dose, challenge_id, _now_ts(), _now_ts()),
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
            "UPDATE challenge_items SET target_sets = ?, target_reps = ?, label = ?, "
            "updated_at = ? WHERE id = ?",
            (target_sets, target_reps, label, _now_ts(), item_id),
        )
    else:
        name = db.execute("SELECT name FROM supplements WHERE id = ?", (item["supplement_id"],)).fetchone()
        dose = (data.get("dose") or "").strip() or None
        label = f"{name['name'] if name else 'Supplement'}{' · ' + dose if dose else ''}"
        db.execute(
            "UPDATE challenge_items SET dose = ?, label = ?, updated_at = ? WHERE id = ?",
            (dose, label, _now_ts(), item_id),
        )
    db.commit()
    return jsonify({"status": "updated"})


@app.route("/api/challenge/history")
def api_challenge_history():
    """A per-day completion matrix for backfilling or correcting the streak.
    Accepts an explicit `from`/`to` date range (YYYY-MM-DD) for importing
    older records, or falls back to the last `days` (default 14). Newest day
    first; the span is capped at ~1 year to keep the payload sane."""
    db = get_db()
    today = date.today()
    MAX_SPAN = 370

    def _parse(arg):
        try:
            return date.fromisoformat(request.args.get(arg))
        except (ValueError, TypeError):
            return None

    to_date = _parse("to") or today
    from_date = _parse("from")
    if from_date is None:
        try:
            days = max(1, min(MAX_SPAN, int(request.args.get("days", 14))))
        except (TypeError, ValueError):
            days = 14
        from_date = to_date - timedelta(days=days - 1)
    if from_date > to_date:
        from_date, to_date = to_date, from_date
    # Clamp an over-wide range to the most recent MAX_SPAN days of it.
    if (to_date - from_date).days > MAX_SPAN - 1:
        from_date = to_date - timedelta(days=MAX_SPAN - 1)

    challenge_id, err = _requested_challenge_id()
    if err:
        return jsonify({"error": err}), 400
    items = _active_challenge_items(db, challenge_id)
    active_ids = [i["id"] for i in items]
    by_day = _completions_by_day(db, active_ids)
    history = []
    d = to_date
    while d >= from_date:
        iso = d.isoformat()
        done = by_day.get(iso, set())
        history.append(
            {
                "day": iso,
                "done": [i["id"] for i in items if i["id"] in done],
                "complete": bool(active_ids) and set(active_ids) <= done,
            }
        )
        d -= timedelta(days=1)
    return jsonify(
        {"items": items, "days": history, "from": from_date.isoformat(), "to": to_date.isoformat()}
    )


@app.route("/api/challenge/items/<int:item_id>/move", methods=["POST"])
def api_move_challenge_item(item_id):
    """Move an item to another challenge.

    Ticks belong to the item they were made against, and they happened under
    the old challenge — so this ends the item's membership there and starts a
    fresh one in the target, rather than dragging its history across. Each
    challenge then keeps the days it actually earned.
    """
    data = request.get_json(force=True, silent=True) or {}
    db = get_db()
    item = db.execute(
        "SELECT * FROM challenge_items WHERE id = ? AND archived = 0", (item_id,)
    ).fetchone()
    if item is None:
        return jsonify({"error": "no such challenge item"}), 404
    try:
        target_id = int(data.get("challenge_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "challenge_id is required"}), 400
    if target_id == item["challenge_id"]:
        return jsonify({"error": "already in that challenge"}), 400
    if db.execute(
        "SELECT 1 FROM challenges WHERE id = ? AND archived = 0", (target_id,)
    ).fetchone() is None:
        return jsonify({"error": "no such challenge"}), 400

    now = _now_ts()
    db.execute(
        "UPDATE challenge_items SET archived = 1, archived_at = ?, updated_at = ? WHERE id = ?",
        (now, now, item_id),
    )
    order = db.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM challenge_items "
        "WHERE challenge_id = ? AND archived = 0",
        (target_id,),
    ).fetchone()["n"]
    cur = db.execute(
        "INSERT INTO challenge_items (label, sort_order, archived, item_type, exercise_id, "
        "supplement_id, target_sets, target_reps, dose, challenge_id, created_at, updated_at, "
        "moved_from) VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            item["label"], order, item["item_type"], item["exercise_id"], item["supplement_id"],
            item["target_sets"], item["target_reps"], item["dose"], target_id, now, now, item_id,
        ),
    )
    db.commit()
    return jsonify({"status": "moved", "id": cur.lastrowid})


@app.route("/api/challenge/items/<int:item_id>", methods=["DELETE"])
def api_delete_challenge_item(item_id):
    db = get_db()
    # Archive rather than delete so past streak/history stays intact.
    cur = db.execute(
        "UPDATE challenge_items SET archived = 1, updated_at = ?, archived_at = ? "
        "WHERE id = ? AND archived = 0",
        (_now_ts(), _now_ts(), item_id),
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


# --- Routes: Garmin Connect ---


def _garmin_status_payload(db):
    cfg = get_garmin_config()
    return {
        "connected": garmin_client.is_connected(),
        "last_sync": _get_app_state(db, "garmin_last_sync"),
        # When the watch last uploaded to Garmin, which is not the same thing
        # as when this add-on last talked to Garmin.
        "device_last_upload": _get_app_state(db, "garmin_device_last_upload") or None,
        "last_error": _get_app_state(db, "garmin_last_error") or None,
        "auto_sync": cfg["auto_sync"],
        "interval_hours": cfg["interval_hours"],
        "backfill_days": cfg["backfill_days"],
    }


@app.route("/api/garmin/status")
def api_garmin_status():
    return jsonify(_garmin_status_payload(get_db()))


@app.route("/api/garmin/connect", methods=["POST"])
def api_garmin_connect():
    data = request.get_json(force=True, silent=True) or {}
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    if not email or not password:
        return jsonify({"error": "email and password are required"}), 400
    try:
        result = garmin_client.begin_login(email, password)
    except Exception as e:  # noqa: BLE001 - report the login failure to the user
        return jsonify({"error": f"Garmin login failed: {e}"}), 502
    return jsonify(result)


@app.route("/api/garmin/mfa", methods=["POST"])
def api_garmin_mfa():
    data = request.get_json(force=True, silent=True) or {}
    code = (data.get("code") or "").strip()
    if not code:
        return jsonify({"error": "code is required"}), 400
    try:
        result = garmin_client.complete_mfa(code)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"2FA verification failed: {e}"}), 502
    return jsonify(result)


@app.route("/api/garmin/disconnect", methods=["POST"])
def api_garmin_disconnect():
    garmin_client.disconnect()
    db = get_db()
    _set_app_state(db, "garmin_last_sync", "")
    _set_app_state(db, "garmin_device_last_upload", "")
    _set_app_state(db, "garmin_last_error", "")
    return jsonify({"status": "disconnected"})


@app.route("/api/garmin/sync", methods=["POST"])
def api_garmin_sync():
    if not garmin_client.is_connected():
        return jsonify({"error": "Garmin is not connected"}), 409
    db = get_db()
    try:
        imported = _garmin_do_sync(db)
    except Exception as e:  # noqa: BLE001
        return jsonify({"status": "failed", "error": str(e)}), 502
    return jsonify({"status": "ok", "imported": imported})


@app.route("/api/garmin/diagnose")
def api_garmin_diagnose():
    """Why a Garmin metric is empty: what each source returns for one day, and
    the shape of the response, without dumping the raw payload."""
    if not garmin_client.is_connected():
        return jsonify({"error": "Garmin is not connected"}), 409
    day = request.args.get("day") or date.today().isoformat()
    try:
        client = garmin_client.get_client()
        return jsonify(
            {
                "body_battery": garmin_client.diagnose_body_battery(client, day),
                "sleep": garmin_client.diagnose_sleep(client, day),
            }
        )
    except Exception as e:  # noqa: BLE001 - a diagnostic must report, not raise
        return jsonify({"day": day, "error": str(e)}), 502


@app.route("/api/garmin/summary")
def api_garmin_summary():
    db = get_db()
    latest = db.execute(
        "SELECT * FROM garmin_daily ORDER BY day DESC LIMIT 1"
    ).fetchone()
    activities = db.execute(
        "SELECT * FROM garmin_activities ORDER BY start_time DESC LIMIT 10"
    ).fetchall()
    return jsonify(
        {
            "connected": garmin_client.is_connected(),
            "latest": dict(latest) if latest else None,
            "activities": [dict(a) for a in activities],
        }
    )


@app.route("/api/garmin/daily")
def api_garmin_daily():
    db = get_db()
    clauses, params = [], []
    if request.args.get("from"):
        clauses.append("day >= ?")
        params.append(request.args["from"])
    if request.args.get("to"):
        clauses.append("day <= ?")
        params.append(request.args["to"])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = db.execute(
        f"SELECT * FROM garmin_daily {where} ORDER BY day DESC LIMIT 370", tuple(params)
    ).fetchall()
    return jsonify([dict(r) for r in rows])


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
    # Only nag if something is still outstanding, across every challenge that
    # is running today — a finished one shouldn't keep asking.
    outstanding = []
    for ch in _challenges(conn):
        if _challenge_finished(ch) or not _active_challenge_items(conn, ch["id"]):
            continue
        start, _ = _challenge_day_range(ch, now.date())
        if start and now.date() < start:
            continue
        if not _challenge_scheduled_on(ch, now.date()):
            continue  # a rest day owes nothing
        # Judged for the day being evaluated, which is not necessarily today.
        if not _challenge_complete_on(conn, today_iso, ch["id"]):
            outstanding.append(ch["name"])
    if outstanding:
        names = ", ".join(outstanding)
        send_notification(
            f"Still to do today: {names} 💪",
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


def _garmin_sync_tick(now, conn):
    cfg = get_garmin_config()
    if not (cfg["auto_sync"] and garmin_client.is_connected()):
        return
    last = _get_app_state(conn, "garmin_last_sync")
    if last:
        try:
            if now - datetime.fromisoformat(last) < timedelta(hours=cfg["interval_hours"]):
                return  # synced recently enough
        except ValueError:
            pass  # unparseable marker — sync now and overwrite it
    try:
        _garmin_do_sync(conn)
    except Exception:  # noqa: BLE001 - error is recorded in app_state; keep looping
        app.logger.exception("garmin sync failed")


def _background_loop():
    # SUPERVISOR_TOKEN is injected whenever the add-on runs under Home Assistant
    # (homeassistant_api: true), so its absence means local/dev mode — where
    # there's neither notify nor a reason to reach out to Garmin.
    if not SUPERVISOR_TOKEN:
        _log("SUPERVISOR_TOKEN not set; background tasks disabled (local/dev mode)")
        return
    while True:
        try:
            conn = _db_connect_standalone()
            try:
                now = datetime.now()
                _garmin_sync_tick(now, conn)
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
