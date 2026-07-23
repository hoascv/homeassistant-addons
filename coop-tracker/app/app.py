import base64
import binascii
import csv
import importlib.metadata
import io
import json
import math
import os
import platform
import signal
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, time as dtime, timedelta

from flask import Flask, Response, g, jsonify, render_template, request, send_file

try:
    import numpy as np
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    STATSMODELS_AVAILABLE = True
    STATSMODELS_ERROR = None
except ImportError as e:
    # statsmodels only has wheels for amd64/aarch64 (see
    # requirements-advanced.txt, ARCHITECTURE.md §19) — armhf/armv7/i386
    # builds skip installing it entirely, so this import fails there by
    # design. Reported via /api/debug instead of crashing the app.
    STATSMODELS_AVAILABLE = False
    STATSMODELS_ERROR = str(e)

try:
    import cv2
    import numpy as np  # own guard — must not depend on the statsmodels import above

    OPENCV_AVAILABLE = True
    OPENCV_ERROR = None
except ImportError as e:
    # opencv-python-headless has amd64/aarch64 manylinux wheels but none at
    # all for armv7/armhf/i386, under any libc — a different reason than
    # statsmodels' aarch64 gap above, but the same amd64/arm64 install gate
    # (see requirements-advanced.txt, ARCHITECTURE.md §20).
    OPENCV_AVAILABLE = False
    OPENCV_ERROR = str(e)

try:
    import pickle

    from sklearn.linear_model import LogisticRegression

    SKLEARN_AVAILABLE = True
    SKLEARN_ERROR = None
except ImportError as e:
    # scikit-learn ships in requirements-advanced.txt alongside
    # opencv-python-headless, same amd64/aarch64 install gate — in every
    # real deployment the two succeed or fail together, but this is
    # guarded independently (like statsmodels vs. opencv above) so a
    # missing scikit-learn reports clearly via /api/debug rather than
    # crashing at import time. See ARCHITECTURE.md §20 addendum.
    SKLEARN_AVAILABLE = False
    SKLEARN_ERROR = str(e)

APP_VERSION = "1.32.1"  # keep in sync with the "version" field in config.yaml

DB_PATH = os.environ.get("COOP_DB_PATH", "/data/coop.db")
OPTIONS_PATH = os.environ.get("COOP_OPTIONS_PATH", "/data/options.json")

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
HA_API_BASE = "http://supervisor/core/api"

CURRENCIES = {
    "USD": {"symbol": "$", "position": "prefix", "decimals": 2},
    "EUR": {"symbol": "€", "position": "prefix", "decimals": 2},
    "GBP": {"symbol": "£", "position": "prefix", "decimals": 2},
    "DKK": {"symbol": "kr", "position": "suffix", "decimals": 2},
    "SEK": {"symbol": "kr", "position": "suffix", "decimals": 2},
    "NOK": {"symbol": "kr", "position": "suffix", "decimals": 2},
    "CHF": {"symbol": "CHF", "position": "prefix", "decimals": 2},
    "CAD": {"symbol": "$", "position": "prefix", "decimals": 2},
    "AUD": {"symbol": "$", "position": "prefix", "decimals": 2},
    "JPY": {"symbol": "¥", "position": "prefix", "decimals": 0},
}
DEFAULT_CURRENCY = "DKK"

FORECAST_MONTHS = 3
FORECAST_TRAILING_DAYS = 30
FORECAST_RATIO_BOUNDS = (0.2, 1.8)  # dampens noise from a single unusual week

# Seasonal laying curve: one universal sinusoid over the calendar year,
# peaking at the summer solstice (daylight-driven laying). Constants, not
# config — see ARCHITECTURE.md §9. Assumes the northern hemisphere.
SEASONAL_AMPLITUDE = 0.25  # ±25% swing; the tuning knob if the backtest runs off
SEASONAL_PEAK_DAY = 172  # ~June 21

# Experimental Holt-Winters comparison forecast — see ARCHITECTURE.md §19.
# "History" = elapsed calendar months since the first-ever egg log, capped
# at 24 to match _recent_month_starts' own hard cap.
ADVANCED_FORECAST_MIN_MONTHS = 6  # minimum to attempt a trend-only fit
ADVANCED_FORECAST_SEASONAL_MIN_MONTHS = 24  # minimum for a seasonal term (2 full cycles)

# Egg photo analysis (count + size from a photo) — see ARCHITECTURE.md §20.
EGG_SIZE_CODES = ("S", "M", "L", "XL")
# Width-in-mm thresholds an egg is bucketed into, derived from standard EU
# weight bands (S<53g, M 53-63g, L 63-73g, XL>73g) via isometric scaling
# anchored at a 63g (M/L boundary) egg ≈ 44mm wide. An approximation of
# weight-based grading from a 2D photo measurement, not a real weight.
EGG_SIZE_MM_BOUNDS = (41.5, 44.0, 46.5)  # S|M, M|L, L|XL boundaries

# Contour-area bounds for "plausibly one egg", as a fraction of the whole
# photo's area — filters out sensor noise (too small) and a misclassified
# background/shadow blob (too large).
EGG_MIN_AREA_FRACTION = 0.0015
EGG_MAX_AREA_FRACTION = 0.20

# A single egg's major/minor axis ratio is typically ~1.3-1.5 (chicken eggs
# aren't very elongated); horizontally-touching eggs merged into one
# contour top out empirically around ~1.7-1.8 before fitEllipse's fit
# naturally separates them into two contours again. 1.6 sits between the
# two with modest margin on each side — imperfect (a genuinely elongated
# single egg near this ratio could get wrongly excluded, and some merged
# pairs at lower overlap won't reach it), but excluding is the safer
# failure: a wrong exclusion costs one "+ Add egg" tap, a wrong inclusion
# silently reports a bogus size for two eggs merged into one. This is the
# fallback used whenever no trained classifier exists yet — see
# EGG_VISION_MIN_TRAINING_SAMPLES below.
EGG_MAX_ASPECT = 1.6

MAX_EGG_VISION_PHOTO_BYTES = 8 * 1024 * 1024  # analysis photo, not the 3MB chicken-photo cap

# Nesting-box width calibration (handheld camera, no coin — see
# ARCHITECTURE.md §20 addendum): a box's interior should dominate a
# properly-framed photo, the same "obvious, hard-to-miss geometric
# primitive" reasoning that made Hough circles the right fit for a coin
# in the original design. The two facing side walls are the reference:
# each is a (possibly slanted) line, and the local wall-to-wall pixel
# distance at an egg's own row, plus the box's known real-world width,
# give that egg's px-per-mm. Slanting captures the dominant handheld
# effect — walls converging with depth when the camera looks into the
# box — without needing the box's depth measured or its (usually
# bedding-buried) floor corners located, which is why this was chosen
# over a full 4-corner perspective transform.
BOX_MIN_AREA_FRACTION = 0.15

# Egg detection color pass (see ARCHITECTURE.md §20 addendum): eggs are
# found as regions whose Lab color differs from the bedding's estimated
# (median) color — hue-agnostic, so brown, white, or any future egg
# color works as long as it contrasts with the bedding. Chroma (a/b)
# gets extra weight vs lightness so shadows and dents in the bedding
# don't read as eggs; the distance floor keeps a near-uniform, eggless
# scene from having its sensor noise Otsu-split into phantom regions.
EGG_BEDDING_L_WEIGHT = 1.0
EGG_BEDDING_CHROMA_WEIGHT = 2.0
EGG_MIN_COLOR_DISTANCE = 12

# Trainable egg-vision model (opt-in, see ARCHITECTURE.md §20 addendum):
# minimum stored examples before each sub-model is considered trained.
# Deliberately conservative — a model fit on a handful of examples is
# worse than the fixed heuristics it replaces. Note these gate each
# sub-model independently inside _train_egg_vision_models; the train
# endpoint itself only refuses when there are zero samples at all.
EGG_VISION_MIN_TRAINING_SAMPLES = 25
EGG_VISION_MIN_CLASSIFIER_POS = 15
EGG_VISION_MIN_CLASSIFIER_NEG = 15
EGG_VISION_MIN_SIZE_SAMPLES = 25

# Box identification (which registered nesting box is in this photo —
# see ARCHITECTURE.md §20 addendum): a small pretrained CNN (SqueezeNet
# 1.1, bundled in the image at app/models/) is used as a frozen feature
# extractor via cv2.dnn — feature extraction only, nothing leaves the
# device — with a nearest-centroid cosine head trained on this
# install's own sample photos. With exactly one registered box there's
# nothing to disambiguate, so no classifier is needed at all; from two
# boxes on, a prediction is only trusted when it clears BOTH an
# absolute similarity floor (rejects "looks like neither box": a new,
# unregistered box or a terrible photo) AND a margin over the runner-up
# (rejects "could be either"); anything else surfaces as "please
# confirm" rather than a silent wrong guess.
EGG_VISION_BOX_EMBED_MODEL_PATH = os.environ.get(
    "COOP_BOX_EMBED_MODEL",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "squeezenet1.1-7.onnx"),
)
EGG_VISION_BOX_EMBED_INPUT_SIZE = 224
EGG_VISION_BOX_EMBED_MEAN = (0.485, 0.456, 0.406)  # ImageNet, RGB order
EGG_VISION_BOX_EMBED_STD = (0.229, 0.224, 0.225)
EGG_VISION_BOX_ID_MIN_SIMILARITY = 0.55
EGG_VISION_BOX_ID_MIN_MARGIN = 0.15
EGG_VISION_BOX_ID_MIN_SAMPLES_PER_BOX = 3
EGG_VISION_BOX_MODEL_FORMAT = 2  # bump whenever embedding features change incompatibly

# The nesting-box wizard's stopping rule (see ARCHITECTURE.md §20
# addendum): keep collecting corrected photos for a box until the
# freshly-retrained model gets N in a row exactly right (zero
# corrections needed, and — once >=2 boxes exist — correct box
# identification too), proving it's reliably right rather than luck.
# Capped so a run that never converges still ends.
EGG_VISION_WIZARD_STREAK_TARGET = 3
EGG_VISION_WIZARD_MAX_ATTEMPTS = 30


def _seasonal_multiplier(when):
    """Multiplier on the flock's flat annual-mean daily rate for the given
    date: ~1.25 in June, ~0.75 in December, ~1.0 at the equinoxes. Annual
    mean ≈ 1.0, so breed annual_eggs totals are redistributed across the
    year, not inflated."""
    day = when.timetuple().tm_yday
    return 1.0 + SEASONAL_AMPLITUDE * math.cos(
        2 * math.pi * (day - SEASONAL_PEAK_DAY) / 365.25
    )

# Simple 3-stage age-based laying curve applied to a bird's breed's annual
# rate — see _chicken_daily_rate(). Deliberately one universal curve
# shape (not per-breed) — see ARCHITECTURE.md §9.
POINT_OF_LAY_DAYS = 140  # ~20 weeks: no eggs before this age
PRIME_END_DAYS = 550  # ~18 months: full rate through this age
REDUCED_RATE_MULTIPLIER = 0.8  # rate applied from PRIME_END_DAYS onward

# A generous ceiling on a decoded chicken photo — the frontend already
# resizes to ~400px JPEG before upload (typically tens of KB), this is
# just a backend safety net against a client that doesn't.
MAX_PHOTO_BYTES = 3 * 1024 * 1024

# Seeded into the breeds table the first time it's created (empty table
# only — see init_db()); editable afterwards via /api/breeds. Values are
# published average annual eggs/hen/year.
DEFAULT_BREEDS = [
    ("Isabrown", 300),
    ("Sussex", 260),
]

# Fixed set, like entry types in api_log — a free-text field would fragment
# into unmatchable variants the same way food_type once did (see §10).
HEALTH_EVENT_TYPES = (
    "vet_visit",
    "vaccination",
    "molt_start",
    "molt_end",
    "weight",
    "observation",
)

# Seeded into the food_types table the first time it's created (empty
# table only — see init_db()); editable afterwards via /api/food-types.
DEFAULT_FOOD_TYPES = [
    "Layer feed",
    "Grower feed",
    "Starter feed",
    "Pellets",
    "Crumbles",
    "Mash",
    "Scratch grains",
    "Mixed grain",
    "Kitchen scraps",
    "Grit",
    "Oyster shell",
]

app = Flask(__name__)


def _log(msg):
    """Prints a timestamped, immediately-flushed [Coop Tracker] log line.
    Used for the console-visible add-on log (startup/shutdown info,
    background-loop diagnostics) rather than app.logger, which only
    surfaces WARNING+ by default (see api routes' own error handling for
    request-scoped logging). flush=True matters here specifically:
    stdout is block-buffered (not a TTY under Supervisor/Docker), so an
    unflushed line can sit in the buffer and be lost entirely if the
    process is later SIGKILLed rather than exiting normally — see the
    exit-137-on-restart investigation."""
    print(f"[Coop Tracker] {datetime.now().isoformat()} {msg}", flush=True)


