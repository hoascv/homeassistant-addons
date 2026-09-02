import calendar
import hmac
import html
import io
import json
import os
import signal
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Flask, Response, g, jsonify, render_template, request, send_file

import energidataservice
import eloverblik
import saveeye
import easee

APP_VERSION = "1.18.0"  # keep in sync with the "version" field in config.yaml

DB_PATH = os.environ.get("ELECTRICITY_DB_PATH", "/data/electricity.db")
OPTIONS_PATH = os.environ.get("ELECTRICITY_OPTIONS_PATH", "/data/options.json")

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
CORE_API = "http://supervisor/core/api"

LOCAL_TZ = ZoneInfo("Europe/Copenhagen")

# How often the background loop wakes up. Prices are cheap and public, so
# every tick asks for them; consumption goes through Eloverblik's rate limits
# and a 24h access token, so it is only actually fetched every
# CONSUMPTION_SYNC_INTERVAL_SECONDS.
BACKGROUND_TICK_SECONDS = 300
CONSUMPTION_SYNC_INTERVAL_SECONDS = 3600
# Easee's session endpoint is throttled in their own client, so it gets its own
# slow schedule rather than riding the sampling tick. Sessions are history: a
# charge that finished an hour ago is no less true for being fetched late.
EASEE_SESSIONS_SYNC_INTERVAL_SECONDS = 3600
# How far back to re-fetch each time. Comfortably past the 30-day view so a
# session that was still running at the last sync gets its final energy and
# unplug time filled in, and any gap from a restart heals on its own.
EASEE_SESSIONS_SYNC_DAYS = 35

app = Flask(__name__)


def _log(msg):
    print(f"[Electricity Tracker] {datetime.now().isoformat()} {msg}", flush=True)


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_options():
    try:
        with open(OPTIONS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# --- Access control (per-user allowlist over the ingress user-ID header) ---
# Mirrors Gym Tracker / Coop Tracker: ingress passes the authenticated Home
# Assistant user's ID in this header, which is the only thing that can narrow
# access, since HA does not expose the user's admin/owner flag to add-ons.
INGRESS_USER_ID_HEADER = "X-Remote-User-ID"


def get_allowed_user_ids():
    raw = _read_options().get("restrict_to_user_ids", "") or ""
    return {uid.strip() for uid in raw.replace("\n", ",").replace(" ", ",").split(",") if uid.strip()}


def get_api_token():
    return (_read_options().get("api_token") or "").strip()


def _request_has_api_token():
    token = get_api_token()
    if not token:
        return False
    header = request.headers.get("Authorization", "")
    presented = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not presented:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), token.encode("utf-8"))


@app.before_request
def _enforce_access():
    """Two doors into this app, and each needs its own key — see Gym Tracker's
    app.py for the full reasoning; this is the same policy."""
    if _request_has_api_token():
        return None

    user_id = request.headers.get(INGRESS_USER_ID_HEADER)
    if not user_id:
        return Response(
            json.dumps({
                "error": "unauthorized",
                "detail": (
                    "This port requires a bearer token. Set api_token in the "
                    "add-on's Configuration tab and send it as "
                    "'Authorization: Bearer <token>'. Requests through Home "
                    "Assistant's ingress do not need one."
                ),
            }),
            status=401,
            mimetype="application/json",
            headers={"WWW-Authenticate": "Bearer"},
        )

    allowed = get_allowed_user_ids()
    if not allowed:
        return None
    if user_id in allowed:
        return None
    return Response(_access_denied_html(user_id), status=403, mimetype="text/html")


def _access_denied_html(user_id):
    shown = html.escape(user_id) if user_id else "(unknown — not opened through Home Assistant)"
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Electricity Tracker — access restricted</title>"
        "<style>body{font-family:system-ui,sans-serif;background:#111;color:#eee;"
        "display:flex;min-height:100vh;margin:0;align-items:center;justify-content:center;padding:1.5rem}"
        ".card{max-width:26rem;text-align:center;line-height:1.5}"
        "code{background:#222;padding:.15rem .4rem;border-radius:5px;word-break:break-all}</style>"
        "</head><body><div class='card'><h1>⚡ Access restricted</h1>"
        "<p>This Electricity Tracker is limited to specific Home Assistant users. "
        "Your account isn't on the list.</p>"
        f"<p>Your user ID is:<br><code>{shown}</code></p>"
        "<p>Ask whoever set up the add-on to add this ID to "
        "<strong>restrict_to_user_ids</strong> on the add-on's Configuration tab.</p>"
        "</div></body></html>"
    )


# --- Options ---


def get_price_options(options):
    """Coerced, clamped tariff/tax/VAT config, with the current 2026/2027
    elafgift as the electricity_tax default. Coercion matters here beyond
    config.yaml's schema because a malformed /data/options.json (hand-edited,
    or a dev run outside Supervisor) should degrade to sane defaults rather
    than crash every price calculation.
    """

    def f(key, default):
        try:
            return float(options.get(key, default))
        except (TypeError, ValueError):
            return default

    price_area = options.get("price_area")
    if price_area not in ("DK1", "DK2"):
        price_area = "DK2"
    return {
        "price_area": price_area,
        "grid_tariff_low": f("grid_tariff_low", 0.0),
        "grid_tariff_high": f("grid_tariff_high", 0.0),
        "grid_tariff_normal": f("grid_tariff_normal", 0.0),
        "grid_tariff_low_start": options.get("grid_tariff_low_start") or "00:00",
        "grid_tariff_low_end": options.get("grid_tariff_low_end") or "06:00",
        "grid_tariff_high_start": options.get("grid_tariff_high_start") or "17:00",
        "grid_tariff_high_end": options.get("grid_tariff_high_end") or "21:00",
        "grid_tariff_high_weekdays": options.get("grid_tariff_high_weekdays") or "",
        "grid_tariff_high_months": options.get("grid_tariff_high_months") or "",
        "transmission_tariff": f("transmission_tariff", 0.0),
        # What the supplier adds to the spot price. Energi Data Service gives
        # the raw market price; a bill shows spot plus the supplier's margin,
        # so without this every figure sits a few øre per kWh under reality.
        "supplier_markup": f("supplier_markup", 0.0),
        "electricity_tax": f("electricity_tax", 0.008),
        "vat_rate": f("vat_rate", 0.25),
        # A standing charge that does not depend on consumption at all — the
        # "Transport fast" or abonnement line on a Danish bill. Kept out of the
        # per-kWh price on purpose: dividing it by consumption would make a
        # quiet day look expensive per kWh, which is true but useless as a
        # price signal. It is added to cost totals instead.
        "fixed_charge_monthly": f("fixed_charge_monthly", 0.0),
    }


def get_eloverblik_config(options):
    try:
        backfill = int(options.get("eloverblik_backfill_days", 30))
    except (TypeError, ValueError):
        backfill = 30
    return {
        "refresh_token": (options.get("eloverblik_refresh_token") or "").strip(),
        "metering_point": (options.get("eloverblik_metering_point") or "").strip(),
        "backfill_days": min(730, max(1, backfill)),
    }


def get_saveeye_config(options):
    try:
        port = int(options.get("saveeye_mqtt_port", 1883))
    except (TypeError, ValueError):
        port = 1883
    return {
        "enabled": bool(options.get("saveeye_enabled", False)),
        "mqtt_host": (options.get("saveeye_mqtt_host") or "core-mosquitto").strip(),
        "mqtt_port": port,
        "mqtt_username": (options.get("saveeye_mqtt_username") or "").strip() or None,
        "mqtt_password": options.get("saveeye_mqtt_password") or None,
        "mqtt_topic": (options.get("saveeye_mqtt_topic") or saveeye.DEFAULT_TOPIC).strip(),
        "device_serial": (options.get("saveeye_device_serial") or "").strip() or None,
    }


def get_easee_config(options):
    return {
        "enabled": bool(options.get("easee_enabled", False)),
        "username": (options.get("easee_username") or "").strip(),
        "password": options.get("easee_password") or "",
        "charger_id": (options.get("easee_charger_id") or "").strip() or None,
    }


# --- Database ---


