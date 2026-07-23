import base64
import binascii
import csv
import io
import json
import math
import os
import platform
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, time as dtime, timedelta

import flask
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

APP_VERSION = "1.29.0"  # keep in sync with the "version" field in config.yaml

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
        app.logger.info(
            "SUPERVISOR_TOKEN not set; reminder and HA sensor push disabled (local/dev mode)"
        )
        return
    while True:
        try:
            conn = _db_connect_standalone()
            try:
                _reminder_tick(datetime.now(), conn)
                _push_ha_sensors(conn)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - keep the loop alive across any single failure
            app.logger.exception("background loop iteration failed")
        time.sleep(60)


@app.route("/")
def index():
    currency = get_currency()
    return render_template(
        "index.html",
        currency_symbol=currency["symbol"],
        currency_position=currency["position"],
        currency_decimals=currency["decimals"],
        app_version=APP_VERSION,
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


def _decode_photo_data_uri(data_uri):
    """Decodes a `data:image/...;base64,...` string (as produced by the
    chicken form's client-side resize) into raw bytes for storage."""
    try:
        _, encoded = data_uri.split(",", 1)
        photo_bytes = base64.b64decode(encoded)
    except (ValueError, binascii.Error):
        return None, "invalid photo data"
    if len(photo_bytes) > MAX_PHOTO_BYTES:
        return None, "photo is too large"
    return photo_bytes, None


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
        INSERT INTO logs (type, ts, count, food_type, amount, notes, price, cost, category, container_empty, given_away)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (entry_type, ts, count, food_type, amount, notes, price, cost, category, container_empty, given_away),
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

    db.execute(
        """
        UPDATE logs
        SET ts = ?, count = ?, food_type = ?, amount = ?, notes = ?, price = ?, cost = ?,
            category = ?, container_empty = ?, given_away = ?
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
            "flask_version": flask.__version__,
            "platform": platform.platform(),
            "statsmodels_available": STATSMODELS_AVAILABLE,
            "statsmodels_error": STATSMODELS_ERROR,
            "advanced_forecast_enabled": get_advanced_forecast_config()["enabled"],
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
    "notes", "price", "cost", "category", "container_empty", "given_away",
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
    print("[Coop Tracker] --- startup debug info ---")
    print(f"[Coop Tracker] version: {APP_VERSION}")
    print(f"[Coop Tracker] container time: {datetime.now().isoformat()} ({time.tzname})")
    print(f"[Coop Tracker] SUPERVISOR_TOKEN set: {bool(SUPERVISOR_TOKEN)}")
    print(f"[Coop Tracker] currency: {_read_options().get('currency', DEFAULT_CURRENCY)}")
    print(
        f"[Coop Tracker] reminder: enabled={reminder['enabled']} "
        f"check_time={reminder['check_time']} threshold_days={reminder['threshold_days']} "
        f"notify_service={reminder['notify_service'] or '(not set)'}"
    )
    print(f"[Coop Tracker] db path: {DB_PATH}")
    print("[Coop Tracker] --- end startup debug info ---")


if __name__ == "__main__":
    from waitress import serve

    init_db()
    _log_startup_debug_info()
    threading.Thread(target=_background_loop, daemon=True).start()
    port = int(os.environ.get("COOP_PORT", "8099"))
    print(f"[Coop Tracker] serving on 0.0.0.0:{port} (waitress)", flush=True)
    serve(app, host="0.0.0.0", port=port)