def _read_options():
    try:
        with open(OPTIONS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def get_currency():
    code = _read_options().get("currency", DEFAULT_CURRENCY)
    return CURRENCIES.get(code, CURRENCIES[DEFAULT_CURRENCY])


def get_reminder_config():
    opts = _read_options()
    return {
        "enabled": bool(opts.get("reminder_enabled", False)),
        "check_time": opts.get("reminder_check_time", "18:00"),
        "threshold_days": int(opts.get("reminder_threshold_days", 2)),
        "notify_service": (opts.get("notify_service") or "").strip(),
    }


def get_ha_sensors_enabled():
    return bool(_read_options().get("ha_sensors_enabled", False))


def get_flock_counts():
    opts = _read_options()
    return {
        "isabrown": int(opts.get("flock_isabrown_count", 3)),
        "sussex": int(opts.get("flock_sussex_count", 2)),
    }


def get_supermarket_egg_price():
    return float(_read_options().get("supermarket_egg_price", 2.5))


def get_advanced_forecast_config():
    return {"enabled": bool(_read_options().get("advanced_forecast_enabled", False))}


def get_egg_vision_config():
    return {"enabled": bool(_read_options().get("egg_vision_enabled", False))}


def get_egg_vision_training_config():
    opts = _read_options()
    return {
        "enabled": bool(opts.get("egg_vision_training_enabled", False)),
        "retention_count": int(opts.get("egg_vision_training_retention_count", 200)),
    }


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


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            ts TEXT NOT NULL,
            count INTEGER,
            food_type TEXT,
            amount TEXT,
            notes TEXT
        )
        """
    )

    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(logs)")}
    for column, coltype in (
        ("price", "REAL"),
        ("cost", "REAL"),
        ("category", "TEXT"),
        ("container_empty", "INTEGER"),
        ("given_away", "INTEGER"),
        ("egg_sizes", "TEXT"),
    ):
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE logs ADD COLUMN {column} {coltype}")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS food_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
        """
    )
    food_type_count = conn.execute("SELECT COUNT(*) FROM food_types").fetchone()[0]
    if food_type_count == 0:
        conn.executemany(
            "INSERT INTO food_types (name) VALUES (?)",
            [(name,) for name in DEFAULT_FOOD_TYPES],
        )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS breeds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            annual_eggs INTEGER NOT NULL
        )
        """
    )
    breed_count = conn.execute("SELECT COUNT(*) FROM breeds").fetchone()[0]
    if breed_count == 0:
        conn.executemany(
            "INSERT INTO breeds (name, annual_eggs) VALUES (?, ?)",
            DEFAULT_BREEDS,
        )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chickens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            breed TEXT,
            hatch_date TEXT,
            status TEXT NOT NULL DEFAULT 'active'
        )
        """
    )
    chicken_columns = {row[1] for row in conn.execute("PRAGMA table_info(chickens)")}
    if "photo" not in chicken_columns:
        conn.execute("ALTER TABLE chickens ADD COLUMN photo BLOB")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )

    # The REFERENCES clause is schema documentation: SQLite only enforces
    # it under PRAGMA foreign_keys, which this app never enables — the
    # cascade is done manually in api_delete_chicken instead. See
    # ARCHITECTURE.md §18.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS health_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chicken_id INTEGER NOT NULL REFERENCES chickens(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            event_date TEXT NOT NULL,
            weight_grams INTEGER,
            notes TEXT,
            created_at TEXT NOT NULL
        )
        """
    )

    # Egg vision: nesting boxes (handheld-camera, width-only calibration
    # reference — see ARCHITECTURE.md §20 addendum) and the optional,
    # opt-in trainable model. box_id is nullable so a sample from a
    # since-deleted box still has a home.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS nesting_boxes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            width_mm REAL NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS egg_vision_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            photo BLOB NOT NULL,
            image_width INTEGER NOT NULL,
            image_height INTEGER NOT NULL,
            box_id INTEGER REFERENCES nesting_boxes(id),
            original_detection TEXT NOT NULL,
            corrected_result TEXT NOT NULL
        )
        """
    )

    # At most one row: training does DELETE then INSERT (see
    # _save_egg_vision_model) so inference is always a trivial SELECT with
    # no "most recent" query logic to get wrong. Models are trained with
    # scikit-learn (see ARCHITECTURE.md §20 addendum) and stored as
    # pickled blobs — internally generated and read back by this same
    # app, never loaded from an untrusted source.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS egg_vision_models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trained_at TEXT NOT NULL,
            trained_on_sample_count INTEGER NOT NULL,
            classifier_blob BLOB,
            classifier_positive_count INTEGER,
            classifier_negative_count INTEGER,
            size_model_blob BLOB,
            size_model_sample_count INTEGER,
            box_classifier_blob BLOB,
            box_classifier_labels TEXT,
            box_classifier_sample_count INTEGER
        )
        """
    )

    conn.commit()
    conn.close()


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


def _last_egg_collection(conn):
    row = conn.execute(
        "SELECT ts FROM logs WHERE type = 'egg' ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    return datetime.fromisoformat(row["ts"]) if row else None


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


def send_notification(message, title="Coop Tracker"):
    service = get_reminder_config()["notify_service"]
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


_reminder_last_checked_date = None


def _eggs_overdue(now, conn, threshold_days):
    last_ts = _last_egg_collection(conn)
    return last_ts is None or (now - last_ts) >= timedelta(days=threshold_days)


def _reminder_tick(now, conn):
    global _reminder_last_checked_date
    cfg = get_reminder_config()
    if not (cfg["enabled"] and cfg["notify_service"]):
        return
    target = _parse_hhmm(cfg["check_time"])
    if target is None or now.time() < target:
        return
    if _reminder_last_checked_date is None:
        # first tick since startup: recover the guard from the DB so a
        # restart shortly after today's reminder can't send a duplicate
        stored = _get_app_state(conn, "reminder_last_checked_date")
        if stored:
            _reminder_last_checked_date = date.fromisoformat(stored)
    if _reminder_last_checked_date == now.date():
        return  # already evaluated today

    _reminder_last_checked_date = now.date()
    _set_app_state(conn, "reminder_last_checked_date", now.date().isoformat())

    if _eggs_overdue(now, conn, cfg["threshold_days"]):
        send_notification(
            f"No eggs collected in {cfg['threshold_days']}+ days — check the coop!",
            title="Coop Tracker reminder",
        )


def _push_ha_state(entity_id, state, attributes=None):
    _, err = _ha_api_request(
        "POST", f"/states/{entity_id}", {"state": state, "attributes": attributes or {}}
    )
    return err


def _push_ha_sensors(conn):
    if not get_ha_sensors_enabled():
        return
    now = datetime.now()
    summary = _compute_summary(conn, now)
    currency = get_currency()
    reminder_cfg = get_reminder_config()

    _push_ha_state(
        "sensor.coop_tracker_eggs_today",
        summary["eggs_today"],
        {"friendly_name": "Coop Tracker eggs today", "unit_of_measurement": "eggs", "icon": "mdi:egg"},
    )
    _push_ha_state(
        "sensor.coop_tracker_eggs_week",
        summary["eggs_week"],
        {"friendly_name": "Coop Tracker eggs this week", "unit_of_measurement": "eggs", "icon": "mdi:egg"},
    )
    _push_ha_state(
        "sensor.coop_tracker_eggs_available",
        summary["eggs_available"],
        {"friendly_name": "Coop Tracker eggs on hand", "unit_of_measurement": "eggs", "icon": "mdi:egg"},
    )
    _push_ha_state(
        "sensor.coop_tracker_last_cleaning",
        summary["last_cleaning"] or "unknown",
        {"friendly_name": "Coop Tracker last cleaning", "icon": "mdi:broom"},
    )
    _push_ha_state(
        "sensor.coop_tracker_last_feeding",
        summary["last_feeding"] or "unknown",
        {"friendly_name": "Coop Tracker last feeding", "icon": "mdi:food-drumstick"},
    )
    _push_ha_state(
        "sensor.coop_tracker_revenue_month",
        summary["revenue_month"],
        {
            "friendly_name": "Coop Tracker revenue this month",
            "unit_of_measurement": currency["symbol"],
            "icon": "mdi:cash-plus",
        },
    )
    _push_ha_state(
        "sensor.coop_tracker_cost_month",
        summary["cost_month"],
        {
            "friendly_name": "Coop Tracker cost this month",
            "unit_of_measurement": currency["symbol"],
            "icon": "mdi:cash-minus",
        },
    )
    _push_ha_state(
        "sensor.coop_tracker_net_month",
        summary["net_month"],
        {
            "friendly_name": "Coop Tracker net this month",
            "unit_of_measurement": currency["symbol"],
            "icon": "mdi:cash",
        },
    )
    _push_ha_state(
        "binary_sensor.coop_tracker_eggs_overdue",
        "on" if _eggs_overdue(now, conn, reminder_cfg["threshold_days"]) else "off",
        {"friendly_name": "Coop Tracker eggs overdue", "icon": "mdi:egg-off"},
    )


def _push_ha_sensors_async():
    """Run _push_ha_sensors on its own connection, in its own thread, so a
    slow/unreachable Home Assistant can't hold up the request that just
    saved a log entry — sqlite3 connections aren't shareable across
    threads, so this opens a fresh one rather than reusing the request's."""
    conn = _db_connect_standalone()
    try:
        _push_ha_sensors(conn)
    finally:
        conn.close()


def _background_loop():
    if not SUPERVISOR_TOKEN:
        # app.logger.info was silently dropped here — Flask's default
        # logger level is WARNING, so this line (useful for noticing
        # "why isn't the background loop doing anything") never actually
        # appeared in the add-on log. _log() is always visible.
        _log("SUPERVISOR_TOKEN not set; reminder and HA sensor push disabled (local/dev mode)")
        return
    while True:
        iteration_start = time.monotonic()
        try:
            conn = _db_connect_standalone()
            try:
                _reminder_tick(datetime.now(), conn)
                _push_ha_sensors(conn)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - keep the loop alive across any single failure
            app.logger.exception("background loop iteration failed")
        # Diagnostic for the exit-137-on-restart investigation: _push_ha_sensors
        # makes up to 9 sequential HA API calls (5s timeout each) — if a restart
        # happens while this loop is mid-iteration, this is the trail that would
        # show whether it was ever the reason the process was slow to exit.
        # Silent in the normal (fast) case to avoid permanent log noise.
        elapsed = time.monotonic() - iteration_start
        if elapsed > 2:
            _log(f"background loop iteration took {elapsed:.1f}s (usually near-instant)")
        time.sleep(60)


@app.route("/")
def index():
    currency = get_currency()
    egg_vision_cfg = get_egg_vision_config()
    training_cfg = get_egg_vision_training_config()
    return render_template(
        "index.html",
        currency_symbol=currency["symbol"],
        currency_position=currency["position"],
        currency_decimals=currency["decimals"],
        app_version=APP_VERSION,
        egg_vision_enabled=egg_vision_cfg["enabled"],
        egg_vision_available=egg_vision_cfg["enabled"] and OPENCV_AVAILABLE and SKLEARN_AVAILABLE,
        egg_vision_training_enabled=training_cfg["enabled"],
    )


def _month_bounds(year, month):
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)
    return start, end