def get_db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db():
    """Close this request's connection, so the file can be replaced under it.

    Only the restore path needs this: SQLite will happily let a file be swapped
    while a handle is open, and the handle then reads a database that no longer
    exists.
    """
    db = g.pop("db", None)
    if db is not None:
        db.close()


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


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prices (
            time_dk TEXT NOT NULL,     -- Danish local wall-clock, quarter-hour start, naive ISO
            price_area TEXT NOT NULL,
            spot_price_dkk_kwh REAL NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (time_dk, price_area)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS consumption (
            time_utc TEXT NOT NULL,    -- UTC, hour start, ISO
            metering_point TEXT NOT NULL,
            kwh REAL NOT NULL,
            quality TEXT,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (time_utc, metering_point)
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS saveeye_samples (
            ts_utc TEXT NOT NULL,          -- when this add-on received the telemetry, UTC ISO
            device_serial TEXT NOT NULL,
            instant_power_w REAL,
            cumulative_wh REAL,            -- the meter's own ever-increasing counter
            PRIMARY KEY (ts_utc, device_serial)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS easee_samples (
            ts_utc TEXT NOT NULL,
            charger_id TEXT NOT NULL,
            status TEXT,                   -- Easee's own chargerOpMode name, stored raw
            session_energy_kwh REAL,       -- resets at the start of each charging session
            total_power_w REAL,
            reason_for_no_current INTEGER, -- Easee's reasonForNoCurrent code, when it gave one
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (ts_utc, charger_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS easee_cloud_sessions (
            charger_id TEXT NOT NULL,
            -- When the cable went in and came out, which is NOT when charging
            -- started and stopped: a car left on a schedule reports a
            -- twelve-hour session that charged for two of them.
            connected_at TEXT NOT NULL,
            disconnected_at TEXT,          -- absent while the cable is still in
            energy_kwh REAL NOT NULL,      -- Easee's own total, the authority
            fetched_at TEXT NOT NULL,
            -- Keyed on the start so a re-fetch updates a session in place
            -- rather than accumulating a copy per sync — an ongoing session
            -- gets its end filled in by a later fetch.
            PRIMARY KEY (charger_id, connected_at)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ev_trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            -- Local calendar days, not instants. A trip is "the weekend we
            -- drove to Aarhus", and pinning it to a timestamp would invite a
            -- precision the keeper does not have and does not need.
            started_on TEXT NOT NULL,
            ended_on TEXT NOT NULL,
            label TEXT NOT NULL,
            -- Optional. Without it the trip still explains a spike in the
            -- chart, which is most of the point; with it the charging can be
            -- put against the distance.
            distance_km REAL,
            notes TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ev_trips ON ev_trips(started_on, ended_on)")
    _migrate_columns(conn)
    conn.commit()
    conn.close()


def _migrate_columns(conn):
    """Bring an older database up to the current schema. CREATE TABLE handles
    fresh installs; these ALTERs handle in-place upgrades."""
    easee_cols = {row[1] for row in conn.execute("PRAGMA table_info(easee_samples)")}
    if "reason_for_no_current" not in easee_cols:
        # Added in 1.6.2. Nullable with no backfill: samples taken before this
        # release genuinely have no reason recorded, and inventing 0 ("nothing
        # wrong") for them would assert something never observed.
        conn.execute("ALTER TABLE easee_samples ADD COLUMN reason_for_no_current INTEGER")


# --- Price calculation ---


def _parse_int_set(raw):
    out = set()
    for part in (raw or "").replace(" ", "").split(","):
        if part:
            try:
                out.add(int(part))
            except ValueError:
                pass
    return out


def _time_in_window(hm, start, end):
    """Whether "HH:MM" `hm` falls in [start, end), wrapping past midnight if
    start > end (e.g. a 22:00-06:00 low-tariff night window)."""
    if not start or not end:
        return False
    if start <= end:
        return start <= hm < end
    return hm >= start or hm < end


def _grid_tariff_band(dt_local, opts):
    """Which of the up to three configured grid-tariff bands a naive local
    datetime falls into. High only applies within its time window *and* on a
    configured weekday *and* in a configured month — all three are optional
    filters (an empty list means "every day"/"every month"), which is how
    most Danish grid companies define their winter weekday peak."""
    hm = dt_local.strftime("%H:%M")
    if _time_in_window(hm, opts["grid_tariff_high_start"], opts["grid_tariff_high_end"]):
        weekdays = _parse_int_set(opts["grid_tariff_high_weekdays"])
        months = _parse_int_set(opts["grid_tariff_high_months"])
        if (not weekdays or dt_local.isoweekday() in weekdays) and (not months or dt_local.month in months):
            return "high"
    if _time_in_window(hm, opts["grid_tariff_low_start"], opts["grid_tariff_low_end"]):
        return "low"
    return "normal"


def compute_total_price(spot_dkk_kwh, dt_local, opts):
    """Full end-user price: spot + grid tariff (time-of-day banded) +
    Energinet's transmission tariff + elafgift, all subject to Danish VAT."""
    band = _grid_tariff_band(dt_local, opts)
    grid = opts[f"grid_tariff_{band}"]
    markup = opts.get("supplier_markup", 0.0)
    subtotal = spot_dkk_kwh + markup + grid + opts["transmission_tariff"] + opts["electricity_tax"]
    total = subtotal * (1 + opts["vat_rate"])
    components = {
        "spot_dkk_kwh": round(spot_dkk_kwh, 4),
        # Reported separately from spot rather than folded into it: the market
        # price is a fact and the margin is a contract, and seeing them apart is
        # what lets a bill be checked against this.
        "supplier_markup_dkk_kwh": markup,
        "grid_tariff_dkk_kwh": grid,
        "grid_tariff_band": band,
        "transmission_tariff_dkk_kwh": opts["transmission_tariff"],
        "electricity_tax_dkk_kwh": opts["electricity_tax"],
        "vat_rate": opts["vat_rate"],
    }
    return total, components


def _price_rows(conn, start_local, end_local, price_area):
    return conn.execute(
        "SELECT time_dk, spot_price_dkk_kwh FROM prices "
        "WHERE price_area = ? AND time_dk >= ? AND time_dk < ? ORDER BY time_dk",
        (price_area, start_local.replace(tzinfo=None).isoformat(), end_local.replace(tzinfo=None).isoformat()),
    ).fetchall()


def quarter_prices_with_total(conn, start_local, end_local, price_area, opts):
    """Quarter-hourly prices in [start_local, end_local), each with the full
    end-user total — the native resolution of the day-ahead market since
    2025-10-01."""
    out = []
    for row in _price_rows(conn, start_local, end_local, price_area):
        dt = datetime.fromisoformat(row["time_dk"])
        total, components = compute_total_price(row["spot_price_dkk_kwh"], dt, opts)
        out.append({"time_dk": row["time_dk"], "total_dkk_kwh": round(total, 4), **components})
    return out


def _hourly_totals(quarter_rows):
    """Quarter-hour totals averaged into hour buckets, keyed by the naive
    local hour-start ISO string — the resolution consumption is metered at."""
    buckets = {}
    for row in quarter_rows:
        hour_key = datetime.fromisoformat(row["time_dk"]).replace(minute=0, second=0, microsecond=0).isoformat()
        buckets.setdefault(hour_key, []).append(row["total_dkk_kwh"])
    return {k: sum(v) / len(v) for k, v in buckets.items()}


def _current_price_row(quarter_rows, now_local):
    now_key = now_local.replace(tzinfo=None, second=0, microsecond=0).isoformat()
    candidates = [r for r in quarter_rows if r["time_dk"] <= now_key]
    return candidates[-1] if candidates else None


def price_config_warning(opts):
    """What is missing from the tariff configuration, or None if nothing is.

    The add-on's whole claim is a *full end-user price*: spot plus the grid
    company's tariff, plus Energinet's, plus tax, plus VAT. Every one of those
    is an option, and two of them default to 0.0 because nobody can guess them
    — they depend on which grid company you are behind.

    Left at zero the arithmetic is still correct, which is exactly the problem:
    the dashboard shows a confident, precise, and badly wrong number. A charge
    priced at 0.12 kr/kWh against a real 1.20 looks like a bug in the add-on
    rather than a gap in its configuration, and there is nothing on screen to
    suggest otherwise. This is that something.
    """
    # Either option alone is a complete answer. Many Danish suppliers bill the
    # grid company's tariff and Energinet's as a single combined "transport"
    # line, in which case the whole figure belongs in transmission_tariff and
    # the grid bands stay at zero — demanding both would nag forever at anyone
    # who configured it correctly.
    configured = any(opts.get(f"grid_tariff_{band}") for band in ("low", "normal", "high")) or bool(
        opts.get("transmission_tariff")
    )
    if configured:
        return None
    return {
        "missing": ["transmission_tariff", "or the grid_tariff_* bands"],
        "detail": (
            "Prices here are spot + VAT only — no pass-through tariff is set, so your grid "
            "company's and Energinet's charges are missing. Every cost in this add-on is "
            "understated until one is set in the Configuration tab. If your bill shows them as "
            "a single combined line, put the whole figure in transmission_tariff."
        ),
    }


# --- Consumption + cost ---


def _consumption_rows(conn, start_utc, end_utc, metering_point):
    return conn.execute(
        "SELECT time_utc, kwh, quality FROM consumption "
        "WHERE metering_point = ? AND time_utc >= ? AND time_utc < ? ORDER BY time_utc",
        (metering_point, start_utc.isoformat(), end_utc.isoformat()),
    ).fetchall()


def consumption_with_cost(conn, start_utc, end_utc, metering_point, price_area, opts):
    rows = _consumption_rows(conn, start_utc, end_utc, metering_point)
    if not rows:
        return []
    local_hours = [
        datetime.fromisoformat(r["time_utc"]).astimezone(LOCAL_TZ).replace(minute=0, second=0, microsecond=0)
        for r in rows
    ]
    price_start = min(local_hours)
    price_end = max(local_hours) + timedelta(hours=1)
    hourly_totals = _hourly_totals(quarter_prices_with_total(conn, price_start, price_end, price_area, opts))

    out = []
    for row, local_hour in zip(rows, local_hours):
        hour_key = local_hour.replace(tzinfo=None).isoformat()
        price = hourly_totals.get(hour_key)
        cost = round(row["kwh"] * price, 4) if price is not None else None
        out.append(
            {
                "time_utc": row["time_utc"],
                "time_dk": hour_key,
                "kwh": row["kwh"],
                "quality": row["quality"],
                "price_dkk_kwh": round(price, 4) if price is not None else None,
                "cost_dkk": cost,
            }
        )
    return out


def combined_consumption_with_cost(
    conn, start_local, end_local, metering_point, price_area, opts, saveeye_device_serial=None
):
    """Eloverblik's measured hours, with any hour in range Eloverblik hasn't
    reported yet filled in from Saveeye's live cumulative counter, if
    configured — a same-day estimate for the 1-3 days Eloverblik typically
    lags, clearly marked `"source": "saveeye_estimate"` rather than presented
    as equivalent to a measured reading. Eloverblik always wins once it has
    the hour; nothing here ever overrides a measured row.

    `kwh`/`source` stay that single blended series (what the totals and the
    tiles are built from). Alongside them every row also carries the two
    sources unblended — `measured_kwh` (Eloverblik, None when it hasn't
    reported the hour) and `saveeye_kwh` (None when Saveeye has no estimate
    for it) — so the chart can draw them as two comparable series, including
    for the hours where both exist and only Eloverblik's shows up in `kwh`.
    """
    rows = consumption_with_cost(
        conn, start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc), metering_point, price_area, opts
    )
    for row in rows:
        row["source"] = "eloverblik"
        row["measured_kwh"] = row["kwh"]
        row["saveeye_kwh"] = None

    if not saveeye_device_serial:
        return rows

    by_hour = {row["time_dk"]: row for row in rows}
    covered = set(by_hour)
    estimates = saveeye_hourly_kwh(conn, start_local, end_local, saveeye_device_serial)
    hourly_totals = _hourly_totals(quarter_prices_with_total(conn, start_local, end_local, price_area, opts))

    for hour_key, kwh in estimates.items():
        measured_row = by_hour.get(hour_key)
        if measured_row is not None:
            # Eloverblik owns this hour, so it stays the blended value; Saveeye's
            # own number rides along as the second series instead of being dropped.
            measured_row["saveeye_kwh"] = kwh
            continue
        price = hourly_totals.get(hour_key)
        cost = round(kwh * price, 4) if price is not None else None
        time_utc = datetime.fromisoformat(hour_key).replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc).isoformat()
        rows.append(
            {
                "time_utc": time_utc,
                "time_dk": hour_key,
                "kwh": kwh,
                "quality": None,
                "price_dkk_kwh": round(price, 4) if price is not None else None,
                "cost_dkk": cost,
                "source": "saveeye_estimate",
                "measured_kwh": None,
                "saveeye_kwh": kwh,
            }
        )

    now_local = datetime.now(LOCAL_TZ)
    current_hour_start = now_local.replace(minute=0, second=0, microsecond=0)
    current_hour_key = current_hour_start.replace(tzinfo=None).isoformat()
    if start_local <= now_local < end_local and current_hour_key not in covered and current_hour_key not in estimates:
        partial = saveeye_partial_hour_kwh(conn, current_hour_start, now_local, saveeye_device_serial)
        if partial:
            price = hourly_totals.get(current_hour_key)
            cost = round(partial["kwh"] * price, 4) if price is not None else None
            rows.append(
                {
                    "time_utc": current_hour_start.astimezone(timezone.utc).isoformat(),
                    "time_dk": current_hour_key,
                    "kwh": partial["kwh"],
                    "quality": None,
                    "price_dkk_kwh": round(price, 4) if price is not None else None,
                    "cost_dkk": cost,
                    "source": "saveeye_partial",
                    "measured_kwh": None,
                    "saveeye_kwh": partial["kwh"],
                }
            )

    rows.sort(key=lambda r: r["time_dk"])
    return rows


def fixed_charge_for_window(opts, start_local, end_local):
    """The standing charge falling inside [start_local, end_local), incl. VAT.

    Accrued per day rather than dropped whole on the 1st: a month's charge
    divided by the days in *that* month, summed over the days the window
    touches. That way "today" carries one day of it, a week carries seven, and
    a part-month carries the part that has actually elapsed — which is what
    makes a running month-to-date total comparable with a bill.

    Charged on calendar days, so February's daily rate is higher than January's.
    That mirrors how the charge is actually levied: per month, not per day.
    """
    monthly = opts.get("fixed_charge_monthly", 0.0)
    if not monthly or end_local <= start_local:
        return 0.0
    total = 0.0
    day = start_local.date()
    last = (end_local - timedelta(microseconds=1)).date()
    while day <= last:
        days_in_month = calendar.monthrange(day.year, day.month)[1]
        total += monthly / days_in_month
        day += timedelta(days=1)
    return round(total * (1 + opts["vat_rate"]), 4)


def _consumption_totals(conn, start_local, end_local, metering_point, price_area, opts, saveeye_device_serial=None):
    rows = combined_consumption_with_cost(
        conn, start_local, end_local, metering_point, price_area, opts, saveeye_device_serial
    )
    kwh = round(sum(r["kwh"] for r in rows), 3)
    costed = [r["cost_dkk"] for r in rows if r["cost_dkk"] is not None]
    cost = round(sum(costed), 2) if costed else None
    return kwh, cost


def consumption_summary(conn, now_local, metering_point, price_area, opts, saveeye_device_serial=None):
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    week_start = today_start - timedelta(days=7)
    month_start = today_start.replace(day=1)
    tomorrow_start = today_start + timedelta(days=1)

    today_kwh, today_cost = _consumption_totals(
        conn, today_start, tomorrow_start, metering_point, price_area, opts, saveeye_device_serial
    )
    yesterday_kwh, yesterday_cost = _consumption_totals(
        conn, yesterday_start, today_start, metering_point, price_area, opts, saveeye_device_serial
    )
    week_kwh, week_cost = _consumption_totals(
        conn, week_start, tomorrow_start, metering_point, price_area, opts, saveeye_device_serial
    )
    month_kwh, month_cost = _consumption_totals(
        conn, month_start, tomorrow_start, metering_point, price_area, opts, saveeye_device_serial
    )

    # The standing charge for each window, reported alongside the energy cost
    # rather than folded into it. Both numbers are real and they answer
    # different questions: what the electricity cost, and what the bill will
    # say. Hiding the split would make a zero-consumption day look like it cost
    # money for no reason.
    windows = (
        ("today", today_start, tomorrow_start, today_cost),
        ("yesterday", yesterday_start, today_start, yesterday_cost),
        ("week", week_start, tomorrow_start, week_cost),
        ("month", month_start, tomorrow_start, month_cost),
    )
    fixed = {name: fixed_charge_for_window(opts, a, b) for name, a, b, _ in windows}
    billed = {
        name: None if cost is None else round(cost + fixed[name], 2)
        for name, _, _, cost in windows
    }

    return {
        "today_kwh": today_kwh,
        "today_cost_dkk": today_cost,
        "yesterday_kwh": yesterday_kwh,
        "yesterday_cost_dkk": yesterday_cost,
        "week_kwh": week_kwh,
        "week_cost_dkk": week_cost,
        "month_kwh": month_kwh,
        "month_cost_dkk": month_cost,
        # Standing charge accrued in each window, and energy + standing charge
        # together — what the bill actually comes to.
        "today_fixed_dkk": fixed["today"],
        "yesterday_fixed_dkk": fixed["yesterday"],
        "week_fixed_dkk": fixed["week"],
        "month_fixed_dkk": fixed["month"],
        "today_billed_dkk": billed["today"],
        "yesterday_billed_dkk": billed["yesterday"],
        "week_billed_dkk": billed["week"],
        "month_billed_dkk": billed["month"],
        "has_fixed_charge": bool(opts.get("fixed_charge_monthly", 0.0)),
    }


# --- Sync: Energi Data Service (prices) ---


def sync_prices(conn, options, now_local=None):
    now_local = now_local or datetime.now(LOCAL_TZ)
    opts = get_price_options(options)
    # From yesterday (covers a gap left by a restart) through two days ahead
    # (today, tomorrow once published, and headroom) — the API simply returns
    # whatever of that window actually exists.
    start = now_local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    end = start + timedelta(days=4)
    try:
        rows = energidataservice.fetch_day_ahead_prices(
            opts["price_area"], start.strftime("%Y-%m-%dT%H:%M"), end.strftime("%Y-%m-%dT%H:%M")
        )
    except energidataservice.EnergiDataServiceError as exc:
        _log(f"price sync failed: {exc}")
        return
    ts = _now_iso()
    conn.executemany(
        "INSERT INTO prices (time_dk, price_area, spot_price_dkk_kwh, fetched_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(time_dk, price_area) DO UPDATE SET "
        "spot_price_dkk_kwh = excluded.spot_price_dkk_kwh, fetched_at = excluded.fetched_at",
        [(r["time_dk"], opts["price_area"], r["price_dkk_kwh"], ts) for r in rows],
    )
    _set_app_state(conn, "last_price_sync", ts)
    conn.commit()
    _log(f"price sync: {len(rows)} rows for {opts['price_area']}")


# --- Sync: Eloverblik (consumption) ---

_eloverblik_token_cache = {"refresh_token": None, "access_token": None, "expires_at": 0.0}


def _get_eloverblik_access_token(refresh_token, timeout=15):
    cache = _eloverblik_token_cache
    now = time.time()
    if cache["refresh_token"] == refresh_token and cache["access_token"] and now < cache["expires_at"]:
        return cache["access_token"]
    token = eloverblik.get_access_token(refresh_token, timeout=timeout)
    # Documented validity is 24h; refreshed a little early to stay safe.
    cache.update(refresh_token=refresh_token, access_token=token, expires_at=now + 23 * 3600)
    return token


def sync_consumption(conn, options, today_local=None):
    cfg = get_eloverblik_config(options)
    if not cfg["refresh_token"] or not cfg["metering_point"]:
        return
    today_local = today_local or date.today()
    date_from = today_local - timedelta(days=cfg["backfill_days"])
    date_to = today_local + timedelta(days=1)
    try:
        token = _get_eloverblik_access_token(cfg["refresh_token"])
        rows = eloverblik.get_hourly_consumption(token, cfg["metering_point"], date_from, date_to)
    except eloverblik.EloverblikError as exc:
        _log(f"consumption sync failed: {exc}")
        return
    ts = _now_iso()
    conn.executemany(
        "INSERT INTO consumption (time_utc, metering_point, kwh, quality, fetched_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(time_utc, metering_point) DO UPDATE SET "
        "kwh = excluded.kwh, quality = excluded.quality, fetched_at = excluded.fetched_at",
        [(r["time_utc"], cfg["metering_point"], r["kwh"], r.get("quality"), ts) for r in rows],
    )
    _set_app_state(conn, "last_consumption_sync", ts)
    conn.commit()
    _log(f"consumption sync: {len(rows)} rows")


# --- Saveeye (live MQTT telemetry, optional) ---

_saveeye_lock = threading.Lock()
_saveeye_latest = {"payload": None, "received_at": None}
_saveeye_status = {"connected": False, "detail": None}
_saveeye_client = None


def _handle_saveeye_telemetry(parsed):
    """Runs on the MQTT client's own thread — just records the latest
    reading. Persisting to SQLite happens on the background loop's thread
    instead, since sqlite3 connections are not safe to share across threads."""
    with _saveeye_lock:
        _saveeye_latest["payload"] = parsed
        _saveeye_latest["received_at"] = _now_iso()


def _handle_saveeye_status(connected, detail):
    with _saveeye_lock:
        _saveeye_status["connected"] = connected
        _saveeye_status["detail"] = detail
    _log(f"Saveeye MQTT: {detail}")


def get_saveeye_latest():
    with _saveeye_lock:
        payload = dict(_saveeye_latest["payload"]) if _saveeye_latest["payload"] else None
        return payload, _saveeye_latest["received_at"]


def get_saveeye_status():
    with _saveeye_lock:
        return dict(_saveeye_status)


def resolve_saveeye_device_serial(conn, configured_serial):
    """The device serial to query saveeye_samples with.

    `saveeye_device_serial` is deliberately left empty in the common
    single-Base-reader case, which is fine for *receiving* telemetry (the
    MQTT client accepts any device on the topic) but every read path below
    needs an actual string to filter the table by. Falls back to whichever
    device most recently sent telemetry — in memory if the add-on has seen a
    message since it started, otherwise the most recent row already in
    storage — so an empty config doesn't silently mean "match nothing",
    which is what happened before this existed.
    """
    if configured_serial:
        return configured_serial
    payload, _ = get_saveeye_latest()
    if payload and payload.get("device_serial"):
        return payload["device_serial"]
    row = conn.execute("SELECT device_serial FROM saveeye_samples ORDER BY ts_utc DESC LIMIT 1").fetchone()
    return row["device_serial"] if row else None


def start_saveeye_client(options):
    """Starts the background MQTT subscriber if saveeye_enabled. A no-op
    (returns None) otherwise — callers should treat that as "not configured",
    not as an error."""
    global _saveeye_client
    cfg = get_saveeye_config(options)
    if not cfg["enabled"]:
        return None
    _saveeye_client = saveeye.SaveeyeClient(
        host=cfg["mqtt_host"],
        port=cfg["mqtt_port"],
        topic=cfg["mqtt_topic"],
        on_telemetry=_handle_saveeye_telemetry,
        on_status=_handle_saveeye_status,
        username=cfg["mqtt_username"],
        password=cfg["mqtt_password"],
        device_serial=cfg["device_serial"],
    )
    _saveeye_client.start()
    _log(f"Saveeye MQTT client starting: {cfg['mqtt_host']}:{cfg['mqtt_port']} topic={cfg['mqtt_topic']}")
    return _saveeye_client


def _persist_saveeye_sample(conn):
    """Once per background-loop tick, if a newer telemetry reading has
    arrived than the last one written, appends it to saveeye_samples.

    5-minute granularity (the tick interval) is deliberately coarse: this
    data only needs to bracket hour boundaries closely enough to interpolate
    a hourly consumption estimate from, not to plot a smooth live power
    curve — that part reads straight from the in-memory latest reading.
    """
    payload, received_at = get_saveeye_latest()
    if not payload or payload.get("cumulative_wh") is None:
        return
    device_serial = payload["device_serial"]
    last = conn.execute(
        "SELECT ts_utc FROM saveeye_samples WHERE device_serial = ? ORDER BY ts_utc DESC LIMIT 1",
        (device_serial,),
    ).fetchone()
    ts = received_at or _now_iso()
    if last and last["ts_utc"] >= ts:
        return
    conn.execute(
        "INSERT OR REPLACE INTO saveeye_samples (ts_utc, device_serial, instant_power_w, cumulative_wh) "
        "VALUES (?, ?, ?, ?)",
        (ts, device_serial, payload.get("instant_power_w"), payload["cumulative_wh"]),
    )
    conn.commit()


def _interp_series(samples, target_ts):
    """Linear interpolation of `samples` (sorted ascending (epoch_seconds,
    value) pairs) at `target_ts`. Returns None rather than extrapolating
    when `target_ts` falls outside the sampled range — an hour not actually
    bracketed by real readings gets no estimate, not a guessed one."""
    if len(samples) < 2 or target_ts < samples[0][0] or target_ts > samples[-1][0]:
        return None
    for (t0, v0), (t1, v1) in zip(samples, samples[1:]):
        if t0 <= target_ts <= t1:
            if t1 == t0:
                return v0
            frac = (target_ts - t0) / (t1 - t0)
            return v0 + frac * (v1 - v0)
    return None


# The most any domestic supply could deliver between two polls, used to bound
# how much of a post-reset counter value can be believed. 25 kW is generous for
# a house; anything above what that allows is a counter doing something other
# than resetting to zero, and is not counted rather than guessed at.
MAX_PLAUSIBLE_KW = 25.0


def _is_counter_reset(previous_value, value):
    """Whether a backward step is the counter restarting, or just jitter.

    A reset restarts near zero, so it falls much further than where it lands:
    71,123 -> 9 dropped 71,114 to reach 9. A meter correction of 1,000 -> 900
    fell 100 to reach 900, and reading that as a reset would invent 900 Wh of
    consumption that never happened. Jitter is left to the existing guards,
    which decline to report rather than report something made up.
    """
    return value < previous_value - value


def _counter_segments(samples):
    """Split a counter series at each reset.

    Saveeye's cumulative counter is not a lifetime total: it resets to zero
    every few days. Observed in a real database — 71,123 Wh to 9 Wh, three
    times in eleven days.

    Differencing straight across that reset gives a large negative number, so
    the old code discarded the hour containing it. Safe, but it silently lost
    an hour of consumption per reset — about 120 hours a year at this device's
    rate. Splitting into runs that only ever increase means each side of a
    reset can be measured on its own and added up.
    """
    segments = []
    current = []
    for sample in samples:
        if current and _is_counter_reset(current[-1][1], sample[1]):
            segments.append(current)
            current = []
        current.append(sample)
    if current:
        segments.append(current)
    return segments


def _segment_energy(segment, window_start, window_end, is_first, previous_end_ts):
    """Watt-hours a single monotonic run contributes to [window_start, window_end).

    A run that *begins* inside the window follows a reset, so its first reading
    is energy consumed since that reset — counted, but only up to what the gap
    could physically have delivered, so a counter that wrapped to a large value
    rather than resetting to zero is not read as a huge burst of consumption.
    """
    total = 0.0
    counted = False

    lo = max(window_start, segment[0][0])
    hi = min(window_end, segment[-1][0])
    if hi > lo and len(segment) >= 2:
        v_lo = _interp_series(segment, lo)
        v_hi = _interp_series(segment, hi)
        if v_lo is not None and v_hi is not None and v_hi >= v_lo:
            total += v_hi - v_lo
            counted = True

    if not is_first and window_start <= segment[0][0] < window_end:
        gap_hours = max(0.0, (segment[0][0] - previous_end_ts) / 3600.0)
        total += min(max(0.0, segment[0][1]), MAX_PLAUSIBLE_KW * 1000.0 * gap_hours)
        counted = True

    return total, counted


def saveeye_hourly_kwh(conn, start_local, end_local, device_serial):
    """Same-day-ish hourly consumption for [start_local, end_local), derived
    from Saveeye's cumulative energy counter by interpolating its value at
    each hour boundary and taking the difference. Only hours actually
    bracketed by real samples on both sides get an estimate; a gap (add-on
    just started, broker down for a while) is simply missing from the
    result, not filled in with a guess. Returns {naive local hour ISO: kwh}.
    """
    if not device_serial:
        return {}
    query_start = (start_local - timedelta(hours=1)).astimezone(timezone.utc).isoformat()
    query_end = (end_local + timedelta(hours=1)).astimezone(timezone.utc).isoformat()
    rows = conn.execute(
        "SELECT ts_utc, cumulative_wh FROM saveeye_samples "
        "WHERE device_serial = ? AND ts_utc >= ? AND ts_utc <= ? ORDER BY ts_utc",
        (device_serial, query_start, query_end),
    ).fetchall()
    samples = [
        (datetime.fromisoformat(r["ts_utc"]).timestamp(), r["cumulative_wh"])
        for r in rows
        if r["cumulative_wh"] is not None
    ]
    if len(samples) < 2:
        return {}

    segments = _counter_segments(samples)
    out = {}
    hour = start_local.replace(minute=0, second=0, microsecond=0)
    while hour < end_local:
        boundary_start = hour.astimezone(timezone.utc).timestamp()
        boundary_end = (hour + timedelta(hours=1)).astimezone(timezone.utc).timestamp()
        # Unchanged from before: an hour not bracketed by real readings on both
        # sides gets no estimate rather than an extrapolated one.
        if boundary_start < samples[0][0] or boundary_end > samples[-1][0]:
            hour += timedelta(hours=1)
            continue

        total = 0.0
        counted = False
        for index, segment in enumerate(segments):
            previous_end = segments[index - 1][-1][0] if index else segment[0][0]
            energy, seen = _segment_energy(segment, boundary_start, boundary_end, index == 0, previous_end)
            total += energy
            counted = counted or seen
        if counted:
            out[hour.replace(tzinfo=None).isoformat()] = round(total / 1000.0, 4)
        hour += timedelta(hours=1)
    return out


def saveeye_partial_hour_kwh(conn, hour_start_local, now_local, device_serial):
    """Energy so far in the current, still-open hour — the counterpart to
    saveeye_hourly_kwh for an hour that hasn't finished yet.

    saveeye_hourly_kwh only ever fills a *completed* hour (samples bracket
    both ends), which means a freshly-connected Saveeye shows nothing at all
    for "today" until after the current hour ends — technically honest, but
    not what anyone watching a live power reading expects. This uses the
    earliest sample at-or-after the hour started as the baseline instead of
    interpolating backward past it, so a mid-hour connection undercounts
    that hour rather than guessing at energy used before it existed —
    `"partial": True` says so explicitly.
    """
    if not device_serial:
        return None
    rows = conn.execute(
        "SELECT ts_utc, cumulative_wh FROM saveeye_samples "
        "WHERE device_serial = ? AND ts_utc >= ? AND ts_utc <= ? ORDER BY ts_utc",
        (device_serial, hour_start_local.astimezone(timezone.utc).isoformat(), now_local.astimezone(timezone.utc).isoformat()),
    ).fetchall()
    usable = [
        (datetime.fromisoformat(r["ts_utc"]).timestamp(), r["cumulative_wh"])
        for r in rows
        if r["cumulative_wh"] is not None
    ]
    if len(usable) < 2:
        return None

    # Sum each monotonic run rather than differencing end to end: the counter
    # resets to zero every few days, and a reset mid-hour used to abandon the
    # whole hour rather than accounting for both sides of it.
    watt_hours = 0.0
    for index, segment in enumerate(_counter_segments(usable)):
        watt_hours += segment[-1][1] - segment[0][1]
        if index:
            previous_end = _counter_segments(usable)[index - 1][-1][0]
            gap_hours = max(0.0, (segment[0][0] - previous_end) / 3600.0)
            watt_hours += min(max(0.0, segment[0][1]), MAX_PLAUSIBLE_KW * 1000.0 * gap_hours)
    if watt_hours < 0:
        return None
    return {"kwh": round(watt_hours / 1000.0, 4), "partial": True}


# --- Easee (EV charger, optional, read-only) ---

_easee_token_cache = {
    "username": None,
    "access_token": None,
    "refresh_token": None,
    "expires_at": 0.0,
    "charger_id": None,
}


def _get_easee_access_token(username, password, timeout=15):
    cache = _easee_token_cache
    now = time.time()
    if cache["username"] == username and cache["access_token"] and now < cache["expires_at"]:
        return cache["access_token"]
    if cache["username"] == username and cache["refresh_token"]:
        try:
            token = easee.refresh_token(cache["access_token"], cache["refresh_token"], timeout=timeout)
            cache.update(username=username, **token)
            return token["access_token"]
        except easee.EaseeError:
            pass  # refresh token no longer valid — fall through to a fresh login
    token = easee.login(username, password, timeout=timeout)
    cache.update(username=username, **token)
    return token["access_token"]


def _resolve_easee_charger_id(access_token, configured_charger_id):
    if configured_charger_id:
        return configured_charger_id
    if _easee_token_cache["charger_id"]:
        return _easee_token_cache["charger_id"]
    chargers = easee.get_chargers(access_token)
    if not chargers:
        return None
    charger_id = chargers[0]["id"]
    _easee_token_cache["charger_id"] = charger_id
    return charger_id


def sync_easee(conn, options):
    cfg = get_easee_config(options)
    if not cfg["enabled"] or not cfg["username"] or not cfg["password"]:
        return
    try:
        access_token = _get_easee_access_token(cfg["username"], cfg["password"])
        charger_id = _resolve_easee_charger_id(access_token, cfg["charger_id"])
        if not charger_id:
            _log("Easee sync: no chargers found on this account")
            return
        state = easee.get_charger_state(access_token, charger_id)
    except easee.EaseeError as exc:
        _log(f"Easee sync failed: {exc}")
        return
    ts = _now_iso()
    conn.execute(
        "INSERT OR REPLACE INTO easee_samples "
        "(ts_utc, charger_id, status, session_energy_kwh, total_power_w, reason_for_no_current, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            ts,
            charger_id,
            state["status"],
            state["session_energy_kwh"],
            state["total_power_w"],
            state.get("reason_for_no_current"),
            ts,
        ),
    )
    _set_app_state(conn, "last_easee_sync", ts)
    conn.commit()
    effective = easee.effective_status(state["status"], state["total_power_w"], state.get("reason_for_no_current"))
    reason = easee.describe_reason(state.get("reason_for_no_current"))
    _log(
        f"Easee sync: {effective}"
        + (f" ({state['status']}: {reason})" if effective != state["status"] else "")
        + f", {state['session_energy_kwh']} kWh this session"
    )


def sync_easee_cloud_sessions(conn, options, now=None, force=False):
    """Easee's own record of every session, fetched on a slow schedule.

    The polled samples cannot see the whole of a charge. A session is
    reconstructed from five-minute polls, so whatever was delivered between the
    last poll and the cable coming out is never observed — always missing,
    never over-counting, bounded by the poll interval times the charge rate.
    Easee knows the real total; this asks for it.

    Rows are replaced rather than appended, so a session still running at one
    sync gets its end and final energy from the next.
    """
    cfg = get_easee_config(options)
    if not cfg["enabled"] or not cfg["username"] or not cfg["password"]:
        return

    now = now or datetime.now(timezone.utc)
    if not force:
        last = _get_app_state(conn, "last_easee_sessions_sync")
        if last:
            try:
                elapsed = (now - datetime.fromisoformat(last)).total_seconds()
            except ValueError:
                elapsed = None
            if elapsed is not None and 0 <= elapsed < EASEE_SESSIONS_SYNC_INTERVAL_SECONDS:
                return

    try:
        access_token = _get_easee_access_token(cfg["username"], cfg["password"])
        charger_id = _resolve_easee_charger_id(access_token, cfg["charger_id"])
        if not charger_id:
            return
        sessions = easee.get_sessions(
            access_token, charger_id, now - timedelta(days=EASEE_SESSIONS_SYNC_DAYS), now
        )
    except easee.EaseeError as exc:
        _log(f"Easee session history sync failed: {exc}")
        return

    # From the same clock the throttle above reads, not _now_iso(). Mixing the
    # two makes the interval meaningless the moment `now` is anything but the
    # wall clock — which is every test, and any backfill.
    stamp = now.isoformat(timespec="seconds")
    for session in sessions:
        conn.execute(
            "INSERT OR REPLACE INTO easee_cloud_sessions "
            "(charger_id, connected_at, disconnected_at, energy_kwh, fetched_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (charger_id, session["connected_at"], session["disconnected_at"],
             session["energy_kwh"], stamp),
        )
    _set_app_state(conn, "last_easee_sessions_sync", stamp)
    conn.commit()
    _log(f"Easee session history: {len(sessions)} sessions in the last "
         f"{EASEE_SESSIONS_SYNC_DAYS} days")


def cloud_sessions(conn, charger_id, days=30, now_local=None):
    """Stored Easee sessions overlapping the window, oldest first."""
    now_local = now_local or datetime.now(LOCAL_TZ)
    window_start = (now_local - timedelta(days=days)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    # A day of lead-in, matching easee_sessions: a session that began just
    # before the window can still have finished inside it.
    query_start = (window_start - timedelta(days=1)).astimezone(timezone.utc)
    rows = conn.execute(
        "SELECT connected_at, disconnected_at, energy_kwh FROM easee_cloud_sessions "
        "WHERE charger_id = ? ORDER BY connected_at",
        (charger_id,),
    ).fetchall()

    out = []
    for row in rows:
        started = _parse_easee_stamp(row["connected_at"])
        if started is None or started < query_start:
            continue
        out.append({
            "connected_at": row["connected_at"],
            "disconnected_at": row["disconnected_at"],
            "energy_kwh": row["energy_kwh"],
            "_start": started,
            "_end": _parse_easee_stamp(row["disconnected_at"]),
        })
    return out


def _parse_easee_stamp(value):
    """Easee's timestamps, as an aware UTC datetime, or None.

    Their API has been seen returning both a 'Z' suffix and a bare naive
    stamp; fromisoformat rejects the first on older Pythons and reads the
    second as local time. Normalising here keeps that in one place.
    """
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# A DISCONNECTED sample means no car is attached, so whatever session was
# running is over. OFFLINE is deliberately not in here: it means the charger is
# unreachable, which says nothing about whether the cable is still in.
SESSION_ENDING_STATUSES = frozenset({"DISCONNECTED"})


def _split_sessions(rows):
    """Chronological samples cut into individual charging sessions.

    Two things end a session, and both are needed:

    - The counter decreasing, which is Easee resetting `sessionEnergy` for a
      new session.
    - A DISCONNECTED sample, which is the car being unplugged. Without this the
      counter simply holds its final value after a charge ends, no decrease
      ever arrives, and the "current session" grows for as long as the sample
      window is deep — reporting a start time that recedes further into the
      past every day while describing a charge that finished on Tuesday.
    """
    sessions = []
    current = []
    for row in rows:
        if row["status"] in SESSION_ENDING_STATUSES:
            if current:
                sessions.append(current)
                current = []
            continue
        if current:
            previous = current[-1]["session_energy_kwh"]
            energy = row["session_energy_kwh"]
            if previous is None or energy is None or energy < previous - 0.01:
                sessions.append(current)
                current = []
        current.append(row)
    if current:
        sessions.append(current)
    return sessions


def _price_session(conn, opts, price_area, session_rows, start_observed):
    """Energy and cost for one session's run of samples.

    Shared by the live card and the history, so a session cannot be costed one
    way on the dashboard and another way in the list underneath it.

    Cost is attributed by multiplying each poll-to-poll energy delta by the
    price at the later sample's hour — the same mechanism a spot meter reading
    would use, just applied to one appliance's session rather than the whole
    house. The subtlety is what the *first* sample of a session means, because
    deltas alone never price it:

    - If a session boundary was observed just before it, the session began in
      that gap and its energy belongs here — priced at that first sample's
      hour, the only hour it can have happened in.
    - If no boundary was observed, this run is simply where our samples start.
      The charger may have been going long before the add-on was installed,
      restarted, or before the window begins. That energy was consumed at
      prices nobody recorded, so it is left out and `cost_is_partial` says so,
      rather than the cost silently describing less energy than
      `session_energy_kwh` claims — which reads on screen as an implausibly
      cheap charge.
    """
    start_local = datetime.fromisoformat(session_rows[0]["ts_utc"]).astimezone(LOCAL_TZ).replace(
        minute=0, second=0, microsecond=0
    )
    end_local = datetime.fromisoformat(session_rows[-1]["ts_utc"]).astimezone(LOCAL_TZ).replace(
        minute=0, second=0, microsecond=0
    ) + timedelta(hours=1)
    hourly_totals = _hourly_totals(quarter_prices_with_total(conn, start_local, end_local, price_area, opts))

    def _price_at(ts_utc):
        hour_key = (
            datetime.fromisoformat(ts_utc)
            .astimezone(LOCAL_TZ)
            .replace(minute=0, second=0, microsecond=0, tzinfo=None)
            .isoformat()
        )
        return hourly_totals.get(hour_key)

    cost = 0.0
    covered_kwh = 0.0
    cost_known = True

    baseline_kwh = session_rows[0]["session_energy_kwh"]
    if start_observed and baseline_kwh:
        price = _price_at(session_rows[0]["ts_utc"])
        if price is None:
            cost_known = False
        else:
            cost += baseline_kwh * price
            covered_kwh += baseline_kwh

    for prev, cur in zip(session_rows, session_rows[1:]):
        if prev["session_energy_kwh"] is None or cur["session_energy_kwh"] is None:
            continue
        delta = cur["session_energy_kwh"] - prev["session_energy_kwh"]
        if delta <= 0:
            continue
        price = _price_at(cur["ts_utc"])
        if price is None:
            cost_known = False
            continue
        cost += delta * price
        covered_kwh += delta

    session_energy = session_rows[-1]["session_energy_kwh"]
    # Nothing priced at all (a lone sample, say) is not "0 kr" — it is not
    # known, and 0.00 on screen would be a claim we cannot make.
    if covered_kwh <= 0:
        cost_dkk = 0.0 if not session_energy else None
    else:
        cost_dkk = round(cost, 2) if cost_known else None

    return {
        "session_energy_kwh": session_energy,
        "session_cost_dkk": cost_dkk,
        "cost_covers_kwh": round(covered_kwh, 3),
        "cost_is_partial": bool(session_energy and covered_kwh + 0.01 < session_energy),
    }


def _trim_session(session_rows):
    """A session's samples cut down to the stretch where energy actually moved,
    plus whether any movement was seen at all.

    Easee's counter holds its final value indefinitely after a charge, and a
    car can sit plugged in for days without drawing anything. Left untrimmed a
    "session" therefore runs from the moment the cable went in until the moment
    it came out — reporting a 4-hour charge as lasting 159 hours, which is not
    a charging session by any reading.

    The span kept is from the sample *before* the first increase (the baseline
    the cost is measured from) to the sample *at* the last increase. Trailing
    idle is not part of the charge, and neither is the waiting beforehand.

    Deliberately not a split: `session_energy_kwh` is a cumulative counter, so
    cutting a paused-then-resumed charge in two would make the second half
    report the whole session's energy as its own.
    """
    increases = [
        i for i in range(1, len(session_rows))
        if (session_rows[i]["session_energy_kwh"] or 0) > (session_rows[i - 1]["session_energy_kwh"] or 0)
    ]
    if not increases:
        # The counter never moved while we were watching. Whatever it reads is
        # a value we found, not a charge we observed.
        return session_rows, False
    return session_rows[increases[0] - 1 : increases[-1] + 1], True


# Below this, a claimed power is small enough that a poll's worth of it could
# plausibly round away in the counter. Above it, energy must visibly move.
STALE_READING_MIN_KWH = 0.1


def _stale_reading(rows):
    """Whether the newest sample is a frozen reading rather than a live one.

    Easee's cloud serves a charger's last known state when it cannot reach it,
    with no indication that it is doing so. Observed in the wild: `CHARGING` at
    10.64 kW for 158 hours straight with `sessionEnergy` unchanged at 26.510 —
    which, had it been real, would have been 1,677 kWh through one car.

    Neither earlier check catches it. The power is far above the pause
    threshold, and the add-on is polling perfectly happily, so the reading is
    fresh by every measure except the one that matters.

    The invariant that does catch it is physical: **if power is flowing, energy
    must accumulate**. A charger drawing 10.6 kW adds about 0.9 kWh every
    five-minute poll. So walk back over samples whose counter has not moved,
    work out how much energy the claimed power should have delivered in that
    span, and if it is more than a rounding error the charger is not doing what
    it says. Scaling by the claimed power rather than using a fixed time window
    is what keeps a genuine trickle charge from being called stale.
    """
    if not rows:
        return None
    newest = rows[-1]
    power_w = newest["total_power_w"]
    energy = newest["session_energy_kwh"]
    if not power_w or power_w < easee.CHARGING_POWER_THRESHOLD_W or energy is None:
        return None

    oldest_same = newest
    for row in reversed(rows[:-1]):
        if row["session_energy_kwh"] is None or abs(row["session_energy_kwh"] - energy) > 0.0001:
            break
        oldest_same = row
    if oldest_same is newest:
        return None

    seconds = (
        datetime.fromisoformat(newest["ts_utc"]) - datetime.fromisoformat(oldest_same["ts_utc"])
    ).total_seconds()
    expected_kwh = power_w / 1000.0 * (seconds / 3600.0)
    if expected_kwh <= STALE_READING_MIN_KWH:
        return None
    return {
        "since": oldest_same["ts_utc"],
        "hours": round(seconds / 3600.0, 1),
        "claimed_kw": round(power_w / 1000.0, 2),
        "expected_kwh": round(expected_kwh, 1),
    }


def easee_current_session(conn, opts, price_area, charger_id):
    """The current (or most recent) charging session's energy and cost, plus
    what the charger is doing right now.

    Those are two different questions and are answered from two different
    places. Status and power come from the newest sample, because "now" is
    now. Energy and cost come from the most recent session that actually drew
    something — so unplugging the car leaves the card showing what that charge
    cost, rather than blanking to a zero-energy segment.

    Cost is attributed by multiplying each poll-to-poll energy delta by the
    price at the later sample's hour — the same mechanism a spot meter reading
    would use, just applied to one appliance's session rather than the whole
    house. The subtlety is what the *first* sample of a session means, because
    deltas alone never price it:

    - If a session boundary was observed just before it, the session began in
      that gap and its energy belongs here — priced at that first sample's
      hour, the only hour it can have happened in.
    - If no boundary was observed, this run is simply where our samples start.
      The charger may have been going long before the add-on was installed,
      restarted, or before the window begins. That energy was consumed at
      prices nobody recorded, so it is left out and `cost_is_partial` says so,
      rather than the cost silently describing less energy than
      `session_energy_kwh` claims — which reads on screen as an implausibly
      cheap charge.
    """
    rows = conn.execute(
        "SELECT ts_utc, session_energy_kwh, total_power_w, status, reason_for_no_current FROM easee_samples "
        "WHERE charger_id = ? ORDER BY ts_utc DESC LIMIT 500",
        (charger_id,),
    ).fetchall()
    if not rows:
        return None
    rows = list(reversed(rows))  # chronological
    newest = rows[-1]

    sessions = _split_sessions(rows)
    # The most recent session that drew anything. A car sitting plugged in at
    # 0 kWh has not started a charge, and showing it would throw away the one
    # that just finished.
    session_rows = None
    fallback = None
    for candidate in reversed(sessions):
        trimmed, charging_seen = _trim_session(candidate)
        if charging_seen:
            session_rows = trimmed
            break
        if fallback is None and candidate[-1]["session_energy_kwh"]:
            # A counter reading with no movement behind it. Worth showing if
            # nothing better exists, since it is still Easee's own session
            # figure, but never in preference to a charge we saw happen.
            fallback = candidate
    if session_rows is None:
        session_rows = fallback or (sessions[-1] if sessions else [newest])

    # The session's start was observed if something closed a session before it,
    # rather than the sample window simply beginning mid-charge.
    start_observed = rows.index(session_rows[0]) > 0

    priced = _price_session(conn, opts, price_area, session_rows, start_observed)
    session_energy = priced["session_energy_kwh"]
    cost_dkk = priced["session_cost_dkk"]
    covered_kwh = priced["cost_covers_kwh"]

    raw_status = newest["status"]
    reason_code = newest["reason_for_no_current"] if "reason_for_no_current" in newest.keys() else None
    status = easee.effective_status(raw_status, newest["total_power_w"], reason_code)
    # A frozen reading outranks everything else: reporting CHARGING from numbers
    # that have not moved in days is worse than admitting we do not know.
    stale = _stale_reading(rows)
    if stale:
        status = "STALE"

    return {
        # What the charger is doing, which is not always what it says: Easee
        # holds chargerOpMode at CHARGING through a pause. See effective_status.
        "status": status,
        "raw_status": raw_status,
        "reason": easee.describe_reason(reason_code),
        "charging": status == "CHARGING",
        # Set when the charger's own numbers have stopped moving while still
        # claiming power — see _stale_reading.
        "stale_reading": stale,
        "session_energy_kwh": session_energy,
        "total_power_w": newest["total_power_w"],
        "session_cost_dkk": cost_dkk,
        "session_started_at": session_rows[0]["ts_utc"],
        "session_ended_at": None if session_rows[-1] is newest else session_rows[-1]["ts_utc"],
        "session_start_observed": start_observed,
        # What the cost figure actually accounts for, so the two numbers on
        # screen can be compared instead of silently disagreeing.
        "cost_covers_kwh": covered_kwh,
        "cost_is_partial": priced["cost_is_partial"],
        # When this reading was taken — a status is only as current as the
        # poll behind it, and a failed sync writes no row at all.
        "measured_at": newest["ts_utc"],
    }


def correct_current_session(conn, opts, price_area, charger_id, session, now_local=None):
    """Give the live card the same energy the history shows for that session.

    Without this the two disagree on one screen — the card reporting 20.06 kWh
    from the polls while the list underneath it reports Easee's 20.58 for the
    same charge. Reconciling behind only the history endpoint was not enough,
    because the card is a different endpoint reading the same event.

    **Only once the charge has finished.** While one is running, Easee's record
    is fetched hourly and is therefore behind the live counter; correcting from
    it would make the number on screen jump backwards between refreshes, which
    is worse than being a few hundred watt-hours low for an hour.

    Routed through the same `_reconcile_one` the history uses, rather than
    repeating the arithmetic here — two copies of this rule would eventually
    disagree, which is the bug being fixed.
    """
    if not session or session.get("session_ended_at") is None:
        return session

    cloud = cloud_sessions(conn, charger_id, days=7, now_local=now_local)
    if not cloud:
        return session

    as_sampled = {
        "started_at": session["session_started_at"],
        "ended_at": session["session_ended_at"],
        "energy_kwh": session["session_energy_kwh"],
        "cost_dkk": session["session_cost_dkk"],
        "cost_covers_kwh": session["cost_covers_kwh"],
        "cost_is_partial": session["cost_is_partial"],
    }
    now_utc = (now_local or datetime.now(LOCAL_TZ)).astimezone(timezone.utc)
    matches = [
        entry for entry in cloud
        if _overlaps(as_sampled, entry["_start"], entry["_end"] or now_utc)
    ]
    if len(matches) != 1:
        # No record yet, or one plug-in covering several charges. Same rule as
        # the history: say nothing rather than guess.
        return session

    start_local = datetime.fromisoformat(as_sampled["started_at"]).astimezone(LOCAL_TZ)
    end_local = datetime.fromisoformat(as_sampled["ended_at"]).astimezone(LOCAL_TZ)
    price_at = _hour_pricer(conn, opts, price_area,
                            start_local - timedelta(hours=1), end_local + timedelta(hours=2))
    merged = _reconcile_one(as_sampled, matches[0], price_at)
    return {
        **session,
        "session_energy_kwh": merged["energy_kwh"],
        "session_cost_dkk": merged["cost_dkk"],
        "cost_covers_kwh": merged["cost_covers_kwh"],
        "cost_is_partial": merged["cost_is_partial"],
        "energy_source": merged["energy_source"],
    }


def easee_sessions(conn, opts, price_area, charger_id, days=30, now_local=None):
    """Every charging session that ended (or is running) within `days`, newest
    first, each costed the same way the live card costs the current one.

    The query reaches a day further back than asked for, and sessions are then
    filtered by when they *ended*. Without that lead-in the oldest session in
    range would begin at the first row of the result and so look like one whose
    start was never observed — reported as partially costed purely because of
    where the window happened to be cut.
    """
    now_local = now_local or datetime.now(LOCAL_TZ)
    window_start = (now_local - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    query_start = (window_start - timedelta(days=1)).astimezone(timezone.utc).isoformat()

    rows = conn.execute(
        "SELECT ts_utc, session_energy_kwh, total_power_w, status, reason_for_no_current FROM easee_samples "
        "WHERE charger_id = ? AND ts_utc >= ? ORDER BY ts_utc",
        (charger_id, query_start),
    ).fetchall()
    if not rows:
        return []

    out = []
    for index, raw_rows in _split_sessions_with_index(rows):
        if not raw_rows[-1]["session_energy_kwh"]:
            continue  # plugged in but never drew anything: not a charge
        session_rows, charging_seen = _trim_session(raw_rows)
        if not charging_seen:
            # The counter never moved across this whole run — a value left over
            # from a charge that happened before the samples begin. Listing it
            # would put a session in the history that nothing here ever saw.
            continue
        index += raw_rows.index(session_rows[0])
        energy = session_rows[-1]["session_energy_kwh"]
        ended_local = datetime.fromisoformat(session_rows[-1]["ts_utc"]).astimezone(LOCAL_TZ)
        if ended_local < window_start:
            continue

        start_observed = index > 0
        priced = _price_session(conn, opts, price_area, session_rows, start_observed)
        started_local = datetime.fromisoformat(session_rows[0]["ts_utc"]).astimezone(LOCAL_TZ)
        minutes = round((ended_local - started_local).total_seconds() / 60)
        covered = priced["cost_covers_kwh"]
        cost = priced["session_cost_dkk"]
        out.append(
            {
                "started_at": session_rows[0]["ts_utc"],
                "ended_at": session_rows[-1]["ts_utc"],
                "day": started_local.date().isoformat(),
                "energy_kwh": round(energy, 3),
                "cost_dkk": cost,
                # Against what the cost actually covers, not the full session —
                # otherwise a partially observed charge reports a rate it never
                # paid, which is the same trap the cost figure itself fell into.
                "avg_dkk_kwh": round(cost / covered, 4) if cost is not None and covered else None,
                "duration_minutes": minutes,
                "cost_covers_kwh": covered,
                "cost_is_partial": priced["cost_is_partial"],
                "ongoing": session_rows[-1] is rows[-1],
            }
        )
    out.reverse()
    return out


def _split_sessions_with_index(rows):
    """`_split_sessions`, but each session paired with its first row's index —
    which is what says whether anything preceded it, and therefore whether its
    start was observed rather than merely being where the samples begin."""
    positions = {id(row): i for i, row in enumerate(rows)}
    return [(positions[id(session[0])], session) for session in _split_sessions(rows)]


def _hour_pricer(conn, opts, price_area, start_local, end_local):
    """A callable giving the all-in price for the hour an instant falls in.

    Built once per request over the whole window rather than queried per hour:
    the reconciliation asks about scattered hours across thirty days, and one
    query beats several hundred.
    """
    hourly_totals = _hourly_totals(
        quarter_prices_with_total(conn, start_local, end_local, price_area, opts))

    def price_at(when):
        if when is None:
            return None
        key = (when.astimezone(LOCAL_TZ)
               .replace(minute=0, second=0, microsecond=0, tzinfo=None).isoformat())
        return hourly_totals.get(key)

    return price_at


def easee_sessions_reconciled(conn, opts, price_area, charger_id, days=30, now_local=None):
    """Charging history with Easee's own totals folded into the polled ones.

    The seam between the two lives here so that everything downstream — the
    daily chart, the monthly roll-up, the totals — sees one list of sessions
    and cannot disagree with the list printed underneath it.
    """
    now_local = now_local or datetime.now(LOCAL_TZ)
    sampled = easee_sessions(conn, opts, price_area, charger_id, days=days,
                             now_local=now_local)
    cloud = cloud_sessions(conn, charger_id, days=days, now_local=now_local)
    if not cloud:
        # Nothing fetched yet (or the option was only just turned on). The
        # polled history is what there is, and it is better than an empty page.
        return [{**session, "energy_source": "polled"} for session in sampled]

    window_start = (now_local - timedelta(days=days + 1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    price_at = _hour_pricer(conn, opts, price_area, window_start,
                            now_local + timedelta(hours=1))
    return reconcile_sessions(sampled, cloud, price_at, LOCAL_TZ,
                              now_utc=now_local.astimezone(timezone.utc))


def reconcile_sessions(sampled, cloud, price_at_hour, local_tz, now_utc=None):
    """The polled sessions corrected against Easee's own record.

    Two sources, each right about something different:

    - **Energy** is Easee's. The polled figure is the counter as of the last
      poll that saw it rising, so anything delivered between that poll and the
      cable coming out is missing — always low, never high, bounded by the poll
      interval times the charge rate.
    - **Timing** is the polls'. Easee reports `carConnected`/`carDisconnected`,
      which is plug-in to unplug: a car left on an overnight schedule reports
      twelve hours, of which it charged for two. The polled window is the one
      that actually describes charging, so it is kept wherever it exists.

    A session Easee knows about and the polls never saw at all — the add-on was
    down, or restarted mid-charge — is emitted from Easee's record alone, with
    its energy spread evenly across the plug-in span for costing and flagged so
    the screen can say the cost is an estimate rather than a measurement.

    Deliberately conservative where the two disagree in shape: if several
    polled sessions fall inside one plug-in, the polled ones are left exactly
    as they are. Attributing one cloud total across them would either
    double-count the energy or invent a split of it, and neither is better than
    saying nothing.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    remaining = list(sampled)
    out = []

    for entry in cloud:
        start = entry["_start"]
        end = entry["_end"] or now_utc
        matches = [
            session for session in remaining
            if _overlaps(session, start, end)
        ]
        if len(matches) == 1:
            out.append(_reconcile_one(matches[0], entry, price_at_hour))
            remaining.remove(matches[0])
        elif matches:
            # More than one polled session inside a single plug-in. Leave them
            # untouched rather than guess how the cloud total divides.
            for session in matches:
                out.append({**session, "energy_source": "polled"})
                remaining.remove(session)
        else:
            out.append(_session_from_cloud(entry, price_at_hour, local_tz, now_utc))

    # Polled sessions with no cloud counterpart. Not an error: the hourly
    # session sync may simply not have run since the charge finished, and
    # dropping them would make a charge vanish for up to an hour.
    for session in remaining:
        out.append({**session, "energy_source": "polled"})

    out.sort(key=lambda session: session["started_at"], reverse=True)
    return out


def _overlaps(session, start, end):
    """Does a polled session's charging window fall within a plug-in span?

    A minute of slack at each end: the polls that bracket a charge sit up to
    one interval outside the plug-in moment, and an exact comparison would miss
    the match that matters most — the one at the boundary.
    """
    try:
        session_start = datetime.fromisoformat(session["started_at"])
        session_end = datetime.fromisoformat(session["ended_at"])
    except (ValueError, KeyError):
        return False
    slack = timedelta(minutes=10)
    return session_start >= start - slack and session_end <= end + slack


def _reconcile_one(session, entry, price_at_hour):
    """One polled session, given Easee's energy total for it."""
    cloud_energy = entry["energy_kwh"]
    polled_energy = session["energy_kwh"] or 0.0
    missing = round(cloud_energy - polled_energy, 3)

    merged = {**session, "energy_kwh": round(cloud_energy, 3),
              "energy_source": "easee", "polled_energy_kwh": session["energy_kwh"]}
    if missing <= 0.01:
        # Easee agrees with the polls, or reports slightly less (a rounding
        # difference). Nothing to add, and nothing to explain on screen.
        merged["energy_kwh"] = round(max(cloud_energy, polled_energy), 3)
        return _recost(merged, session["cost_dkk"], session["cost_covers_kwh"])

    # The missing energy was delivered between the last poll and the unplug, so
    # it was priced at the hour the polled session ended in — the only hour it
    # can have happened in.
    price = price_at_hour(datetime.fromisoformat(session["ended_at"]))
    if price is None or session["cost_dkk"] is None:
        return _recost(merged, session["cost_dkk"], session["cost_covers_kwh"])
    return _recost(
        merged,
        round(session["cost_dkk"] + missing * price, 2),
        round((session["cost_covers_kwh"] or 0.0) + missing, 3),
    )


def _recost(session, cost, covered):
    """Re-derive the figures that depend on energy, cost and coverage together.

    Kept in one place because they have to agree: an average rate computed
    against energy the cost does not cover reports a price that was never paid,
    which is the trap the cost figure itself already fell into once.
    """
    energy = session["energy_kwh"]
    session["cost_dkk"] = cost
    session["cost_covers_kwh"] = covered
    session["cost_is_partial"] = bool(energy and (covered or 0.0) + 0.01 < energy)
    session["avg_dkk_kwh"] = (
        round(cost / covered, 4) if cost is not None and covered else None
    )
    return session


def _session_from_cloud(entry, price_at_hour, local_tz, now_utc):
    """A session Easee recorded that the polls never saw.

    Its span is plug-in to unplug rather than a charging window, so the
    duration is not comparable with a polled session's and the cost can only be
    an estimate: the energy is spread evenly across the hours, which is what
    you would assume knowing nothing, and flagged as an assumption.
    """
    start = entry["_start"]
    end = entry["_end"] or now_utc
    started_local = start.astimezone(local_tz)
    energy = entry["energy_kwh"]
    cost, covered = _spread_cost(start, end, energy, price_at_hour)

    session = {
        "started_at": start.isoformat(),
        "ended_at": end.isoformat(),
        "day": started_local.date().isoformat(),
        "energy_kwh": round(energy, 3),
        "cost_dkk": cost,
        "avg_dkk_kwh": None,
        "duration_minutes": round((end - start).total_seconds() / 60),
        "cost_covers_kwh": covered,
        "cost_is_partial": False,
        "ongoing": entry["_end"] is None,
        "energy_source": "easee",
        "polled_energy_kwh": None,
        # Two separate warnings, and they are not the same thing: the cost was
        # assumed rather than measured, and the span is how long the cable was
        # in rather than how long it charged.
        "cost_is_estimated": True,
        "span_is_plugged_in": True,
    }
    return _recost(session, cost, covered)


def _spread_cost(start, end, energy, price_at_hour):
    """Cost of `energy` spread evenly over [start, end) at each hour's price.

    Returns (cost, covered_kwh), and refuses to guess: an hour with no price
    contributes no cost and no coverage, so a span that is only half priced
    reports as partial rather than as cheap.
    """
    total_seconds = (end - start).total_seconds()
    if total_seconds <= 0 or not energy:
        return None, 0.0

    cost = 0.0
    covered = 0.0
    cursor = start
    while cursor < end:
        hour_end = min((cursor + timedelta(hours=1)).replace(
            minute=0, second=0, microsecond=0), end)
        if hour_end <= cursor:
            break
        share = energy * ((hour_end - cursor).total_seconds() / total_seconds)
        price = price_at_hour(cursor)
        if price is not None:
            cost += share * price
            covered += share
        cursor = hour_end

    if covered <= 0:
        return None, 0.0
    return round(cost, 2), round(covered, 3)


def trips_with_charging(trips, sessions):
    """Each trip with the charging that happened during it.

    **What this counts is charging within the trip's dates, not the energy the
    trip consumed.** They are not the same thing and the difference is not
    small: you arrive home empty and plug in that evening, so the charge that
    paid for the last 200 km falls on the day you got back. Ending a trip on
    the day you plugged in rather than the day you arrived is what makes the
    figure mean what you want it to — and that is a decision only the keeper
    can make, so the app does not guess it.

    `kwh_per_100km` inherits the same caveat and is offered because it is the
    number an EV owner actually compares between trips, not because it is a
    measurement of consumption.
    """
    out = []
    for trip in trips:
        during = [
            session for session in sessions
            if trip["started_on"] <= session["day"] <= trip["ended_on"]
        ]
        energy = round(sum(session["energy_kwh"] or 0 for session in during), 2)
        # One unpriced session makes the whole total unknown rather than low —
        # the same rule the monthly roll-up uses, for the same reason: a
        # partial figure sitting next to complete ones invites comparison.
        costs = [session["cost_dkk"] for session in during]
        cost = round(sum(costs), 2) if costs and all(c is not None for c in costs) else None
        distance = trip.get("distance_km")
        out.append({
            **trip,
            "sessions": len(during),
            "energy_kwh": energy,
            "cost_dkk": cost,
            "kwh_per_100km": (round(energy / distance * 100, 1)
                              if distance and energy else None),
            "dkk_per_100km": (round(cost / distance * 100, 2)
                              if distance and cost is not None else None),
        })
    return out


def trip_days(trips):
    """Every calendar day any trip covers, for marking the chart."""
    days = {}
    for trip in trips:
        start = _parse_day(trip["started_on"])
        end = _parse_day(trip["ended_on"])
        if start is None or end is None:
            continue
        current = start
        while current <= end:
            days.setdefault(current.isoformat(), trip["label"])
            current += timedelta(days=1)
    return days


def _parse_day(value):
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def easee_charging_totals(sessions):
    """Roll a list of sessions up into the figures the history card shows.

    Sessions whose cost is partial contribute their energy but are counted
    separately, so a total that is missing some of its cost says so rather than
    reporting a suspiciously cheap month.
    """
    energy = round(sum(s["energy_kwh"] for s in sessions), 2)
    costed = [s for s in sessions if s["cost_dkk"] is not None]
    cost = round(sum(s["cost_dkk"] for s in costed), 2) if costed else None
    covered = round(sum(s["cost_covers_kwh"] for s in costed), 3)
    partial = [s for s in sessions if s["cost_is_partial"] or s["cost_dkk"] is None]
    return {
        "sessions": len(sessions),
        "energy_kwh": energy,
        "cost_dkk": cost,
        "avg_dkk_kwh": round(cost / covered, 4) if cost is not None and covered else None,
        "partial_sessions": len(partial),
        "longest_minutes": max((s["duration_minutes"] for s in sessions), default=0),
    }


def easee_monthly_charging(sessions, today=None):
    """Sessions rolled up per calendar month, newest first, plus the average
    across the complete ones.

    Calendar months rather than 30-day blocks: "how much did I charge in July"
    is the question people actually have, and a rolling window cannot answer it.

    Two things are kept honest here, both for the same reason — an average is
    only worth having if you know what went into it:

    - **The current month is partial** and is excluded from the average. It is
      still listed, because you want to see it, but a month that is four days
      old would otherwise drag the mean down and make every August look like a
      collapse in usage.
    - **Cost is dropped from a month's total if any session in it is not fully
      costed.** `easee_charging_totals` already refuses to report a
      "suspiciously cheap month" for this reason; a monthly table makes the
      trap worse, because a month missing half its spot prices sits next to
      complete ones and invites comparison.
    """
    if not sessions:
        return {"months": [], "average": None}

    today = today or datetime.now(LOCAL_TZ).date()
    current = today.strftime("%Y-%m")

    by_month = {}
    for session in sessions:
        month = session["day"][:7]
        entry = by_month.setdefault(month, {
            "month": month, "sessions": 0, "energy_kwh": 0.0,
            "cost_dkk": 0.0, "cost_known": True, "covered_kwh": 0.0,
        })
        entry["sessions"] += 1
        entry["energy_kwh"] += session["energy_kwh"]
        if session["cost_dkk"] is None or session["cost_is_partial"]:
            entry["cost_known"] = False
        else:
            entry["cost_dkk"] += session["cost_dkk"]
            entry["covered_kwh"] += session["cost_covers_kwh"]

    months = []
    for entry in sorted(by_month.values(), key=lambda e: e["month"], reverse=True):
        known = entry["cost_known"]
        cost = round(entry["cost_dkk"], 2) if known else None
        covered = entry["covered_kwh"]
        months.append({
            "month": entry["month"],
            "sessions": entry["sessions"],
            "energy_kwh": round(entry["energy_kwh"], 2),
            "cost_dkk": cost,
            "avg_dkk_kwh": round(cost / covered, 4) if known and covered else None,
            # True for the month still being lived through: shown, but never
            # averaged, because it is not finished.
            "partial": entry["month"] == current,
        })

    complete = [m for m in months if not m["partial"]]
    average = None
    if complete:
        costed = [m for m in complete if m["cost_dkk"] is not None]
        average = {
            "months": len(complete),
            "sessions": round(sum(m["sessions"] for m in complete) / len(complete), 1),
            "energy_kwh": round(sum(m["energy_kwh"] for m in complete) / len(complete), 2),
            # Averaged over the months whose cost is fully known, and saying how
            # many those were — an average over three of five months is a fact,
            # an average presented as over five would be a guess.
            "cost_dkk": round(sum(m["cost_dkk"] for m in costed) / len(costed), 2) if costed else None,
            "cost_months": len(costed),
        }
    return {"months": months, "average": average}


def easee_daily_charging(sessions):
    """Per-day totals for the history chart, oldest first and with the empty
    days present — a gap in a time series has to be a zero, not a missing
    point, or the line implies charging on a day nothing was plugged in."""
    if not sessions:
        return []
    by_day = {}
    for session in sessions:
        entry = by_day.setdefault(
            session["day"], {"day": session["day"], "kwh": 0.0, "cost": 0.0, "cost_known": True, "sessions": 0}
        )
        entry["kwh"] += session["energy_kwh"]
        entry["sessions"] += 1
        if session["cost_dkk"] is None:
            entry["cost_known"] = False
        else:
            entry["cost"] += session["cost_dkk"]

    days = sorted(by_day)
    cursor = date.fromisoformat(days[0])
    last = date.fromisoformat(days[-1])
    out = []
    while cursor <= last:
        key = cursor.isoformat()
        entry = by_day.get(key, {"day": key, "kwh": 0.0, "cost": 0.0, "cost_known": True, "sessions": 0})
        out.append({**entry, "kwh": round(entry["kwh"], 3), "cost": round(entry["cost"], 2)})
        cursor += timedelta(days=1)
    return out


# --- Insights ---
#
# Everything here is derived from rows that already exist. No new tables, no new
# sync: the value is in asking questions of what was already collected.


def _price_performance(rows):
    """What you actually paid per kWh, against what a flat consumer would have.

    This is the number that says whether being on a spot tariff is worth
    anything to you. Your average is weighted by when you used power; the
    comparison is the plain mean of the same hours' prices, which is what
    somebody consuming identically every hour would have paid. Beating it means
    your consumption is genuinely leaning into the cheap hours.

    Only hours with both a reading and a price count, on both sides — comparing
    a weighted average over one set of hours with a flat average over a
    different set would not be a comparison at all.
    """
    priced = [r for r in rows if r["cost_dkk"] is not None and r["price_dkk_kwh"] is not None]
    kwh = sum(r["kwh"] for r in priced)
    cost = sum(r["cost_dkk"] for r in priced)
    if not priced or kwh <= 0:
        return None
    paid = cost / kwh
    flat = sum(r["price_dkk_kwh"] for r in priced) / len(priced)
    return {
        "avg_paid_dkk_kwh": round(paid, 4),
        "flat_dkk_kwh": round(flat, 4),
        # Positive means cheaper than consuming evenly; negative means the
        # opposite, which is worth knowing and is not hidden.
        "difference_pct": round((flat - paid) / flat * 100, 1) if flat else None,
        "difference_dkk": round((flat - paid) * kwh, 2),
        "hours": len(priced),
        "kwh": round(kwh, 2),
    }


def _hourly_profile(rows):
    """Average consumption and average price paid, by hour of the day.

    A household's shape is remarkably stable, and seeing it next to the price
    curve is what turns "shift usage to cheap hours" from advice into a
    specific hour to move something to.
    """
    buckets = {}
    for row in rows:
        hour = int(row["time_dk"][11:13])
        entry = buckets.setdefault(hour, {"hour": hour, "kwh": 0.0, "cost": 0.0, "days": 0, "priced": 0})
        entry["kwh"] += row["kwh"]
        entry["days"] += 1
        if row["cost_dkk"] is not None:
            entry["cost"] += row["cost_dkk"]
            entry["priced"] += 1
    out = []
    for hour in range(24):
        entry = buckets.get(hour)
        if not entry or not entry["days"]:
            out.append({"hour": hour, "avg_kwh": 0.0, "avg_price": None, "samples": 0})
            continue
        out.append({
            "hour": hour,
            "avg_kwh": round(entry["kwh"] / entry["days"], 3),
            "avg_price": round(entry["cost"] / entry["kwh"], 4) if entry["kwh"] > 0 and entry["priced"] else None,
            "samples": entry["days"],
        })
    return out


def _baseline_load(rows):
    """The load that never goes away, estimated as the 10th percentile of
    hourly consumption.

    Not the minimum: a single hour of a power cut or a gap in reporting would
    define it, and the answer would be zero. The 10th percentile is the level
    the house sits at when nothing in particular is happening — standby draw,
    the fridge, the router, whatever is always on. Annualised it is usually a
    surprising number, which is the point of showing it.
    """
    values = sorted(r["kwh"] for r in rows)
    if len(values) < 24:
        return None  # less than a day of hours says nothing about a baseline
    index = max(0, int(len(values) * 0.10) - 1)
    kwh_per_hour = values[index]
    return {
        "kw": round(kwh_per_hour, 3),
        "annual_kwh": round(kwh_per_hour * 24 * 365, 0),
        "share_pct": round(kwh_per_hour * len(values) / sum(values) * 100, 1) if sum(values) else None,
    }


def _day_totals(rows):
    days = {}
    for row in rows:
        day = row["time_dk"][:10]
        entry = days.setdefault(day, {"day": day, "kwh": 0.0, "cost": 0.0, "cost_known": True})
        entry["kwh"] += row["kwh"]
        if row["cost_dkk"] is None:
            entry["cost_known"] = False
        else:
            entry["cost"] += row["cost_dkk"]
    return [
        {**d, "kwh": round(d["kwh"], 3), "cost": round(d["cost"], 2)}
        for d in sorted(days.values(), key=lambda d: d["day"])
    ]


def _day_extremes(daily):
    """The days worth looking at: most used, most spent, and the cheapest rate.

    A cheapest-rate day is a different question from a cheapest day, and the
    interesting one — it is the day the household's timing worked.
    """
    if not daily:
        return None
    costed = [d for d in daily if d["cost_known"] and d["kwh"] > 0]
    return {
        "most_kwh": max(daily, key=lambda d: d["kwh"]),
        "most_cost": max(costed, key=lambda d: d["cost"]) if costed else None,
        "best_rate": min(costed, key=lambda d: d["cost"] / d["kwh"]) if costed else None,
        "worst_rate": max(costed, key=lambda d: d["cost"] / d["kwh"]) if costed else None,
    }


def _cheapest_hours_of_day(quarter_rows, top=3):
    """Which hours of the day have been cheapest on average, from price history
    alone — true whether or not any consumption has ever been recorded."""
    buckets = {}
    for row in quarter_rows:
        hour = int(row["time_dk"][11:13])
        entry = buckets.setdefault(hour, [0.0, 0])
        entry[0] += row["total_dkk_kwh"]
        entry[1] += 1
    averages = [
        {"hour": hour, "avg_price": round(total / count, 4)}
        for hour, (total, count) in sorted(buckets.items())
        if count
    ]
    if not averages:
        return None
    ordered = sorted(averages, key=lambda a: a["avg_price"])
    return {"by_hour": averages, "cheapest": ordered[:top], "priciest": list(reversed(ordered[-top:]))}


def build_insights(conn, options, days=30, now_local=None):
    """Everything the Insights tab shows, from rows already collected."""
    now_local = now_local or datetime.now(LOCAL_TZ)
    opts = get_price_options(options)
    cfg = get_eloverblik_config(options)
    start = (now_local - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = now_local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)

    rows = []
    if cfg["metering_point"]:
        saveeye_cfg = get_saveeye_config(options)
        rows = combined_consumption_with_cost(
            conn, start, end, cfg["metering_point"], opts["price_area"], opts,
            resolve_saveeye_device_serial(conn, saveeye_cfg["device_serial"]),
        )

    quarter_rows = quarter_prices_with_total(conn, start, end, opts["price_area"], opts)
    daily = _day_totals(rows)

    ev = None
    easee_cfg = get_easee_config(options)
    if easee_cfg["enabled"]:
        charger_id = easee_cfg["charger_id"] or _easee_token_cache["charger_id"]
        if charger_id:
            sessions = easee_sessions_reconciled(conn, opts, opts["price_area"], charger_id,
                                                 days=days, now_local=now_local)
            totals = easee_charging_totals(sessions)
            house_kwh = sum(d["kwh"] for d in daily)
            share = round(totals["energy_kwh"] / house_kwh * 100, 1) if house_kwh > 0 else None
            ev = {
                **totals,
                # What share of everything the house used went into the car.
                # Only meaningful when there is a house figure to compare to.
                "share_of_house_pct": share,
                # The car draws through the house meter, so its share cannot
                # really exceed 100%. When the arithmetic says otherwise it is
                # because Eloverblik runs days behind Easee: the charging is
                # recorded and the meter reading covering it has not arrived.
                # Saying that is better than printing "102% of the house".
                "house_behind": bool(share is not None and share > 100),
                "house_kwh": round(house_kwh, 2),
            }

    # What a bill would show: energy plus the standing charge, over the energy
    # actually used. Deliberately separate from price_performance, which
    # compares timing and must stay on energy alone — the standing charge is
    # identical however you time your consumption, so including it there would
    # dilute the comparison without changing what it measures.
    priced = [r for r in rows if r["cost_dkk"] is not None]
    energy_cost = sum(r["cost_dkk"] for r in priced)
    energy_kwh = sum(r["kwh"] for r in priced)
    all_in = None
    if priced and energy_kwh > 0:
        # The standing charge is accrued over the span the *priced* energy
        # covers, not the whole requested range. Price history usually starts
        # later than consumption history, so charging a full month against ten
        # days of energy would inflate the rate several-fold and quietly make
        # this figure incomparable with the bill it exists to be compared with.
        covered_start = datetime.fromisoformat(priced[0]["time_dk"]).replace(tzinfo=LOCAL_TZ)
        covered_end = (
            datetime.fromisoformat(priced[-1]["time_dk"]).replace(tzinfo=LOCAL_TZ) + timedelta(hours=1)
        )
        fixed = fixed_charge_for_window(opts, covered_start, covered_end)
        all_in = {
            "energy_dkk": round(energy_cost, 2),
            "fixed_dkk": round(fixed, 2),
            "total_dkk": round(energy_cost + fixed, 2),
            "kwh": round(energy_kwh, 2),
            "all_in_dkk_kwh": round((energy_cost + fixed) / energy_kwh, 4),
            # Said out loud, because it is often shorter than the range asked
            # for and that changes how the figure should be read.
            "covers_from": covered_start.date().isoformat(),
            "covers_to": (covered_end - timedelta(hours=1)).date().isoformat(),
        }

    return {
        "days": days,
        "from": start.date().isoformat(),
        "to": (end - timedelta(days=1)).date().isoformat(),
        "consumption_hours": len(rows),
        "all_in": all_in,
        "price_performance": _price_performance(rows),
        "hourly_profile": _hourly_profile(rows),
        "baseline": _baseline_load(rows),
        "daily": daily,
        "extremes": _day_extremes(daily),
        "prices": _cheapest_hours_of_day(quarter_rows),
        "ev": ev,
        "price_config_warning": price_config_warning(opts),
    }


# --- Home Assistant sensors ---


def _ha_api(path, method="GET", payload=None, timeout=10):
    if not SUPERVISOR_TOKEN:
        return None, "SUPERVISOR_TOKEN not set (not running under Supervisor)"
    req = urllib.request.Request(f"{CORE_API}{path}", method=method)
    req.add_header("Authorization", f"Bearer {SUPERVISOR_TOKEN}")
    req.add_header("Content-Type", "application/json")
    data = json.dumps(payload).encode() if payload is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
            body = resp.read()
            return (json.loads(body) if body else None), None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}: {exc.read().decode('utf-8', 'ignore')[:200]}"
    except Exception as exc:  # noqa: BLE001 - a push failure must not kill the loop
        return None, str(exc)


def push_sensor(entity_id, state, attributes):
    return _ha_api(f"/states/{entity_id}", method="POST", payload={"state": state, "attributes": attributes})


def publish_sensors(conn, options):
    now_local = datetime.now(LOCAL_TZ)
    opts = get_price_options(options)
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    quarters_today = quarter_prices_with_total(conn, today_start, today_start + timedelta(days=1), opts["price_area"], opts)
    current = _current_price_row(quarters_today, now_local)
    if current:
        cheapest = min(quarters_today, key=lambda r: r["total_dkk_kwh"])
        priciest = max(quarters_today, key=lambda r: r["total_dkk_kwh"])
        tomorrow_rows = _price_rows(
            conn, today_start + timedelta(days=1), today_start + timedelta(days=2), opts["price_area"]
        )
        push_sensor(
            "sensor.electricity_tracker_price_now",
            current["total_dkk_kwh"],
            {
                "friendly_name": "Electricity price now",
                "icon": "mdi:transmission-tower",
                "unit_of_measurement": "DKK/kWh",
                "price_area": opts["price_area"],
                "spot_price_dkk_kwh": current["spot_dkk_kwh"],
                "grid_tariff_dkk_kwh": current["grid_tariff_dkk_kwh"],
                "transmission_tariff_dkk_kwh": current["transmission_tariff_dkk_kwh"],
                "electricity_tax_dkk_kwh": current["electricity_tax_dkk_kwh"],
                "vat_rate": current["vat_rate"],
                "cheapest_today_dkk_kwh": cheapest["total_dkk_kwh"],
                "cheapest_today_time": cheapest["time_dk"],
                "priciest_today_dkk_kwh": priciest["total_dkk_kwh"],
                "priciest_today_time": priciest["time_dk"],
                "tomorrow_published": bool(tomorrow_rows),
            },
        )

    saveeye_cfg = get_saveeye_config(options)
    saveeye_device_serial = resolve_saveeye_device_serial(conn, saveeye_cfg["device_serial"])

    cfg = get_eloverblik_config(options)
    if cfg["metering_point"]:
        summary = consumption_summary(
            conn, now_local, cfg["metering_point"], opts["price_area"], opts, saveeye_device_serial
        )
        state = summary["today_kwh"] if summary["today_kwh"] is not None else "unknown"
        push_sensor(
            "sensor.electricity_tracker_consumption_today",
            state,
            {
                "friendly_name": "Electricity consumption today",
                "icon": "mdi:lightning-bolt",
                "unit_of_measurement": "kWh",
                **summary,
            },
        )

    if saveeye_cfg["enabled"]:
        payload, received_at = get_saveeye_latest()
        if payload and payload.get("instant_power_w") is not None:
            push_sensor(
                "sensor.electricity_tracker_power_now",
                payload["instant_power_w"],
                {
                    "friendly_name": "Electricity power now",
                    "icon": "mdi:flash",
                    "unit_of_measurement": "W",
                    "device_class": "power",
                    "state_class": "measurement",
                    "source": "saveeye",
                    "device_serial": payload["device_serial"],
                    "received_at": received_at,
                },
            )

    easee_cfg = get_easee_config(options)
    if easee_cfg["enabled"]:
        charger_id = easee_cfg["charger_id"] or _easee_token_cache["charger_id"]
        if charger_id:
            session = easee_current_session(conn, opts, opts["price_area"], charger_id)
            if session:
                state = session["total_power_w"] if session["total_power_w"] is not None else "unknown"
                push_sensor(
                    "sensor.electricity_tracker_ev_power",
                    state,
                    {
                        "friendly_name": "EV charging power",
                        "icon": "mdi:ev-station",
                        "unit_of_measurement": "W",
                        "device_class": "power",
                        "state_class": "measurement",
                        "status": session["status"],
                        "charger_op_mode": session["raw_status"],
                        "charging": session["charging"],
                        "reason": session["reason"],
                        "session_energy_kwh": session["session_energy_kwh"],
                        "session_cost_dkk": session["session_cost_dkk"],
                        "session_started_at": session["session_started_at"],
                    },
                )


# --- Background loop ---


def _background_loop():
    if not SUPERVISOR_TOKEN:
        _log("SUPERVISOR_TOKEN not set; sensor push disabled (local/dev mode)")
    last_consumption_sync = 0.0
    while True:
        try:
            conn = _db_connect_standalone()
            try:
                options = _read_options()
                sync_prices(conn, options)
                now = time.time()
                if now - last_consumption_sync >= CONSUMPTION_SYNC_INTERVAL_SECONDS:
                    sync_consumption(conn, options)
                    last_consumption_sync = now
                _persist_saveeye_sample(conn)
                sync_easee(conn, options)
                sync_easee_cloud_sessions(conn, options)
                if SUPERVISOR_TOKEN:
                    publish_sensors(conn, options)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - keep the loop alive across any single failure
            app.logger.exception("background loop iteration failed")
        time.sleep(BACKGROUND_TICK_SECONDS)


# --- Routes: pages ---


@app.route("/")
def index():
    return render_template("index.html", app_version=APP_VERSION)


# --- Routes: API ---


@app.route("/api/summary")
def api_summary():
    db = get_db()
    options = _read_options()
    opts = get_price_options(options)
    now_local = datetime.now(LOCAL_TZ)
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)

    today = quarter_prices_with_total(db, today_start, tomorrow_start, opts["price_area"], opts)
    tomorrow = quarter_prices_with_total(db, tomorrow_start, tomorrow_start + timedelta(days=1), opts["price_area"], opts)
    current = _current_price_row(today, now_local)

    hourly_today = _hourly_totals(today)
    cheapest_today = min(hourly_today.items(), key=lambda kv: kv[1], default=(None, None))
    priciest_today = max(hourly_today.items(), key=lambda kv: kv[1], default=(None, None))

    cfg = get_eloverblik_config(options)
    saveeye_cfg = get_saveeye_config(options)
    saveeye_device_serial = resolve_saveeye_device_serial(db, saveeye_cfg["device_serial"])
    consumption = None
    if cfg["metering_point"]:
        consumption = consumption_summary(
            db, now_local, cfg["metering_point"], opts["price_area"], opts, saveeye_device_serial
        )

    saveeye_now = None
    if saveeye_cfg["enabled"]:
        payload, received_at = get_saveeye_latest()
        saveeye_now = {"payload": payload, "received_at": received_at, **get_saveeye_status()}

    easee_cfg = get_easee_config(options)
    easee_now = None
    if easee_cfg["enabled"]:
        charger_id = easee_cfg["charger_id"] or _easee_token_cache["charger_id"]
        easee_now = {
            "charger_id": charger_id,
            "session": easee_current_session(db, opts, opts["price_area"], charger_id) if charger_id else None,
            # Without this the page has no way to tell a 30-second-old status
            # from a three-day-old one, and a failed sync writes no row at all.
            "last_sync": _get_app_state(db, "last_easee_sync"),
        }

    return jsonify(
        {
            "app_version": APP_VERSION,
            "price_area": opts["price_area"],
            "now_local": now_local.isoformat(timespec="seconds"),
            "current_price": current,
            "today": today,
            "tomorrow": tomorrow or None,
            "cheapest_hour_today": {"time_dk": cheapest_today[0], "total_dkk_kwh": round(cheapest_today[1], 4)}
            if cheapest_today[0]
            else None,
            "priciest_hour_today": {"time_dk": priciest_today[0], "total_dkk_kwh": round(priciest_today[1], 4)}
            if priciest_today[0]
            else None,
            "consumption": consumption,
            "eloverblik_configured": bool(cfg["refresh_token"] and cfg["metering_point"]),
            "saveeye": saveeye_now,
            "easee": easee_now,
            "price_config_warning": price_config_warning(opts),
            "last_price_sync": _get_app_state(db, "last_price_sync"),
            "last_consumption_sync": _get_app_state(db, "last_consumption_sync"),
        }
    )


@app.route("/api/prices")
def api_prices():
    db = get_db()
    options = _read_options()
    opts = get_price_options(options)
    days = request.args.get("days", default=2, type=int) or 2
    days = min(14, max(1, days))
    now_local = datetime.now(LOCAL_TZ)
    start = now_local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    end = now_local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=2)
    return jsonify(quarter_prices_with_total(db, start, end, opts["price_area"], opts))


@app.route("/api/consumption")
def api_consumption():
    db = get_db()
    options = _read_options()
    opts = get_price_options(options)
    cfg = get_eloverblik_config(options)
    if not cfg["metering_point"]:
        return jsonify([])
    saveeye_cfg = get_saveeye_config(options)
    saveeye_device_serial = resolve_saveeye_device_serial(db, saveeye_cfg["device_serial"])
    days = request.args.get("days", default=14, type=int) or 14
    days = min(90, max(1, days))
    now_local = datetime.now(LOCAL_TZ)
    start = now_local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    end = now_local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return jsonify(
        combined_consumption_with_cost(
            db, start, end, cfg["metering_point"], opts["price_area"], opts, saveeye_device_serial
        )
    )


@app.route("/api/saveeye/now")
def api_saveeye_now():
    """Live instant power/energy reading plus MQTT connection status — the
    Settings panel's "test" surface for Saveeye, the same role
    /api/eloverblik/diagnose plays for Eloverblik."""
    options = _read_options()
    cfg = get_saveeye_config(options)
    if not cfg["enabled"]:
        return jsonify({"enabled": False})
    payload, received_at = get_saveeye_latest()
    return jsonify({"enabled": True, "payload": payload, "received_at": received_at, **get_saveeye_status()})


@app.route("/api/easee/now")
def api_easee_now():
    """The current (or most recent) charging session's live state and cost
    so far, from the last poll — see easee_current_session for how that's
    derived."""
    db = get_db()
    options = _read_options()
    cfg = get_easee_config(options)
    if not cfg["enabled"]:
        return jsonify({"enabled": False})
    opts = get_price_options(options)
    charger_id = cfg["charger_id"] or _easee_token_cache["charger_id"]
    if not charger_id:
        return jsonify({"enabled": True, "charger_id": None, "session": None})
    session = correct_current_session(
        db, opts, opts["price_area"], charger_id,
        easee_current_session(db, opts, opts["price_area"], charger_id))
    return jsonify(
        {
            "enabled": True,
            "charger_id": charger_id,
            "session": session,
            "last_sync": _get_app_state(db, "last_easee_sync"),
        }
    )


@app.route("/api/insights")
def api_insights():
    """Everything the Insights tab shows. Derived on request from rows already
    collected — nothing here is stored or synced separately."""
    db = get_db()
    days = min(365, max(1, request.args.get("days", default=30, type=int) or 30))
    return jsonify(build_insights(db, _read_options(), days=days))


@app.route("/api/easee/history")
def api_easee_history():
    """Past charging sessions, their per-day totals, and the roll-up — the
    history card's whole payload in one request."""
    db = get_db()
    options = _read_options()
    opts = get_price_options(options)
    cfg = get_easee_config(options)
    if not cfg["enabled"]:
        return jsonify({"enabled": False, "sessions": [], "daily": [],
                        "monthly": {"months": [], "average": None}, "totals": None})
    charger_id = cfg["charger_id"] or _easee_token_cache["charger_id"]
    if not charger_id:
        return jsonify({"enabled": True, "charger_id": None, "sessions": [], "daily": [],
                        "monthly": {"months": [], "average": None}, "totals": None})

    days = min(365, max(1, request.args.get("days", default=30, type=int) or 30))
    sessions = easee_sessions_reconciled(db, opts, opts["price_area"], charger_id, days=days)
    return jsonify(
        {
            "enabled": True,
            "charger_id": charger_id,
            "days": days,
            "sessions": sessions,
            "daily": easee_daily_charging(sessions),
            "monthly": easee_monthly_charging(sessions),
            "totals": easee_charging_totals(sessions),
            "trip_days": trip_days(_read_trips(db)),
        }
    )


def _read_trips(db):
    return [dict(row) for row in db.execute(
        "SELECT * FROM ev_trips ORDER BY started_on DESC, id DESC")]


@app.route("/api/trips")
def api_trips():
    """Long trips, each with the charging that happened during it."""
    db = get_db()
    options = _read_options()
    opts = get_price_options(options)
    cfg = get_easee_config(options)
    charger_id = cfg["charger_id"] or _easee_token_cache["charger_id"]

    sessions = []
    if cfg["enabled"] and charger_id:
        days = min(730, max(1, request.args.get("days", default=365, type=int) or 365))
        sessions = easee_sessions_reconciled(db, opts, opts["price_area"],
                                             charger_id, days=days)
    return jsonify({"trips": trips_with_charging(_read_trips(db), sessions)})


@app.route("/api/trips", methods=["POST"])
def api_add_trip():
    data = request.get_json(force=True, silent=True) or {}
    label = (data.get("label") or "").strip()
    if not label:
        return jsonify({"error": "a trip needs a label"}), 400

    started = _parse_day(data.get("started_on"))
    if started is None:
        return jsonify({"error": "started_on must be a date, YYYY-MM-DD"}), 400
    # A one-day trip is the common case, so the end defaults to the start
    # rather than being a second thing to fill in every time.
    ended = _parse_day(data.get("ended_on")) or started
    if ended < started:
        return jsonify({"error": "the trip cannot end before it starts"}), 400

    distance = data.get("distance_km")
    if distance not in (None, ""):
        try:
            distance = float(distance)
        except (TypeError, ValueError):
            return jsonify({"error": "distance_km must be a number"}), 400
        if distance <= 0:
            return jsonify({"error": "distance_km must be positive"}), 400
    else:
        distance = None

    db = get_db()
    db.execute(
        "INSERT INTO ev_trips (started_on, ended_on, label, distance_km, notes, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (started.isoformat(), ended.isoformat(), label[:120], distance,
         (data.get("notes") or "").strip()[:500] or None, _now_iso()),
    )
    db.commit()
    return jsonify({"trips": trips_with_charging(_read_trips(db), [])}), 201


@app.route("/api/trips/<int:trip_id>", methods=["DELETE"])
def api_delete_trip(trip_id):
    db = get_db()
    if db.execute("SELECT id FROM ev_trips WHERE id = ?", (trip_id,)).fetchone() is None:
        return jsonify({"error": "no such trip"}), 404
    db.execute("DELETE FROM ev_trips WHERE id = ?", (trip_id,))
    db.commit()
    return jsonify({"deleted": trip_id})


@app.route("/api/easee/diagnose")
def api_easee_diagnose():
    """A live round-trip to Easee: logs in with the configured account and
    lists every charger it can see, so the right charger id can be copied
    into easee_charger_id without guessing — same role
    /api/eloverblik/diagnose plays for Eloverblik."""
    options = _read_options()
    cfg = get_easee_config(options)
    if not cfg["username"] or not cfg["password"]:
        return jsonify({"ok": False, "error": "easee_username/easee_password not set"}), 400
    try:
        access_token = _get_easee_access_token(cfg["username"], cfg["password"])
        chargers = easee.get_chargers(access_token)
    except easee.EaseeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    return jsonify({"ok": True, "chargers": chargers, "configured_charger_id": cfg["charger_id"]})


@app.route("/api/eloverblik/diagnose")
def api_eloverblik_diagnose():
    """A live round-trip to Eloverblik: exchanges the configured refresh token
    and lists every metering point it can see, so the right GSRN id can be
    copied into eloverblik_metering_point without guessing."""
    options = _read_options()
    cfg = get_eloverblik_config(options)
    if not cfg["refresh_token"]:
        return jsonify({"ok": False, "error": "eloverblik_refresh_token is not set"}), 400
    try:
        token = _get_eloverblik_access_token(cfg["refresh_token"])
        points = eloverblik.list_metering_points(token, include_all=True)
    except eloverblik.EloverblikError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    return jsonify(
        {
            "ok": True,
            "metering_points": points,
            "configured_metering_point": cfg["metering_point"] or None,
        }
    )


@app.route("/api/health")
def api_health():
    try:
        get_db().execute("SELECT 1")
    except sqlite3.Error as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    return jsonify({"ok": True, "version": APP_VERSION})


TRACKED_TABLES = ("prices", "consumption", "saveeye_samples", "easee_samples",
                  "easee_cloud_sessions", "ev_trips")


@app.route("/api/stats")
def api_stats():
    db = get_db()

    def count(table):
        try:
            return db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        except sqlite3.Error:
            return None

    counts = {table: count(table) for table in TRACKED_TABLES}
    try:
        size = os.path.getsize(DB_PATH)
    except OSError:
        size = None
    return jsonify(
        {
            "app_version": APP_VERSION,
            "total": sum(v for v in counts.values() if v),
            "counts": counts,
            "db_bytes": size,
        }
    )


@app.route("/api/backup")
def api_backup():
    """The whole database as a file, for keeping or moving to another install.

    Copied through SQLite's own backup API rather than sent straight off disk:
    the background sync writes on its own connection, so streaming the file
    could hand out a snapshot taken mid-write.
    """
    db = get_db()
    db.commit()
    filename = f"electricity-tracker-backup-{datetime.now(LOCAL_TZ).strftime('%Y%m%d-%H%M%S')}.db"

    # A unique path per request, deleted before the response is built rather
    # than through response.call_on_close — that callback does not reliably
    # fire, which leaves a full copy of the database beside it after every
    # download. Reading it into memory first is a brief spike on a file this
    # size, and it is the only way the temporary copy is certain to go away.
    handle, snapshot = tempfile.mkstemp(prefix="electricity-backup-", suffix=".db")
    os.close(handle)
    try:
        target = sqlite3.connect(snapshot)
        try:
            db.backup(target)
        finally:
            target.close()
        with open(snapshot, "rb") as source:
            data = source.read()
    finally:
        if os.path.exists(snapshot):
            os.remove(snapshot)

    return send_file(
        io.BytesIO(data), as_attachment=True, download_name=filename,
        mimetype="application/vnd.sqlite3",
    )


def _is_valid_backup(path):
    """Whether this file is one of *this* add-on's databases.

    Checked before anything is replaced: restoring a Goal Tracker backup here
    would swap a working database for one with none of the right tables, and the
    add-on would come back up empty with no way back.
    """
    try:
        conn = sqlite3.connect(path)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        conn.close()
    except sqlite3.Error:
        return False
    return {"prices", "consumption"}.issubset(tables)


@app.route("/api/restore", methods=["POST"])
def api_restore():
    """Replace the database with an uploaded backup.

    Validated before the swap and written to a temporary path first, so a
    truncated upload or somebody else's backup cannot leave this add-on without
    a database at all.
    """
    uploaded = request.files.get("file")
    if uploaded is None or uploaded.filename == "":
        return jsonify({"error": "no file provided"}), 400
    tmp_path = DB_PATH + ".upload"
    uploaded.save(tmp_path)
    if not _is_valid_backup(tmp_path):
        os.remove(tmp_path)
        return jsonify({"error": "not a valid Electricity Tracker backup file"}), 400
    close_db()
    os.replace(tmp_path, DB_PATH)
    init_db()  # backfill any columns added since the backup was taken
    return jsonify({"status": "restored"}), 200


@app.route("/api/export")
def api_export():
    db = get_db()

    def rows(table):
        return [dict(r) for r in db.execute(f"SELECT * FROM {table}")]

    return jsonify(
        {
            "app_version": APP_VERSION,
            "taken_at": _now_iso(),
            "tables": {table: rows(table) for table in TRACKED_TABLES},
        }
    )


# --- Shutdown + entrypoint ---


def _handle_shutdown_signal(signum, frame):
    _log(f"received signal {signum}, shutting down")
    sys.exit(0)


if __name__ == "__main__":
    from waitress import serve

    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    init_db()
    _log(f"starting Electricity Tracker {APP_VERSION}")
    start_saveeye_client(_read_options())
    threading.Thread(target=_background_loop, daemon=True).start()
    port = int(os.environ.get("ELECTRICITY_PORT", "8099"))
    _log(f"serving on 0.0.0.0:{port} (waitress)")
    serve(app, host="0.0.0.0", port=port)