def _compute_summary(conn, now, year=None, month=None):
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())

    eggs_today = conn.execute(
        "SELECT COALESCE(SUM(count), 0) AS total FROM logs WHERE type = 'egg' AND ts >= ?",
        (today_start.isoformat(),),
    ).fetchone()["total"]

    eggs_week = conn.execute(
        "SELECT COALESCE(SUM(count), 0) AS total FROM logs WHERE type = 'egg' AND ts >= ?",
        (week_start.isoformat(),),
    ).fetchone()["total"]

    last_cleaning = conn.execute(
        "SELECT ts FROM logs WHERE type = 'cleaning' ORDER BY ts DESC LIMIT 1"
    ).fetchone()

    last_feeding = conn.execute(
        "SELECT ts FROM logs WHERE type = 'feeding' ORDER BY ts DESC LIMIT 1"
    ).fetchone()

    if year is None or month is None:
        year, month = now.year, now.month
    month_start, month_end = _month_bounds(year, month)

    eggs_collected_total = conn.execute(
        "SELECT COALESCE(SUM(count), 0) AS total FROM logs WHERE type = 'egg'"
    ).fetchone()["total"]

    eggs_sold_total = conn.execute(
        "SELECT COALESCE(SUM(count), 0) AS total FROM logs WHERE type = 'sale'"
    ).fetchone()["total"]

    eggs_used_total = conn.execute(
        "SELECT COALESCE(SUM(count), 0) AS total FROM logs WHERE type = 'used'"
    ).fetchone()["total"]

    eggs_used_month = conn.execute(
        "SELECT COALESCE(SUM(count), 0) AS total FROM logs WHERE type = 'used' AND ts >= ? AND ts < ?",
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchone()["total"]

    eggs_used_total_for_savings = conn.execute(
        "SELECT COALESCE(SUM(count), 0) AS total FROM logs "
        "WHERE type = 'used' AND (given_away IS NULL OR given_away = 0)"
    ).fetchone()["total"]

    eggs_used_month_for_savings = conn.execute(
        "SELECT COALESCE(SUM(count), 0) AS total FROM logs "
        "WHERE type = 'used' AND (given_away IS NULL OR given_away = 0) AND ts >= ? AND ts < ?",
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchone()["total"]

    revenue_month = conn.execute(
        "SELECT COALESCE(SUM(price), 0) AS total FROM logs WHERE type = 'sale' AND ts >= ? AND ts < ?",
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchone()["total"]

    cost_month = conn.execute(
        "SELECT COALESCE(SUM(cost), 0) AS total FROM logs WHERE type = 'expense' AND ts >= ? AND ts < ?",
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchone()["total"]

    revenue_total = conn.execute(
        "SELECT COALESCE(SUM(price), 0) AS total FROM logs WHERE type = 'sale'"
    ).fetchone()["total"]

    cost_total = conn.execute(
        "SELECT COALESCE(SUM(cost), 0) AS total FROM logs WHERE type = 'expense'"
    ).fetchone()["total"]

    egg_price_each = get_supermarket_egg_price()

    return {
        "eggs_today": eggs_today,
        "eggs_week": eggs_week,
        "last_cleaning": last_cleaning["ts"] if last_cleaning else None,
        "last_feeding": last_feeding["ts"] if last_feeding else None,
        "eggs_available": eggs_collected_total - eggs_sold_total - eggs_used_total,
        "month": f"{year:04d}-{month:02d}",
        "revenue_month": revenue_month,
        "cost_month": cost_month,
        "net_month": revenue_month - cost_month,
        "revenue_total": revenue_total,
        "cost_total": cost_total,
        "net_total": revenue_total - cost_total,
        "savings_month": eggs_used_month_for_savings * egg_price_each,
        "savings_total": eggs_used_total_for_savings * egg_price_each,
    }


@app.route("/api/summary")
def api_summary():
    db = get_db()
    now = datetime.now()

    month_param = request.args.get("month")
    try:
        year, month = (int(part) for part in month_param.split("-"))
    except (AttributeError, ValueError):
        year, month = None, None

    return jsonify(_compute_summary(db, now, year, month))


def _recent_month_starts(now, months):
    """The last `months` calendar months up to and including `now`'s
    month, oldest first, as (year, month) tuples."""
    months = max(1, min(months, 24))
    month_starts = []
    year, month = now.year, now.month
    for i in range(months):
        m = month - i
        y = year
        while m <= 0:
            m += 12
            y -= 1
        month_starts.append((y, m))
    month_starts.reverse()
    return month_starts


def _compute_trends(conn, now, months):
    month_starts = _recent_month_starts(now, months)
    labels = [f"{y:04d}-{m:02d}" for y, m in month_starts]
    range_start, _ = _month_bounds(*month_starts[0])

    def series_for(entry_type):
        rows = conn.execute(
            """
            SELECT strftime('%Y-%m', ts) AS ym, COALESCE(SUM(count), 0) AS total
            FROM logs
            WHERE type = ? AND ts >= ?
            GROUP BY ym
            """,
            (entry_type, range_start.isoformat()),
        ).fetchall()
        by_month = {row["ym"]: row["total"] for row in rows}
        return [by_month.get(label, 0) for label in labels]

    return {
        "months": labels,
        "collected": series_for("egg"),
        "sold": series_for("sale"),
        "used": series_for("used"),
    }


def _get_breed_annual_eggs(conn, breed_name):
    if not breed_name:
        return None
    row = conn.execute(
        "SELECT annual_eggs FROM breeds WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))",
        (breed_name,),
    ).fetchone()
    return row["annual_eggs"] if row else None


def _age_stage_multiplier(age_days):
    """Simple 3-stage laying curve: not yet laying, full rate through a
    "prime" window, reduced rate after — one universal shape applied to
    whatever the bird's breed's own annual rate is (see ARCHITECTURE.md
    §9 for why this isn't a more detailed multi-year curve)."""
    if age_days < POINT_OF_LAY_DAYS:
        return 0.0
    if age_days < PRIME_END_DAYS:
        return 1.0
    return REDUCED_RATE_MULTIPLIER


def _chicken_daily_rate(conn, chicken, now):
    annual_eggs = _get_breed_annual_eggs(conn, chicken["breed"])
    if not annual_eggs:
        return 0.0  # unknown/removed breed: no rate to go on

    if not chicken["hatch_date"]:
        stage_multiplier = 1.0  # unknown age: assume prime, the most forgiving default
    else:
        hatch = datetime.fromisoformat(chicken["hatch_date"])
        stage_multiplier = _age_stage_multiplier((now - hatch).days)

    return (annual_eggs / 365) * stage_multiplier


def _flock_baseline_daily_rate(conn, now):
    """The forecast's starting point before blending in actual history
    (see _forecast_daily_rate below): the sum of each active chicken's
    age-adjusted daily rate, or — if no chickens have been added yet — the
    flat per-breed counts (flock_isabrown_count / flock_sussex_count),
    kept for backward compatibility with installs from before individual
    tracking existed. Returns (basis, daily_rate) where basis is
    "individual" or "flat_counts", so callers can report which was used."""
    # Only the columns _chicken_daily_rate actually needs — a backtest
    # calls this once per historical month, so skipping the (potentially
    # large) photo blob here avoids re-reading it unnecessarily each time.
    chickens = conn.execute(
        "SELECT breed, hatch_date FROM chickens WHERE status = 'active'"
    ).fetchall()
    if chickens:
        return "individual", sum(_chicken_daily_rate(conn, c, now) for c in chickens)

    counts = get_flock_counts()
    isabrown_eggs = _get_breed_annual_eggs(conn, "Isabrown") or 0
    sussex_eggs = _get_breed_annual_eggs(conn, "Sussex") or 0
    baseline = counts["isabrown"] * (isabrown_eggs / 365) + counts["sussex"] * (sussex_eggs / 365)
    return "flat_counts", baseline


def _forecast_components(conn, now):
    """The season-independent pieces of the forecast, computed once per
    forecast: (flock_basis, baseline_daily, ratio, ever_logged).

    `baseline_daily` is the flat annual-mean rate from
    _flock_baseline_daily_rate; `ratio` compares the trailing actual rate
    against the *seasonally expected* rate as of `now` (baseline ×
    seasonal multiplier), clamped by FORECAST_RATIO_BOUNDS. Dividing by
    the seasonally expected rate — not the flat baseline — makes the ratio
    a season-independent flock-health signal: a seasonally normal winter
    low reads as ratio ≈ 1.0, not as a badly performing flock projected
    flatly into spring."""
    flock_basis, baseline_daily = _flock_baseline_daily_rate(conn, now)
    ever_logged = conn.execute(
        "SELECT COUNT(*) AS n FROM logs WHERE type = 'egg'"
    ).fetchone()["n"]
    if baseline_daily <= 0 or ever_logged == 0:
        return flock_basis, baseline_daily, 1.0, ever_logged

    window_start = now - timedelta(days=FORECAST_TRAILING_DAYS)
    actual_eggs = conn.execute(
        "SELECT COALESCE(SUM(count), 0) AS total FROM logs WHERE type = 'egg' AND ts >= ? AND ts <= ?",
        (window_start.isoformat(), now.isoformat()),
    ).fetchone()["total"]
    actual_daily = actual_eggs / FORECAST_TRAILING_DAYS

    ratio = actual_daily / (baseline_daily * _seasonal_multiplier(now))
    ratio = max(FORECAST_RATIO_BOUNDS[0], min(FORECAST_RATIO_BOUNDS[1], ratio))
    return flock_basis, baseline_daily, ratio, ever_logged


def _forecast_daily_rate(conn, now, when=None):
    """Expected eggs/day at `when` (default: `now`), blending a flock
    baseline with recent actual performance and the seasonal curve.
    Recomputed from scratch on every call — no stored model, no training
    step — so it "self-corrects" simply by always looking at the last
    FORECAST_TRAILING_DAYS as of `now`. Evaluated at `when == now` the
    seasonal terms cancel, so the blended "current" rate still equals the
    observed trailing rate; seasonality only reshapes projections at other
    dates."""
    _, baseline_daily, ratio, _ = _forecast_components(conn, now)
    return baseline_daily * _seasonal_multiplier(when or now) * ratio


def _compute_forecast(conn, now, months=FORECAST_MONTHS):
    flock_basis, baseline_daily, ratio, ever_logged = _forecast_components(conn, now)

    labels = []
    values = []
    year, month = now.year, now.month
    for i in range(1, months + 1):
        m = month + i
        y = year
        while m > 12:
            m -= 12
            y += 1
        start, end = _month_bounds(y, m)
        days_in_month = (end - start).days
        midpoint = start + (end - start) / 2
        rate = baseline_daily * _seasonal_multiplier(midpoint) * ratio
        labels.append(f"{y:04d}-{m:02d}")
        values.append(round(rate * days_in_month))

    return {
        "forecast_months": labels,
        "forecast_collected": values,
        "forecast_daily_rate": round(
            baseline_daily * _seasonal_multiplier(now) * ratio, 2
        ),
        "forecast_basis": "breed_standard" if ever_logged == 0 else "blended",
        "forecast_flock_basis": flock_basis,
    }


def _compute_backtest(conn, now, months):
    """For each of the same historical months _compute_trends just
    returned, what would the forecast have predicted for that month, using
    only data available as of that month's start? Reuses
    _forecast_daily_rate as-is — data cutoff at the month's start, seasonal
    factor at its midpoint, exactly the treatment the forward projection
    gives a future month, so the backtest stays a fair test of the shipped
    formula."""
    month_starts = _recent_month_starts(now, months)
    values = []
    for y, m in month_starts:
        month_start, month_end = _month_bounds(y, m)
        days_in_month = (month_end - month_start).days
        midpoint = month_start + (month_end - month_start) / 2
        daily_rate = _forecast_daily_rate(conn, month_start, when=midpoint)
        values.append(round(daily_rate * days_in_month))
    return {"forecast_backtest": values}


def _compute_forecast_margin(collected, backtest):
    """Mean absolute error between what the backtest predicted and what
    actually happened, over completed historical months only — excludes
    the last (current, still-partial) month, same reasoning
    _compute_backtest's own docstring gives: comparing a full-month
    projection against a partial actual isn't a fair test. None with no
    completed month to measure (a fresh install), so callers can suppress
    the uncertainty band entirely rather than draw one from zero data.

    Flat, not growing with forecast horizon: the backtest only ever tests
    a 1-month-ahead prediction (data cutoff at a month's start, predicting
    that same month) — there's no data here on how much worse a 3-month
    projection is than a 1-month one, so a flat margin is the only claim
    this data actually backs."""
    pairs = list(zip(collected, backtest))[:-1]
    if not pairs:
        return None
    errors = [abs(c - b) for c, b in pairs]
    return round(sum(errors) / len(errors))


@app.route("/api/trends")
def api_trends():
    db = get_db()
    now = datetime.now()
    try:
        months = int(request.args.get("months", 6))
    except ValueError:
        months = 6
    result = _compute_trends(db, now, months)
    result.update(_compute_forecast(db, now))
    result.update(_compute_backtest(db, now, months))
    result["forecast_margin"] = _compute_forecast_margin(
        result["collected"], result["forecast_backtest"]
    )
    return jsonify(result)


def _egg_history_span_months(conn, now):
    """Elapsed calendar months from the first-ever egg log to now
    (inclusive), capped at 24 — both the minimum-data gate and the
    fitting window for the advanced forecast below."""
    row = conn.execute("SELECT MIN(ts) AS first_ts FROM logs WHERE type = 'egg'").fetchone()
    if not row["first_ts"]:
        return 0
    first = datetime.fromisoformat(row["first_ts"])
    span = (now.year - first.year) * 12 + (now.month - first.month) + 1
    return max(0, min(span, 24))


def _compute_advanced_forecast(conn, now):
    """An independent, real statistical model (Holt-Winters) as a check
    against the hand-tuned forecast above — see ARCHITECTURE.md §19 for
    why this model, why it's gated the way it is, and why it's a separate
    endpoint rather than folded into /api/trends."""
    history_months = _egg_history_span_months(conn, now)
    result = {
        "advanced_libs_available": STATSMODELS_AVAILABLE,
        "advanced_libs_error": STATSMODELS_ERROR,
        "advanced_enabled": get_advanced_forecast_config()["enabled"],
        "advanced_error": None,
        "history_months": history_months,
        "min_months_required": ADVANCED_FORECAST_MIN_MONTHS,
        "seasonal_min_months_required": ADVANCED_FORECAST_SEASONAL_MIN_MONTHS,
        "model": None,
        "months": [],
        "collected": [],
        "advanced_months": [],
        "advanced_forecast": [],
        "advanced_ci_lower": [],
        "advanced_ci_upper": [],
    }
    if not STATSMODELS_AVAILABLE or not result["advanced_enabled"]:
        return result
    if history_months < ADVANCED_FORECAST_MIN_MONTHS:
        return result

    trends = _compute_trends(conn, now, history_months)
    seasonal = history_months >= ADVANCED_FORECAST_SEASONAL_MIN_MONTHS
    try:
        fit = ExponentialSmoothing(
            trends["collected"],
            trend="add",
            seasonal="add" if seasonal else None,
            seasonal_periods=12 if seasonal else None,
            initialization_method="estimated",
        ).fit()
        forecast_values = fit.forecast(FORECAST_MONTHS)
        # ExponentialSmoothing has no closed-form confidence interval (unlike
        # SARIMAX) — simulate repetitions of the fitted model and take
        # percentiles, the standard statsmodels approach for Holt-Winters CIs.
        sims = fit.simulate(nsimulations=FORECAST_MONTHS, repetitions=1000, error="add")
        ci_lower = np.percentile(sims, 2.5, axis=1)
        ci_upper = np.percentile(sims, 97.5, axis=1)
    except Exception as e:  # noqa: BLE001 - a fit failure degrades, never 500s
        result["advanced_error"] = str(e)
        return result

    result.update(
        {
            "model": "holt_winters_seasonal" if seasonal else "holt_winters_trend",
            "months": trends["months"],
            "collected": trends["collected"],
            "advanced_months": _compute_forecast(conn, now)["forecast_months"],
            "advanced_forecast": [max(0, round(v)) for v in forecast_values],
            "advanced_ci_lower": [max(0, round(v)) for v in ci_lower],
            "advanced_ci_upper": [max(0, round(v)) for v in ci_upper],
        }
    )
    return result


@app.route("/api/trends/advanced")
def api_trends_advanced():
    return jsonify(_compute_advanced_forecast(get_db(), datetime.now()))


def _compute_feeding_stats(conn, food_type, now):
    food_type = (food_type or "").strip()
    if not food_type:
        return {
            "food_type": food_type,
            "total_feedings": 0,
            "empty_count": 0,
            "last_empty": None,
            "days_since_last_empty": None,
            "avg_days_between_empty": None,
        }

    total_feedings = conn.execute(
        "SELECT COUNT(*) AS n FROM logs WHERE type = 'feeding' AND LOWER(TRIM(food_type)) = LOWER(TRIM(?))",
        (food_type,),
    ).fetchone()["n"]

    rows = conn.execute(
        """
        SELECT ts FROM logs
        WHERE type = 'feeding' AND container_empty = 1 AND LOWER(TRIM(food_type)) = LOWER(TRIM(?))
        ORDER BY ts ASC
        """,
        (food_type,),
    ).fetchall()
    timestamps = [datetime.fromisoformat(row["ts"]) for row in rows]

    avg_days_between_empty = None
    if len(timestamps) >= 2:
        intervals = [
            (timestamps[i] - timestamps[i - 1]).total_seconds() / 86400
            for i in range(1, len(timestamps))
        ]
        avg_days_between_empty = round(sum(intervals) / len(intervals), 1)

    last_empty = timestamps[-1] if timestamps else None

    return {
        "food_type": food_type,
        "total_feedings": total_feedings,
        "empty_count": len(timestamps),
        "last_empty": last_empty.isoformat() if last_empty else None,
        "days_since_last_empty": (
            round((now - last_empty).total_seconds() / 86400, 1) if last_empty else None
        ),
        "avg_days_between_empty": avg_days_between_empty,
    }


def _compute_all_feeding_stats(conn, now):
    """Feeding stats (see _compute_feeding_stats) for every food type that
    has ever actually been logged — not just the ones currently in the
    food_types management list, so removing one doesn't drop its history
    from this retrospective summary."""
    rows = conn.execute(
        """
        SELECT DISTINCT food_type FROM logs
        WHERE type = 'feeding' AND food_type IS NOT NULL AND TRIM(food_type) != ''
        ORDER BY food_type COLLATE NOCASE ASC
        """
    ).fetchall()
    return [_compute_feeding_stats(conn, row["food_type"], now) for row in rows]


@app.route("/api/feeding-stats")
def api_feeding_stats():
    db = get_db()
    return jsonify(_compute_feeding_stats(db, request.args.get("food_type", ""), datetime.now()))


@app.route("/api/feeding-stats-all")
def api_feeding_stats_all():
    db = get_db()
    return jsonify(_compute_all_feeding_stats(db, datetime.now()))


@app.route("/api/food-types")
def api_food_types():
    db = get_db()
    rows = db.execute("SELECT id, name FROM food_types ORDER BY id ASC").fetchall()
    return jsonify([dict(row) for row in rows])


@app.route("/api/food-types", methods=["POST"])
def api_add_food_type():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    db = get_db()
    existing = db.execute(
        "SELECT id FROM food_types WHERE LOWER(name) = LOWER(?)", (name,)
    ).fetchone()
    if existing:
        return jsonify({"error": "that food type is already in the list"}), 400

    cur = db.execute("INSERT INTO food_types (name) VALUES (?)", (name,))
    db.commit()
    return jsonify({"id": cur.lastrowid, "name": name}), 201


@app.route("/api/food-types/<int:food_type_id>", methods=["DELETE"])
def api_delete_food_type(food_type_id):
    db = get_db()
    db.execute("DELETE FROM food_types WHERE id = ?", (food_type_id,))
    db.commit()
    return "", 204


@app.route("/api/breeds")
def api_breeds():
    db = get_db()
    rows = db.execute("SELECT id, name, annual_eggs FROM breeds ORDER BY id ASC").fetchall()
    return jsonify([dict(row) for row in rows])


@app.route("/api/breeds", methods=["POST"])
def api_add_breed():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    try:
        annual_eggs = int(data.get("annual_eggs"))
    except (TypeError, ValueError):
        return jsonify({"error": "annual_eggs must be a number"}), 400
    if annual_eggs <= 0:
        return jsonify({"error": "annual_eggs must be positive"}), 400

    db = get_db()
    existing = db.execute("SELECT id FROM breeds WHERE LOWER(name) = LOWER(?)", (name,)).fetchone()
    if existing:
        return jsonify({"error": "that breed is already in the list"}), 400

    cur = db.execute("INSERT INTO breeds (name, annual_eggs) VALUES (?, ?)", (name, annual_eggs))
    db.commit()
    return jsonify({"id": cur.lastrowid, "name": name, "annual_eggs": annual_eggs}), 201


@app.route("/api/breeds/<int:breed_id>", methods=["DELETE"])
def api_delete_breed(breed_id):
    db = get_db()
    db.execute("DELETE FROM breeds WHERE id = ?", (breed_id,))
    db.commit()
    return "", 204


def _parse_date_field(value, label):
    if not value:
        return None, None
    try:
        return datetime.fromisoformat(value).date().isoformat(), None
    except ValueError:
        return None, f"invalid {label}"


def _parse_hatch_date(value):
    return _parse_date_field(value, "hatch_date")


def _decode_photo_data_uri(data_uri, max_bytes=None):
    """Decodes a `data:image/...;base64,...` string (as produced by a
    client-side canvas resize) into raw bytes. `max_bytes` defaults to the
    chicken-photo cap (resolved at call time, not import time, so tests can
    still monkeypatch MAX_PHOTO_BYTES); the egg-vision endpoint passes a
    larger one since that image is analyzed, not stored — see
    MAX_EGG_VISION_PHOTO_BYTES."""
    if max_bytes is None:
        max_bytes = MAX_PHOTO_BYTES
    try:
        _, encoded = data_uri.split(",", 1)
        photo_bytes = base64.b64decode(encoded)
    except (ValueError, binascii.Error):
        return None, "invalid photo data"
    if len(photo_bytes) > max_bytes:
        return None, "photo is too large"
    return photo_bytes, None


def _egg_size_code(width_mm):
    """Buckets a measured egg width (mm) into S/M/L/XL via
    EGG_SIZE_MM_BOUNDS — see ARCHITECTURE.md §20 for the derivation and
    its honesty caveat (width, not weight). This is the fallback used
    whenever no trained size model exists — see _egg_size_code_ml."""
    s_m, m_l, l_xl = EGG_SIZE_MM_BOUNDS
    if width_mm < s_m:
        return "S"
    if width_mm < m_l:
        return "M"
    if width_mm < l_xl:
        return "L"
    return "XL"


def _ml_is_egg(candidate, classifier):
    """Applies a trained scikit-learn classifier — replaces the fixed
    EGG_MAX_ASPECT cutoff once a model has been trained on this install's
    own corrections. See ARCHITECTURE.md §20 addendum."""
    x = [[candidate["area_fraction"], candidate["aspect_ratio"], candidate["extent"], candidate["solidity"]]]
    return bool(classifier.predict(x)[0])


def _egg_size_code_ml(width_mm, aspect_ratio, extent, size_model):
    """Applies a trained scikit-learn multi-class classifier — replaces
    EGG_SIZE_MM_BOUNDS once a model has been trained on this install's
    own corrections."""
    x = [[width_mm, aspect_ratio, extent]]
    return size_model.predict(x)[0]


def _order_corners_clockwise(pts):
    """Orders 4 (x,y) points as top-left, top-right, bottom-right,
    bottom-left — the coordinate sum is smallest at top-left/largest at
    bottom-right, and the y−x difference is smallest at top-right/largest
    at bottom-left, regardless of the polygon's original winding order
    from approxPolyDP."""
    pts = np.asarray(pts, dtype=np.float64)
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).flatten()
    return [
        pts[np.argmin(s)].tolist(),
        pts[np.argmin(diff)].tolist(),
        pts[np.argmax(s)].tolist(),
        pts[np.argmax(diff)].tolist(),
    ]


def _detect_box_walls(gray, blurred):
    """Finds a nesting box's interior as two (possibly slanted) side-wall
    lines. A box interior is a large, high-contrast region against its
    walls/bedding — the same "obvious, hard-to-miss geometric primitive"
    reasoning that made Hough circles the right fit for a coin in the
    original design. When the winning contour simplifies to a convex
    4-gon, its left and right edges become the wall lines — capturing
    the walls converging with depth when a handheld camera looks into
    the box; otherwise the bounding rectangle's vertical edges are used
    (a zero-slant trapezoid). See ARCHITECTURE.md §20 addendum.

    Unlike the coin/egg case, the box is typically the MAJORITY of the
    frame, not a small minority blob — so this can't assume "the smaller
    class is the foreground". Instead it tries both polarities of the
    Otsu split and keeps whichever produces a large-but-not-degenerate
    contour (a contour close to the full photo's bounds is almost always
    the image border itself, e.g. when the background — not the box —
    ends up as the "foreground" class, since background typically
    touches all 4 edges of a photo).

    Returns {top_y, bottom_y, left_top_x, left_bottom_x, right_top_x,
    right_bottom_x} in pixels, or None if nothing qualifies (box out of
    frame / heavily occluded / no plain background to contrast against)
    — the caller always falls back to a default so the user has
    draggable handles to correct, never a dead end."""
    _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    photo_area = gray.shape[0] * gray.shape[1]
    kernel = np.ones((15, 15), np.uint8)
    best = None
    for candidate in (thresh, cv2.bitwise_not(thresh)):
        closed = cv2.morphologyEx(candidate, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        largest = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(largest)
        if area < BOX_MIN_AREA_FRACTION * photo_area or area > 0.98 * photo_area:
            continue
        if best is None or area < cv2.contourArea(best):
            best = largest  # prefer the tighter of the two valid candidates
    if best is None:
        return None

    approx = cv2.approxPolyDP(best, 0.02 * cv2.arcLength(best, True), True)
    if len(approx) == 4 and cv2.isContourConvex(approx):
        tl, tr, br, bl = _order_corners_clockwise(approx.reshape(4, 2))
        top_y = (tl[1] + tr[1]) / 2
        bottom_y = (bl[1] + br[1]) / 2
        if bottom_y > top_y:
            return {
                "top_y": float(top_y),
                "bottom_y": float(bottom_y),
                "left_top_x": float(tl[0]),
                "left_bottom_x": float(bl[0]),
                "right_top_x": float(tr[0]),
                "right_bottom_x": float(br[0]),
            }

    x, y, w, h = cv2.boundingRect(best)
    return {
        "top_y": float(y),
        "bottom_y": float(y + h),
        "left_top_x": float(x),
        "left_bottom_x": float(x),
        "right_top_x": float(x + w),
        "right_bottom_x": float(x + w),
    }


def _default_box_walls(w, h):
    """Centered inset guess when auto-detection fails — mirrors the
    original coin's default center/radius guess in app.js so the user
    always has something draggable."""
    return {
        "top_y": h * 0.1,
        "bottom_y": h * 0.9,
        "left_top_x": w * 0.1,
        "left_bottom_x": w * 0.1,
        "right_top_x": w * 0.9,
        "right_bottom_x": w * 0.9,
    }


def _normalize_box_walls(walls):
    """Accepts either the current trapezoid shape or the pre-1.32 flat
    {left, top, right, bottom} rectangle (still present in samples stored
    by 1.31.x, which keep training the size model) and returns the
    trapezoid shape, or None if the input is unusable."""
    if not isinstance(walls, dict):
        return None
    if "top_y" in walls:
        required = ("top_y", "bottom_y", "left_top_x", "left_bottom_x", "right_top_x", "right_bottom_x")
        if not all(k in walls for k in required):
            return None
        return walls
    if all(k in walls for k in ("left", "top", "right", "bottom")):
        return {
            "top_y": walls["top"],
            "bottom_y": walls["bottom"],
            "left_top_x": walls["left"],
            "left_bottom_x": walls["left"],
            "right_top_x": walls["right"],
            "right_bottom_x": walls["right"],
        }
    return None


def _wall_px_per_mm_at(walls, y, width_mm):
    """Local pixels-per-mm at image row y: each wall's x is linearly
    interpolated between its top and bottom endpoints (y clamped to the
    wall segment), and the local wall-to-wall span is scaled by the
    box's known real-world width. This is what makes an egg near the
    (farther, narrower-looking) back of an angled photo measure the same
    real size as one near the front. Returns None when the geometry is
    degenerate (zero-height walls, crossed walls, no width)."""
    if not width_mm:
        return None
    span_y = walls["bottom_y"] - walls["top_y"]
    if span_y <= 0:
        return None
    t = min(max((y - walls["top_y"]) / span_y, 0.0), 1.0)
    x_left = walls["left_top_x"] + t * (walls["left_bottom_x"] - walls["left_top_x"])
    x_right = walls["right_top_x"] + t * (walls["right_bottom_x"] - walls["right_top_x"])
    span = x_right - x_left
    if span <= 0:
        return None
    return span / width_mm


def _crop_to_box(img, box_walls):
    """Crops to the detected/default box interior (the trapezoid's
    bounding box) before running egg detection. Restores a clean
    two-class (bedding vs egg) scene for the color-distance pass, and is
    the semantically correct thing to do anyway — there's no reason to
    go looking for eggs outside the box. Returns (cropped_img, offset_x,
    offset_y); callers add the offset back onto every candidate's cx/cy
    so downstream consumers keep working in full-photo pixel
    coordinates. See ARCHITECTURE.md §20 addendum."""
    h, w = img.shape[:2]
    left = max(0, int(min(box_walls["left_top_x"], box_walls["left_bottom_x"])))
    top = max(0, int(box_walls["top_y"]))
    right = min(w, int(max(box_walls["right_top_x"], box_walls["right_bottom_x"])))
    bottom = min(h, int(box_walls["bottom_y"]))
    if right <= left or bottom <= top:
        return img, 0, 0
    return img[top:bottom, left:right], left, top


def _extract_egg_candidates(img):
    """Color-distance + contour pass shared by live inference
    (_analyze_egg_photo) and training-time re-extraction
    (_train_egg_vision_models), so a model is always trained on exactly
    the features it's later applied against.

    Eggs are found as regions whose Lab color differs from the bedding's
    estimated color — the bedding estimate is the per-channel median of
    the (box-cropped) photo, robust because bedding dominates the crop's
    area while eggs are the minority. Distance is chroma-weighted
    (EGG_BEDDING_CHROMA_WEIGHT) so a brown egg on pale straw — nearly
    identical in brightness, clearly different in color — separates,
    while bedding shadows (brightness-only differences) mostly don't.
    Hue-agnostic by design: any egg color that contrasts with the
    bedding works, including future green/blue eggs; an egg colored
    almost exactly like its bedding remains the documented hard case.
    The threshold is Otsu on the distance map with an absolute floor
    (EGG_MIN_COLOR_DISTANCE) so an eggless, near-uniform scene doesn't
    get its sensor noise split into phantom regions.

    Returns every contour above the tiny-noise floor
    (EGG_MIN_AREA_FRACTION) with a full feature dict — deliberately
    applies NO upper-area or aspect-ratio filtering here; that "is this
    plausibly one egg" decision belongs to the caller (hardcoded
    defaults, or a trained classifier — see ARCHITECTURE.md §20
    addendum)."""
    h, w = img.shape[:2]
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
    bedding = np.median(lab.reshape(-1, 3), axis=0)
    diff = lab - bedding
    dist = np.sqrt(
        (diff[:, :, 0] * EGG_BEDDING_L_WEIGHT) ** 2
        + (diff[:, :, 1] * EGG_BEDDING_CHROMA_WEIGHT) ** 2
        + (diff[:, :, 2] * EGG_BEDDING_CHROMA_WEIGHT) ** 2
    )
    dist_u8 = np.clip(dist, 0, 255).astype(np.uint8)
    dist_u8 = cv2.GaussianBlur(dist_u8, (9, 9), 2)
    otsu_t, _ = cv2.threshold(dist_u8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, thresh = cv2.threshold(dist_u8, max(otsu_t, EGG_MIN_COLOR_DISTANCE), 255, cv2.THRESH_BINARY)
    kernel = np.ones((5, 5), np.uint8)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    photo_area = h * w
    candidates = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < EGG_MIN_AREA_FRACTION * photo_area:
            continue
        if len(c) < 5:
            continue  # fitEllipse needs >= 5 points
        (ex, ey), (minor, major), angle = cv2.fitEllipse(c)
        if minor <= 0:
            continue
        bx, by, bw, bh = cv2.boundingRect(c)
        hull_area = cv2.contourArea(cv2.convexHull(c))
        candidates.append(
            {
                "cx": float(ex),
                "cy": float(ey),
                "width_px": float(minor),
                "height_px": float(major),
                "angle": float(angle),
                "area_fraction": float(area / photo_area),
                "aspect_ratio": float(major / minor),
                "extent": float(area / (bw * bh)) if bw and bh else 0.0,
                "solidity": float(area / hull_area) if hull_area > 0 else 0.0,
            }
        )
    return candidates


# Box-ID embedder: a frozen, pretrained SqueezeNet 1.1 loaded once per
# process via cv2.dnn and shared across requests. waitress serves from
# multiple threads and cv2.dnn's Net.forward() isn't documented
# thread-safe, so both lazy initialization and every forward pass hold
# _box_embedder_lock — box-ID runs at most once per photo analysis and
# once per training sample, so the serialization cost is irrelevant.
_box_embedder_lock = threading.Lock()
_box_embedder = None  # {"net": cv2.dnn_Net|None, "dim": int|None, "error": str|None}


def _get_box_embedder():
    global _box_embedder
    with _box_embedder_lock:
        if _box_embedder is not None:
            return _box_embedder
        try:
            if not os.path.exists(EGG_VISION_BOX_EMBED_MODEL_PATH):
                raise FileNotFoundError(f"model file missing: {EGG_VISION_BOX_EMBED_MODEL_PATH}")
            net = cv2.dnn.readNetFromONNX(EGG_VISION_BOX_EMBED_MODEL_PATH)
            probe = np.zeros((1, 3, EGG_VISION_BOX_EMBED_INPUT_SIZE, EGG_VISION_BOX_EMBED_INPUT_SIZE), np.float32)
            net.setInput(probe)
            out = net.forward()
            dim = int(out.size)
            if dim < 256 or not np.isfinite(out).all():
                raise RuntimeError(f"unexpected embedder output (size {dim})")
            _box_embedder = {"net": net, "dim": dim, "error": None}
        except Exception as e:  # noqa: BLE001 - embedder failure degrades to confirm_box, never crashes
            _box_embedder = {"net": None, "dim": None, "error": str(e)}
        return _box_embedder


def _box_embedder_status():
    emb = _get_box_embedder()
    return {
        "available": emb["net"] is not None,
        "error": emb["error"],
        "model_path": EGG_VISION_BOX_EMBED_MODEL_PATH,
        "dim": emb["dim"],
    }


def _embed_box_photo(img):
    """L2-normalized embedding of the full photo through the bundled
    SqueezeNet — this ONNX export's default output IS the flattened
    global-pooled conv10 features (no softmax layer in the graph,
    verified empirically), so no intermediate-layer tap is needed.
    Preprocessing is explicit numpy (not blobFromImage) because
    per-channel std division isn't expressible as blobFromImage's scalar
    scale. Plain 224x224 resize, aspect distortion accepted — it's
    identical at train and inference time, and keeping the full frame
    preserves context (mounting position, surroundings) that is itself
    discriminative signal. Returns None if the embedder is unavailable."""
    emb = _get_box_embedder()
    if emb["net"] is None:
        return None
    small = cv2.resize(img, (EGG_VISION_BOX_EMBED_INPUT_SIZE, EGG_VISION_BOX_EMBED_INPUT_SIZE))
    rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    rgb = (rgb - np.array(EGG_VISION_BOX_EMBED_MEAN, np.float32)) / np.array(EGG_VISION_BOX_EMBED_STD, np.float32)
    blob = rgb.transpose(2, 0, 1)[np.newaxis, ...]
    with _box_embedder_lock:
        emb["net"].setInput(blob)
        out = emb["net"].forward()
    vec = out.flatten().astype(np.float32)
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec


def _predict_box_id(img, box_classifier, box_classifier_labels):
    """Returns (box_id, similarity, margin) for the best-matching
    registered box, or (None, 0.0, 0.0) whenever no trustworthy
    prediction can be made: no trained head, a stale pre-1.32 head (a
    pickled sklearn object instead of the current dict format), an
    embedding-dimension mismatch after an OpenCV/model change, or the
    embedder being unavailable. The caller requires BOTH
    similarity >= EGG_VISION_BOX_ID_MIN_SIMILARITY and
    margin >= EGG_VISION_BOX_ID_MIN_MARGIN before trusting the answer —
    anything else falls back to asking the user (confirm_box), never a
    silent wrong guess or a 500."""
    try:
        if not isinstance(box_classifier, dict) or box_classifier.get("format") != EGG_VISION_BOX_MODEL_FORMAT:
            return None, 0.0, 0.0
        vec = _embed_box_photo(img)
        if vec is None or vec.shape[0] != box_classifier["dim"]:
            return None, 0.0, 0.0
        centered = vec - np.asarray(box_classifier["mean"], np.float32)
        norm = float(np.linalg.norm(centered))
        if norm <= 0:
            return None, 0.0, 0.0
        centered /= norm
        sims = sorted(
            ((float(np.dot(centered, np.asarray(c, np.float32))), int(bid)) for bid, c in box_classifier["centroids"].items()),
            reverse=True,
        )
        best_sim, best_id = sims[0]
        margin = best_sim - sims[1][0] if len(sims) > 1 else 1.0
        return best_id, best_sim, margin
    except Exception:  # noqa: BLE001 - any malformed head degrades to confirm_box
        return None, 0.0, 0.0


def _analyze_egg_photo(photo_bytes, box, classifier=None):
    """Classical CV egg count + size estimate — see ARCHITECTURE.md §20
    (and its addendum) for why classical CV is the base layer and the
    full failure-mode reasoning. Never raises for "nothing found"; only
    for a genuinely undecodable image.

    `box` is {"id":.., "width_mm":..} — which registered nesting box
    this photo is of, resolved by the caller (explicit selection, the
    sole registered box, or a confident box-ID classifier match — see
    api_vision_eggs/_resolve_egg_vision_box). `classifier` is an
    optional trained scikit-learn egg/not-egg model (see _ml_is_egg);
    when None (the default, and the case for every install that hasn't
    opted in and trained), egg detection is exactly the original
    fixed-threshold implementation. Size bucketing (S/M/L/XL) is
    deliberately NOT done here — see _apply_egg_sizes, called by the
    endpoint only once status is "ok", mirroring how a missing coin used
    to leave `size` unset in the original coin-based design."""
    arr = np.frombuffer(photo_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return {
            "status": "error",
            "error": "couldn't decode image",
            "image_width": None,
            "image_height": None,
            "eggs": [],
            "box_walls": None,
            "box": None,
        }

    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (9, 9), 2)

    detected = _detect_box_walls(gray, blurred)
    walls_found = detected is not None
    box_walls = detected or _default_box_walls(w, h)

    cropped, offset_x, offset_y = _crop_to_box(img, box_walls)
    candidates = _extract_egg_candidates(cropped)
    for c in candidates:
        c["cx"] += offset_x
        c["cy"] += offset_y

    eggs = []
    for c in candidates:
        if c["area_fraction"] > EGG_MAX_AREA_FRACTION:
            continue  # always-applied hard ceiling, model or not — see ARCHITECTURE.md §20 addendum
        keep = _ml_is_egg(c, classifier) if classifier is not None else (c["aspect_ratio"] <= EGG_MAX_ASPECT)
        if not keep:
            continue
        # Local (per-row) scale, not one global factor — see _wall_px_per_mm_at.
        px_per_mm = _wall_px_per_mm_at(box_walls, c["cy"], box.get("width_mm"))
        width_mm = (c["width_px"] / px_per_mm) if px_per_mm else None
        eggs.append(
            {
                "cx": c["cx"],
                "cy": c["cy"],
                "width_px": c["width_px"],
                "height_px": c["height_px"],
                "angle": c["angle"],
                "aspect_ratio": c["aspect_ratio"],
                "extent": c["extent"],
                "width_mm": width_mm,
            }
        )

    status = "ok"
    if not eggs:
        status = "no_eggs_found"
    elif not walls_found:
        status = "walls_not_found"

    return {
        "status": status,
        "error": None,
        "image_width": w,
        "image_height": h,
        "eggs": eggs,
        "box_walls": box_walls,
        "box": box,
    }


def _resolve_egg_vision_box(db, box_id):
    """Resolves which registered nesting box a photo is of, for the
    cases that don't need the photo itself:
      - explicit box_id given and it exists -> ("ok", box)
      - no box_id, but exactly one box is registered -> ("ok", box)
        (nothing to disambiguate, so auto-identification is skipped
        entirely — see ARCHITECTURE.md §20 addendum)
      - no boxes registered at all -> ("no_boxes_registered", None)
      - no box_id and >=2 boxes registered -> ("confirm_box", None):
        the caller must decode the photo and try the box-ID classifier
        (see api_vision_eggs)."""
    if box_id is not None:
        row = db.execute("SELECT * FROM nesting_boxes WHERE id = ?", (box_id,)).fetchone()
        if row is not None:
            return "ok", {"id": row["id"], "name": row["name"], "width_mm": row["width_mm"]}
    boxes = db.execute("SELECT * FROM nesting_boxes ORDER BY id ASC").fetchall()
    if not boxes:
        return "no_boxes_registered", None
    if len(boxes) == 1:
        row = boxes[0]
        return "ok", {"id": row["id"], "name": row["name"], "width_mm": row["width_mm"]}
    return "confirm_box", None


def _load_egg_vision_model(db):
    row = db.execute("SELECT * FROM egg_vision_models").fetchone()
    if row is None:
        return None

    def _safe_unpickle(blob):
        # A corrupt or format-incompatible blob (e.g. a pre-1.32 pickle
        # whose class layout no longer matches) must degrade to "no
        # model", never 500 the analyze endpoint — retraining rebuilds
        # everything from the retained sample photos.
        if not blob:
            return None
        try:
            return pickle.loads(blob)
        except Exception:  # noqa: BLE001
            return None

    return {
        "classifier": _safe_unpickle(row["classifier_blob"]),
        "size_model": _safe_unpickle(row["size_model_blob"]),
        "box_classifier": _safe_unpickle(row["box_classifier_blob"]),
        "box_classifier_labels": json.loads(row["box_classifier_labels"]) if row["box_classifier_labels"] else None,
    }


def _apply_egg_sizes(eggs, size_model):
    for egg in eggs:
        if egg["width_mm"] is None:
            continue
        egg["size"] = (
            _egg_size_code_ml(egg["width_mm"], egg["aspect_ratio"], egg["extent"], size_model)
            if size_model is not None
            else _egg_size_code(egg["width_mm"])
        )


@app.route("/api/vision/eggs", methods=["POST"])
def api_vision_eggs():
    cfg = get_egg_vision_config()
    empty = {"eggs": [], "box_walls": None, "box": None}
    if not cfg["enabled"]:
        return jsonify({"status": "disabled", "error": None, **empty})
    if not OPENCV_AVAILABLE:
        return jsonify({"status": "libs_unavailable", "error": OPENCV_ERROR, **empty})

    data = request.get_json(force=True, silent=True) or {}
    photo_bytes, err = _decode_photo_data_uri(data.get("photo") or "", max_bytes=MAX_EGG_VISION_PHOTO_BYTES)
    if err:
        return jsonify({"error": err}), 400

    db = get_db()
    box_status, box = _resolve_egg_vision_box(db, data.get("box_id"))
    model = _load_egg_vision_model(db)

    if box_status == "no_boxes_registered":
        return jsonify({"status": "no_boxes_registered", "error": None, **empty})

    if box_status == "confirm_box":
        arr = np.frombuffer(photo_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        predicted_id, similarity, margin = (None, 0.0, 0.0)
        if img is not None and model and model["box_classifier"] is not None:
            predicted_id, similarity, margin = _predict_box_id(
                img, model["box_classifier"], model["box_classifier_labels"]
            )
        if (
            predicted_id is not None
            and similarity >= EGG_VISION_BOX_ID_MIN_SIMILARITY
            and margin >= EGG_VISION_BOX_ID_MIN_MARGIN
        ):
            row = db.execute("SELECT * FROM nesting_boxes WHERE id = ?", (predicted_id,)).fetchone()
            if row is not None:
                box_status, box = "ok", {"id": row["id"], "name": row["name"], "width_mm": row["width_mm"]}
        if box_status != "ok":
            candidates = [dict(row) for row in db.execute("SELECT id, name FROM nesting_boxes ORDER BY id ASC")]
            # confidence + margin are tuning telemetry (readable from
            # devtools against real photos) — app.js only consumes
            # box_candidates.
            return jsonify(
                {
                    "status": "confirm_box",
                    "error": None,
                    **empty,
                    "box_candidates": candidates,
                    "predicted_box_id": predicted_id,
                    "confidence": similarity,
                    "margin": margin,
                }
            )

    try:
        result = _analyze_egg_photo(photo_bytes, box, classifier=(model["classifier"] if model else None))
    except Exception as e:  # noqa: BLE001 - analysis failure degrades, never 500s
        return jsonify({"status": "error", "error": str(e), **empty, "box": box})

    if result["status"] == "ok":
        _apply_egg_sizes(result["eggs"], model["size_model"] if model else None)

    return jsonify(result)


@app.route("/api/vision/eggs/recompute", methods=["POST"])
def api_vision_eggs_recompute():
    """Re-runs local width scaling + sizing against corrected box walls
    and the eggs' already-known pixel geometry — no image decode needed.
    Fired on pointerup while dragging a wall endpoint handle. The scale
    math is cheap enough that this could be done client-side, but sizing
    (once a trained scikit-learn size model exists) can't be — routing
    both through one endpoint avoids two implementations of the same
    math drifting apart."""
    data = request.get_json(force=True, silent=True) or {}
    box_id = data.get("box_id")
    walls = _normalize_box_walls(data.get("box_walls"))
    if box_id is None or walls is None:
        return jsonify({"error": "box_id and box_walls are required"}), 400
    box = get_db().execute("SELECT * FROM nesting_boxes WHERE id = ?", (box_id,)).fetchone()
    if box is None:
        return jsonify({"error": "no such nesting box"}), 400

    model = _load_egg_vision_model(get_db())
    size_model = model["size_model"] if model else None
    out = []
    for e in data.get("eggs", []):
        px_per_mm = _wall_px_per_mm_at(walls, e["cy"], box["width_mm"])
        width_mm = (e["width_px"] / px_per_mm) if px_per_mm else None
        size = None
        if width_mm is not None:
            size = (
                _egg_size_code_ml(width_mm, e["aspect_ratio"], e["extent"], size_model)
                if size_model is not None
                else _egg_size_code(width_mm)
            )
        out.append({"width_mm": width_mm, "size": size})
    return jsonify({"eggs": out})


@app.route("/api/nesting-boxes")
def api_nesting_boxes():
    db = get_db()
    rows = db.execute("SELECT id, name, width_mm, created_at FROM nesting_boxes ORDER BY id ASC").fetchall()
    return jsonify([dict(row) for row in rows])


@app.route("/api/nesting-boxes", methods=["POST"])
def api_add_nesting_box():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    try:
        width_mm = float(data.get("width_mm"))
    except (TypeError, ValueError):
        return jsonify({"error": "width_mm must be a number"}), 400
    if width_mm <= 0:
        return jsonify({"error": "width_mm must be positive"}), 400

    db = get_db()
    existing = db.execute("SELECT id FROM nesting_boxes WHERE LOWER(name) = LOWER(?)", (name,)).fetchone()
    if existing:
        return jsonify({"error": "a nesting box with that name already exists"}), 400

    created_at = datetime.now().isoformat()
    cur = db.execute(
        "INSERT INTO nesting_boxes (name, width_mm, created_at) VALUES (?, ?, ?)",
        (name, width_mm, created_at),
    )
    db.commit()
    return jsonify({"id": cur.lastrowid, "name": name, "width_mm": width_mm, "created_at": created_at}), 201


@app.route("/api/nesting-boxes/<int:box_id>", methods=["DELETE"])
def api_delete_nesting_box(box_id):
    # Un-link rather than cascade: preserve already-collected training
    # photos even if their box definition is later removed.
    db = get_db()
    db.execute("UPDATE egg_vision_samples SET box_id = NULL WHERE box_id = ?", (box_id,))
    db.execute("DELETE FROM nesting_boxes WHERE id = ?", (box_id,))
    db.commit()
    return "", 204


def _match_candidate(candidate, corrected_eggs, used):
    """Nearest-center match between a re-extracted CV candidate and the
    user's corrected egg list, tolerance scaled to egg size — used to
    label training examples (matched = positive, unmatched = negative)."""
    best_i, best_d = None, None
    for i, egg in enumerate(corrected_eggs):
        if i in used:
            continue
        d = math.hypot(candidate["cx"] - egg["cx"], candidate["cy"] - egg["cy"])
        tol = 0.5 * max(candidate["width_px"], egg["width_px"])
        if d <= tol and (best_d is None or d < best_d):
            best_i, best_d = i, d
    return best_i


def _train_egg_vision_models(samples):
    """Re-extracts CV candidates from each stored sample's photo (via the
    same _extract_egg_candidates pass live inference uses, so a model is
    always trained on exactly the features it's later applied against)
    and matches them against the user's corrected result to build labeled
    training examples for three independent models: the egg/not-egg
    classifier and the size classifier (scikit-learn), and — once >=2
    boxes have enough samples — the box-ID head (nearest-centroid over
    SqueezeNet embeddings, a plain dict, no sklearn). Deterministic where
    it can be: the color-distance/contour pass has no randomness, so a
    candidate the old fixed heuristic would have excluded, and a
    candidate the user explicitly removed with the chip's X, both
    surface here as classifier negatives the same way. See
    ARCHITECTURE.md §20 addendum."""
    classifier_X, classifier_y = [], []
    size_X, size_y = [], []
    box_X, box_y = [], []
    box_sample_counts = {}

    for row in samples:
        try:
            corrected = json.loads(row["corrected_result"])
        except (TypeError, ValueError):
            continue
        corrected_eggs = corrected.get("eggs") or []
        box_walls = _normalize_box_walls(corrected.get("box_walls"))
        box_width_mm = corrected.get("box_width_mm")
        box_id = row["box_id"]

        arr = np.frombuffer(row["photo"], dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            continue

        if box_id is not None:
            emb = _embed_box_photo(img)
            if emb is not None:
                box_X.append(emb)
                box_y.append(box_id)
                box_sample_counts[box_id] = box_sample_counts.get(box_id, 0) + 1

        if box_walls is None or not box_width_mm:
            continue

        cropped, offset_x, offset_y = _crop_to_box(img, box_walls)
        candidates = _extract_egg_candidates(cropped)
        for c in candidates:
            c["cx"] += offset_x
            c["cy"] += offset_y
        used = set()
        for c in candidates:
            match_i = _match_candidate(c, corrected_eggs, used)
            classifier_X.append([c["area_fraction"], c["aspect_ratio"], c["extent"], c["solidity"]])
            if match_i is None:
                classifier_y.append(0)
                continue
            used.add(match_i)
            classifier_y.append(1)
            matched_egg = corrected_eggs[match_i]
            px_per_mm = _wall_px_per_mm_at(box_walls, c["cy"], box_width_mm)
            if px_per_mm and matched_egg.get("size") in EGG_SIZE_CODES:
                size_X.append([c["width_px"] / px_per_mm, c["aspect_ratio"], c["extent"]])
                size_y.append(matched_egg["size"])

        # Eggs the user manually placed (no matching contour — a genuinely
        # missed detection) still teach the size model, using the placed
        # ellipse geometry directly. Excluded from the classifier: there's
        # no real contour shape signal (extent/solidity) to trust for a
        # blob that was never actually detected by the CV pass.
        for i, egg in enumerate(corrected_eggs):
            if i in used or not egg.get("added"):
                continue
            if egg.get("size") not in EGG_SIZE_CODES or not egg.get("width_px"):
                continue
            px_per_mm = _wall_px_per_mm_at(box_walls, egg.get("cy", 0), box_width_mm)
            if not px_per_mm:
                continue
            width_mm = egg["width_px"] / px_per_mm
            aspect_ratio = (egg["height_px"] / egg["width_px"]) if egg.get("height_px") else 1.0
            size_X.append([width_mm, aspect_ratio, 1.0])  # extent: idealized ellipse, no real contour to measure
            size_y.append(egg["size"])

    classifier = None
    pos = sum(1 for v in classifier_y if v == 1)
    neg = sum(1 for v in classifier_y if v == 0)
    if pos >= EGG_VISION_MIN_CLASSIFIER_POS and neg >= EGG_VISION_MIN_CLASSIFIER_NEG:
        classifier = LogisticRegression(max_iter=1000).fit(classifier_X, classifier_y)

    size_model = None
    if len(size_y) >= EGG_VISION_MIN_SIZE_SAMPLES and len(set(size_y)) >= 2:
        size_model = LogisticRegression(max_iter=1000).fit(size_X, size_y)

    # Box-ID head: nearest-centroid with cosine similarity over centered,
    # L2-normalized embeddings. Centering against the training-set mean
    # matters: ImageNet feature vectors share a large common component
    # (any two natural photos land at cosine ~0.5-0.8 raw), and removing
    # it spreads same-box vs cross-box similarities far apart, which is
    # what makes the fixed floor/margin thresholds meaningful. Chosen
    # over LogisticRegression on 1000-d with n=6-20 (p>>n needs
    # regularization tuning and yields the overconfident probabilities
    # this replaces) and over 1-NN (a single-outlier trap at 3
    # samples/box). Stored as a plain dict — inspectable, no sklearn
    # dependency at predict time.
    box_classifier = None
    box_classifier_labels = None
    eligible_boxes = {bid for bid, n in box_sample_counts.items() if n >= EGG_VISION_BOX_ID_MIN_SAMPLES_PER_BOX}
    if len(eligible_boxes) >= 2:
        filtered = [(x, y) for x, y in zip(box_X, box_y) if y in eligible_boxes]
        if filtered:
            embs = np.stack([x for x, _ in filtered])
            labels = [y for _, y in filtered]
            mean = embs.mean(axis=0)
            centered = embs - mean
            norms = np.linalg.norm(centered, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            centered /= norms
            centroids = {}
            for bid in sorted(eligible_boxes):
                rows = centered[[i for i, y in enumerate(labels) if y == bid]]
                centroid = rows.mean(axis=0)
                cnorm = float(np.linalg.norm(centroid))
                centroids[int(bid)] = (centroid / cnorm if cnorm > 0 else centroid).tolist()
            box_classifier = {
                "format": EGG_VISION_BOX_MODEL_FORMAT,
                "kind": "nearest_centroid_cosine",
                "embedder": {"model": "squeezenet1.1-7"},
                "dim": int(embs.shape[1]),
                "mean": mean.tolist(),
                "centroids": centroids,
            }
            box_classifier_labels = sorted(centroids)

    return {
        "classifier": classifier,
        "classifier_positive_count": pos,
        "classifier_negative_count": neg,
        "size_model": size_model,
        "size_model_sample_count": len(size_y),
        "box_classifier": box_classifier,
        "box_classifier_labels": box_classifier_labels,
        "box_classifier_sample_count": len(box_y),
    }


def _save_egg_vision_model(db, result, sample_count):
    db.execute("DELETE FROM egg_vision_models")
    db.execute(
        "INSERT INTO egg_vision_models (trained_at, trained_on_sample_count, classifier_blob, "
        "classifier_positive_count, classifier_negative_count, size_model_blob, size_model_sample_count, "
        "box_classifier_blob, box_classifier_labels, box_classifier_sample_count) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            datetime.now().isoformat(),
            sample_count,
            pickle.dumps(result["classifier"]) if result["classifier"] is not None else None,
            result["classifier_positive_count"],
            result["classifier_negative_count"],
            pickle.dumps(result["size_model"]) if result["size_model"] is not None else None,
            result["size_model_sample_count"],
            pickle.dumps(result["box_classifier"]) if result["box_classifier"] is not None else None,
            json.dumps(result["box_classifier_labels"]) if result["box_classifier_labels"] is not None else None,
            result["box_classifier_sample_count"],
        ),
    )
    db.commit()


@app.route("/api/vision/eggs/sample", methods=["POST"])
def api_vision_eggs_sample():
    """Persists one training sample: the review photo plus the
    pre-correction detection and the user's final corrected result
    (which nests box_id, box_width_mm, box_walls, and eggs — see
    app.js). Fired fire-and-forget from app.js after a successful log
    save (or, during the nesting-box wizard, after each guided photo —
    see `source` below). Gated on egg_vision_training_enabled UNLESS
    source is "wizard": the wizard is itself an explicit, deliberate
    opt-in and stores its seed photos regardless of whether ongoing
    day-to-day capture is on — see ARCHITECTURE.md §20 addendum."""
    training_cfg = get_egg_vision_training_config()
    data = request.get_json(force=True, silent=True) or {}
    if not training_cfg["enabled"] and data.get("source") != "wizard":
        return jsonify({"status": "disabled"})
    if not OPENCV_AVAILABLE:
        return jsonify({"status": "libs_unavailable", "error": OPENCV_ERROR})

    photo_bytes, err = _decode_photo_data_uri(data.get("photo") or "", max_bytes=MAX_EGG_VISION_PHOTO_BYTES)
    if err:
        return jsonify({"error": err}), 400

    corrected = data.get("corrected") or {}
    original = data.get("original") or {}
    box_id = corrected.get("box_id")
    if not isinstance(corrected.get("eggs"), list) or box_id is None:
        return jsonify({"error": "invalid corrected result"}), 400

    arr = np.frombuffer(photo_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return jsonify({"error": "couldn't decode image"}), 400
    h, w = img.shape[:2]

    db = get_db()
    db.execute(
        "INSERT INTO egg_vision_samples (created_at, photo, image_width, image_height, box_id, "
        "original_detection, corrected_result) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (datetime.now().isoformat(), photo_bytes, w, h, box_id, json.dumps(original), json.dumps(corrected)),
    )
    db.execute(
        "DELETE FROM egg_vision_samples WHERE id NOT IN "
        "(SELECT id FROM egg_vision_samples ORDER BY id DESC LIMIT ?)",
        (training_cfg["retention_count"],),
    )
    db.commit()
    count = db.execute("SELECT COUNT(*) FROM egg_vision_samples").fetchone()[0]
    return jsonify({"status": "stored", "sample_count": count}), 201


@app.route("/api/vision/train/status")
def api_vision_train_status():
    training_cfg = get_egg_vision_training_config()
    db = get_db()
    sample_count = db.execute("SELECT COUNT(*) FROM egg_vision_samples").fetchone()[0]
    samples_per_box = {
        row["box_id"]: row["n"]
        for row in db.execute(
            "SELECT box_id, COUNT(*) as n FROM egg_vision_samples WHERE box_id IS NOT NULL GROUP BY box_id"
        )
    }
    row = db.execute("SELECT * FROM egg_vision_models").fetchone()
    return jsonify(
        {
            "training_enabled": training_cfg["enabled"],
            "opencv_available": OPENCV_AVAILABLE,
            "sklearn_available": SKLEARN_AVAILABLE,
            "box_embedder_available": OPENCV_AVAILABLE and _box_embedder_status()["available"],
            "sample_count": sample_count,
            "retention_count": training_cfg["retention_count"],
            "min_samples_required": EGG_VISION_MIN_TRAINING_SAMPLES,
            "samples_per_box": samples_per_box,
            "model": None
            if row is None
            else {
                "trained_at": row["trained_at"],
                "trained_on_sample_count": row["trained_on_sample_count"],
                "has_classifier": row["classifier_blob"] is not None,
                "classifier_positive_count": row["classifier_positive_count"],
                "classifier_negative_count": row["classifier_negative_count"],
                "has_size_model": row["size_model_blob"] is not None,
                "size_model_sample_count": row["size_model_sample_count"],
                "has_box_classifier": row["box_classifier_blob"] is not None,
                "box_classifier_sample_count": row["box_classifier_sample_count"],
            },
        }
    )


@app.route("/api/vision/train", methods=["POST"])
def api_vision_train():
    if not OPENCV_AVAILABLE:
        return jsonify({"status": "libs_unavailable", "error": OPENCV_ERROR})
    db = get_db()
    sample_count = db.execute("SELECT COUNT(*) FROM egg_vision_samples").fetchone()[0]
    # Only "no samples at all" blocks training outright — each sub-model
    # gates itself on its own minimum inside _train_egg_vision_models.
    # A single total-count gate here (25, as pre-1.32) silently prevented
    # the box-ID head from EVER training on a fresh install: the wizard
    # needs just 3 photos per box, so 2 boxes x a few wizard photos never
    # reached 25 — the head that only needed 6 samples got none.
    if sample_count == 0:
        return jsonify(
            {"status": "insufficient_samples", "sample_count": sample_count, "min_required": EGG_VISION_MIN_TRAINING_SAMPLES}
        )
    samples = db.execute("SELECT box_id, photo, corrected_result FROM egg_vision_samples").fetchall()
    try:
        result = _train_egg_vision_models(samples)
    except Exception as e:  # noqa: BLE001 - training failure degrades, never 500s
        return jsonify({"status": "error", "error": str(e)})
    _save_egg_vision_model(db, result, sample_count)
    return jsonify(
        {
            "status": "trained",
            "trained_on_sample_count": sample_count,
            "classifier_trained": result["classifier"] is not None,
            "classifier_positive_count": result["classifier_positive_count"],
            "classifier_negative_count": result["classifier_negative_count"],
            "size_model_trained": result["size_model"] is not None,
            "size_model_sample_count": result["size_model_sample_count"],
            "box_classifier_trained": result["box_classifier"] is not None,
            "box_classifier_sample_count": result["box_classifier_sample_count"],
        }
    )


@app.route("/api/vision/train/clear", methods=["POST"])
def api_vision_train_clear():
    # Deliberately deletes only the stored photos, not an already-trained
    # model — a trained model is small and not privacy-sensitive the way
    # raw photos are, so there's no reason a "delete my photos" action
    # should also throw away a model derived from them. See
    # ARCHITECTURE.md §20 addendum.
    db = get_db()
    db.execute("DELETE FROM egg_vision_samples")
    db.commit()
    return jsonify({"status": "cleared"})


@app.route("/api/chickens")
def api_chickens():
    db = get_db()
    now = datetime.now()
    rows = [dict(row) for row in db.execute("SELECT * FROM chickens ORDER BY id ASC").fetchall()]
    for row in rows:
        row["daily_rate"] = round(_chicken_daily_rate(db, row, now), 2)
        row["has_photo"] = row["photo"] is not None
        del row["photo"]  # served separately by api_chicken_photo, keep this list light
    return jsonify(rows)


@app.route("/api/chickens/<int:chicken_id>/photo")
def api_chicken_photo(chicken_id):
    db = get_db()
    row = db.execute("SELECT photo FROM chickens WHERE id = ?", (chicken_id,)).fetchone()
    if row is None or row["photo"] is None:
        return "", 404
    # The URL is the same before and after a chicken's photo is replaced, so
    # without this the browser can keep serving the old cached bytes for it
    # (the previous photo "always present" after a re-upload).
    response = Response(row["photo"], mimetype="image/jpeg")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/chickens", methods=["POST"])
def api_add_chicken():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    breed = (data.get("breed") or "").strip() or None
    hatch_date, err = _parse_hatch_date(data.get("hatch_date"))
    if err:
        return jsonify({"error": err}), 400
    status = data.get("status") or "active"
    if status not in ("active", "lost"):
        return jsonify({"error": "invalid status"}), 400

    photo_bytes = None
    if data.get("photo"):
        photo_bytes, err = _decode_photo_data_uri(data["photo"])
        if err:
            return jsonify({"error": err}), 400

    db = get_db()
    cur = db.execute(
        "INSERT INTO chickens (name, breed, hatch_date, status, photo) VALUES (?, ?, ?, ?, ?)",
        (name, breed, hatch_date, status, photo_bytes),
    )
    db.commit()
    return jsonify({"id": cur.lastrowid}), 201


@app.route("/api/chickens/<int:chicken_id>", methods=["PUT"])
def api_update_chicken(chicken_id):
    db = get_db()
    row = db.execute("SELECT * FROM chickens WHERE id = ?", (chicken_id,)).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404

    data = request.get_json(force=True, silent=True) or {}

    name = (data.get("name", row["name"]) or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    breed = data.get("breed", row["breed"])
    hatch_date, err = _parse_hatch_date(data.get("hatch_date", row["hatch_date"]))
    if err:
        return jsonify({"error": err}), 400
    status = data.get("status", row["status"])
    if status not in ("active", "lost"):
        return jsonify({"error": "invalid status"}), 400

    if "photo" in data:
        # explicitly present: either a new photo to decode, or a falsy
        # value (null/"") meaning "clear the existing photo"
        photo_bytes = None
        if data["photo"]:
            photo_bytes, err = _decode_photo_data_uri(data["photo"])
            if err:
                return jsonify({"error": err}), 400
    else:
        photo_bytes = row["photo"]  # not mentioned: leave unchanged

    db.execute(
        "UPDATE chickens SET name = ?, breed = ?, hatch_date = ?, status = ?, photo = ? WHERE id = ?",
        (name, breed, hatch_date, status, photo_bytes, chicken_id),
    )
    db.commit()
    return jsonify({"id": chicken_id}), 200


@app.route("/api/chickens/<int:chicken_id>", methods=["DELETE"])
def api_delete_chicken(chicken_id):
    db = get_db()
    # manual cascade — SQLite doesn't enforce the FK without a pragma this
    # app never sets; a health event without its chicken is meaningless
    db.execute("DELETE FROM health_events WHERE chicken_id = ?", (chicken_id,))
    db.execute("DELETE FROM chickens WHERE id = ?", (chicken_id,))
    db.commit()
    return "", 204


@app.route("/api/chickens/<int:chicken_id>/health")
def api_chicken_health_events(chicken_id):
    db = get_db()
    if db.execute("SELECT id FROM chickens WHERE id = ?", (chicken_id,)).fetchone() is None:
        return jsonify({"error": "not found"}), 404
    rows = db.execute(
        "SELECT * FROM health_events WHERE chicken_id = ? ORDER BY event_date DESC, id DESC",
        (chicken_id,),
    ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.route("/api/chickens/<int:chicken_id>/health", methods=["POST"])
def api_add_health_event(chicken_id):
    db = get_db()
    if db.execute("SELECT id FROM chickens WHERE id = ?", (chicken_id,)).fetchone() is None:
        return jsonify({"error": "not found"}), 404

    data = request.get_json(force=True, silent=True) or {}

    event_type = data.get("event_type")
    if event_type not in HEALTH_EVENT_TYPES:
        return jsonify({"error": "invalid event_type"}), 400

    event_date, err = _parse_date_field(data.get("event_date"), "event_date")
    if err:
        return jsonify({"error": err}), 400
    if event_date is None:
        return jsonify({"error": "event_date is required"}), 400

    weight_grams = data.get("weight_grams")
    if weight_grams is not None:
        try:
            weight_grams = int(weight_grams)
        except (ValueError, TypeError):
            return jsonify({"error": "invalid weight_grams"}), 400
        if weight_grams <= 0:
            return jsonify({"error": "invalid weight_grams"}), 400
    if event_type == "weight" and weight_grams is None:
        return jsonify({"error": "weight_grams is required for weight events"}), 400

    notes = (data.get("notes") or "").strip() or None
    created_at = datetime.now().isoformat()

    cur = db.execute(
        "INSERT INTO health_events (chicken_id, event_type, event_date, weight_grams, notes, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (chicken_id, event_type, event_date, weight_grams, notes, created_at),
    )
    db.commit()
    row = db.execute("SELECT * FROM health_events WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/health-events/<int:event_id>", methods=["DELETE"])
def api_delete_health_event(event_id):
    db = get_db()
    db.execute("DELETE FROM health_events WHERE id = ?", (event_id,))
    db.commit()
    return "", 204


@app.route("/api/entries")
def api_entries():
    entry_type = request.args.get("type")
    limit = min(int(request.args.get("limit", 50)), 200)

    db = get_db()
    if entry_type:
        rows = db.execute(
            "SELECT * FROM logs WHERE type = ? ORDER BY ts DESC LIMIT ?",
            (entry_type, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM logs ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()

    return jsonify([dict(row) for row in rows])


def _normalize_bool_flag(value):
    """Normalizes a JSON boolean (or an already-stored 0/1/None) into what
    a nullable INTEGER "flag" column should hold — used for both
    container_empty (feeding) and given_away (used eggs)."""
    return None if value is None else (1 if value else 0)


@app.route("/api/log", methods=["POST"])
def api_log():
    data = request.get_json(force=True, silent=True) or {}
    entry_type = data.get("type")

    if entry_type not in ("egg", "cleaning", "feeding", "sale", "expense", "used"):
        return jsonify({"error": "invalid type"}), 400

    count = data.get("count")
    food_type = data.get("food_type")
    amount = data.get("amount")
    notes = data.get("notes")
    price = data.get("price")
    cost = data.get("cost")
    category = data.get("category")
    container_empty = _normalize_bool_flag(data.get("container_empty"))
    given_away = _normalize_bool_flag(data.get("given_away"))
    egg_sizes = data.get("egg_sizes")

    ts_input = data.get("ts")
    if ts_input:
        try:
            ts = datetime.fromisoformat(ts_input).isoformat()
        except ValueError:
            return jsonify({"error": "invalid ts"}), 400
    else:
        ts = datetime.now().isoformat()

    db = get_db()
    cur = db.execute(
        """
        INSERT INTO logs (type, ts, count, food_type, amount, notes, price, cost, category, container_empty, given_away, egg_sizes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (entry_type, ts, count, food_type, amount, notes, price, cost, category, container_empty, given_away, egg_sizes),
    )
    db.commit()
    threading.Thread(target=_push_ha_sensors_async, daemon=True).start()

    return jsonify({"id": cur.lastrowid, "type": entry_type, "ts": ts}), 201


@app.route("/api/entries/<int:entry_id>", methods=["PUT"])
def api_update_entry(entry_id):
    db = get_db()
    row = db.execute("SELECT * FROM logs WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404

    data = request.get_json(force=True, silent=True) or {}

    ts_input = data.get("ts")
    if ts_input:
        try:
            ts = datetime.fromisoformat(ts_input).isoformat()
        except ValueError:
            return jsonify({"error": "invalid ts"}), 400
    else:
        ts = row["ts"]

    count = data.get("count", row["count"])
    food_type = data.get("food_type", row["food_type"])
    amount = data.get("amount", row["amount"])
    notes = data.get("notes", row["notes"])
    price = data.get("price", row["price"])
    cost = data.get("cost", row["cost"])
    category = data.get("category", row["category"])
    container_empty = _normalize_bool_flag(data.get("container_empty", row["container_empty"]))
    given_away = _normalize_bool_flag(data.get("given_away", row["given_away"]))
    egg_sizes = data.get("egg_sizes", row["egg_sizes"])

    db.execute(
        """
        UPDATE logs
        SET ts = ?, count = ?, food_type = ?, amount = ?, notes = ?, price = ?, cost = ?,
            category = ?, container_empty = ?, given_away = ?, egg_sizes = ?
        WHERE id = ?
        """,
        (
            ts,
            count,
            food_type,
            amount,
            notes,
            price,
            cost,
            category,
            container_empty,
            given_away,
            egg_sizes,
            entry_id,
        ),
    )
    db.commit()
    threading.Thread(target=_push_ha_sensors_async, daemon=True).start()

    return jsonify({"id": entry_id, "ts": ts}), 200


@app.route("/api/entries/<int:entry_id>", methods=["DELETE"])
def api_delete_entry(entry_id):
    db = get_db()
    db.execute("DELETE FROM logs WHERE id = ?", (entry_id,))
    db.commit()
    threading.Thread(target=_push_ha_sensors_async, daemon=True).start()
    return "", 204


@app.route("/api/notifications")
def api_notifications():
    services, err = get_notify_services()
    return jsonify(
        {
            "reminder": get_reminder_config(),
            "services": services,
            "services_error": err,
        }
    )


@app.route("/api/notify-test", methods=["POST"])
def api_notify_test():
    ok, err = send_notification(
        "This is a test notification from Coop Tracker.", title="Coop Tracker test"
    )
    return jsonify({"status": "sent" if ok else "error", "error": err}), (200 if ok else 502)


@app.route("/api/debug")
def api_debug():
    now = datetime.now()
    db = get_db()

    db_ok = True
    db_error = None
    try:
        db.execute("SELECT COUNT(*) FROM logs").fetchone()
    except sqlite3.Error as e:
        db_ok = False
        db_error = str(e)

    ha_config, ha_error = _ha_api_request("GET", "/config")

    return jsonify(
        {
            "app_version": APP_VERSION,
            "container_time": now.isoformat(),
            "container_timezone": time.tzname,
            "supervisor_token_set": bool(SUPERVISOR_TOKEN),
            "ha_api_reachable": ha_error is None,
            "ha_api_error": ha_error,
            "ha_location_name": (ha_config or {}).get("location_name") if ha_config else None,
            "ha_time_zone": (ha_config or {}).get("time_zone") if ha_config else None,
            "options_path": OPTIONS_PATH,
            "options_path_exists": os.path.exists(OPTIONS_PATH),
            "db_path": DB_PATH,
            "db_ok": db_ok,
            "db_error": db_error,
            "reminder_last_checked_date": (
                _reminder_last_checked_date.isoformat()
                if _reminder_last_checked_date
                else _get_app_state(db, "reminder_last_checked_date")
            ),
            "python_version": sys.version.split()[0],
            "flask_version": importlib.metadata.version("flask"),
            "platform": platform.platform(),
            "statsmodels_available": STATSMODELS_AVAILABLE,
            "statsmodels_error": STATSMODELS_ERROR,
            "advanced_forecast_enabled": get_advanced_forecast_config()["enabled"],
            "opencv_available": OPENCV_AVAILABLE,
            "opencv_error": OPENCV_ERROR,
            "sklearn_available": SKLEARN_AVAILABLE,
            "sklearn_error": SKLEARN_ERROR,
            "box_embedder_available": OPENCV_AVAILABLE and _box_embedder_status()["available"],
            "box_embedder_error": _box_embedder_status()["error"] if OPENCV_AVAILABLE else "opencv unavailable",
            "box_embedder_model_path": EGG_VISION_BOX_EMBED_MODEL_PATH,
            "egg_vision_enabled": get_egg_vision_config()["enabled"],
        }
    )


@app.route("/api/backup")
def api_backup():
    db = get_db()
    db.commit()
    filename = f"coop-tracker-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    return send_file(DB_PATH, as_attachment=True, download_name=filename)


# Mirrors the logs table's columns exactly — the export is a faithful dump
# for spreadsheets/analysis, not a curated report, and deliberately one-way
# (only the .db backup can be restored, see api_restore).
EXPORT_COLUMNS = (
    "id", "type", "ts", "count", "food_type", "amount",
    "notes", "price", "cost", "category", "container_empty", "given_away", "egg_sizes",
)


@app.route("/api/export.csv")
def api_export_csv():
    db = get_db()
    rows = db.execute("SELECT * FROM logs ORDER BY ts ASC").fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(EXPORT_COLUMNS)
    for row in rows:
        writer.writerow(
            ["" if row[col] is None else row[col] for col in EXPORT_COLUMNS]
        )

    filename = f"coop-tracker-export-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
    response = Response(buf.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response


def _is_valid_backup(path):
    try:
        conn = sqlite3.connect(path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(logs)")}
        conn.close()
    except sqlite3.Error:
        return False
    return {"type", "ts", "count", "food_type", "amount", "notes"}.issubset(columns)


@app.route("/api/restore", methods=["POST"])
def api_restore():
    uploaded = request.files.get("file")
    if uploaded is None or uploaded.filename == "":
        return jsonify({"error": "no file provided"}), 400

    tmp_path = DB_PATH + ".upload"
    uploaded.save(tmp_path)

    if not _is_valid_backup(tmp_path):
        os.remove(tmp_path)
        return jsonify({"error": "not a valid Coop Tracker backup file"}), 400

    close_db()
    os.replace(tmp_path, DB_PATH)
    init_db()  # backfill any columns added since the backup was taken

    return jsonify({"status": "restored"}), 200


def _log_startup_debug_info():
    reminder = get_reminder_config()
    _log("--- startup debug info ---")
    _log(f"version: {APP_VERSION}")
    _log(f"container timezone: {time.tzname}")
    _log(f"SUPERVISOR_TOKEN set: {bool(SUPERVISOR_TOKEN)}")
    _log(f"currency: {_read_options().get('currency', DEFAULT_CURRENCY)}")
    _log(
        f"reminder: enabled={reminder['enabled']} "
        f"check_time={reminder['check_time']} threshold_days={reminder['threshold_days']} "
        f"notify_service={reminder['notify_service'] or '(not set)'}"
    )
    _log(f"db path: {DB_PATH}")
    _log("--- end startup debug info ---")


# Set by _handle_shutdown_signal; read after serve() returns to measure how
# long shutdown actually took (diagnostic for the exit-137 investigation).
_shutdown_signal_at = None


def _handle_shutdown_signal(signum, frame):
    # Diagnostic for the exit-137-on-restart investigation: Supervisor
    # restarts consistently take ~10s and end in SIGKILL (exit 137), but
    # every local reproduction of a bare SIGTERM exits instantly and
    # cleanly (exit 143) — this log line is the one thing that can tell us,
    # from the real environment, whether the signal is even being received
    # promptly or not.
    #
    # sys.exit(0) here raises SystemExit on the main thread, which interrupts
    # waitress's asyncore select/poll loop. Waitress's own BaseWSGIServer.run()
    # catches that SystemExit internally (it does NOT propagate it) and calls
    # task_dispatcher.shutdown(timeout=5) — which blocks for up to 5s waiting
    # on any worker thread still mid-request — before returning normally to
    # this __main__ block. So this log line alone only proves the signal
    # arrived; it says nothing about whether that shutdown() call, or
    # anything after it, is what eats the remaining time. The thread dump
    # below and the post-serve() log line close that gap.
    global _shutdown_signal_at
    _shutdown_signal_at = time.monotonic()
    alive = [f"{t.name}(daemon={t.daemon})" for t in threading.enumerate()]
    _log(f"received signal {signum}, shutting down; live threads: {alive}")
    sys.exit(0)


if __name__ == "__main__":
    from waitress import serve

    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    init_db()
    _log_startup_debug_info()
    threading.Thread(target=_background_loop, daemon=True).start()
    port = int(os.environ.get("COOP_PORT", "8099"))
    _log(f"serving on 0.0.0.0:{port} (waitress)")
    serve(app, host="0.0.0.0", port=port)
    # Diagnostic for the exit-137-on-restart investigation: if this line
    # never shows up in the logs before a SIGKILL, the hang is inside
    # waitress's shutdown (most likely task_dispatcher.shutdown()'s 5s wait
    # on a busy worker thread) or the signal never interrupted the accept
    # loop at all. If it DOES show up, the hang is somewhere after this
    # point — interpreter finalization, an atexit hook, or a non-daemon
    # thread we haven't identified yet.
    since_signal = (
        f"{time.monotonic() - _shutdown_signal_at:.1f}s after signal"
        if _shutdown_signal_at is not None
        else "no shutdown signal seen"
    )
    _log(f"serve() returned ({since_signal}), reaching end of __main__")
