import hmac
import html
import json
import os
import signal
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Flask, Response, g, jsonify, render_template, request

import energidataservice
import eloverblik
import saveeye
import easee

APP_VERSION = "1.4.2"  # keep in sync with the "version" field in config.yaml

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
        "electricity_tax": f("electricity_tax", 0.008),
        "vat_rate": f("vat_rate", 0.25),
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
            status TEXT,
            session_energy_kwh REAL,       -- resets at the start of each charging session
            total_power_w REAL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (ts_utc, charger_id)
        )
        """
    )
    conn.commit()
    conn.close()


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
    subtotal = spot_dkk_kwh + grid + opts["transmission_tariff"] + opts["electricity_tax"]
    total = subtotal * (1 + opts["vat_rate"])
    components = {
        "spot_dkk_kwh": round(spot_dkk_kwh, 4),
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
    """
    rows = consumption_with_cost(
        conn, start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc), metering_point, price_area, opts
    )
    for row in rows:
        row["source"] = "eloverblik"

    if not saveeye_device_serial:
        return rows

    covered = {row["time_dk"] for row in rows}
    estimates = saveeye_hourly_kwh(conn, start_local, end_local, saveeye_device_serial)
    hourly_totals = _hourly_totals(quarter_prices_with_total(conn, start_local, end_local, price_area, opts))

    for hour_key, kwh in estimates.items():
        if hour_key in covered:
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
                }
            )

    rows.sort(key=lambda r: r["time_dk"])
    return rows


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

    return {
        "today_kwh": today_kwh,
        "today_cost_dkk": today_cost,
        "yesterday_kwh": yesterday_kwh,
        "yesterday_cost_dkk": yesterday_cost,
        "week_kwh": week_kwh,
        "week_cost_dkk": week_cost,
        "month_kwh": month_kwh,
        "month_cost_dkk": month_cost,
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

    out = {}
    hour = start_local.replace(minute=0, second=0, microsecond=0)
    while hour < end_local:
        boundary_start = hour.astimezone(timezone.utc).timestamp()
        boundary_end = (hour + timedelta(hours=1)).astimezone(timezone.utc).timestamp()
        v_start = _interp_series(samples, boundary_start)
        v_end = _interp_series(samples, boundary_end)
        if v_start is not None and v_end is not None and v_end >= v_start:
            out[hour.replace(tzinfo=None).isoformat()] = round((v_end - v_start) / 1000.0, 4)
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
    usable = [r["cumulative_wh"] for r in rows if r["cumulative_wh"] is not None]
    if len(usable) < 2:
        return None
    kwh = (usable[-1] - usable[0]) / 1000.0
    if kwh < 0:
        return None  # a session/meter reset happened mid-hour — bail rather than show a negative
    return {"kwh": round(kwh, 4), "partial": True}


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
        "(ts_utc, charger_id, status, session_energy_kwh, total_power_w, fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
        (ts, charger_id, state["status"], state["session_energy_kwh"], state["total_power_w"], ts),
    )
    _set_app_state(conn, "last_easee_sync", ts)
    conn.commit()
    _log(f"Easee sync: {state['status']}, {state['session_energy_kwh']} kWh this session")


def easee_current_session(conn, opts, price_area, charger_id):
    """The most recent (or ongoing) charging session's energy and cost so
    far, from Easee's own `sessionEnergy` field — which the charger itself
    resets at the start of each session, so a *decrease* between two
    consecutive polls marks a session boundary. The "session" here is
    therefore the longest run of recent samples where it never decreases.

    Cost is attributed by multiplying each poll-to-poll energy delta by the
    price at the later sample's hour — the same mechanism a spot meter
    reading would use, just applied to one appliance's share of the total
    rather than the whole house.
    """
    rows = conn.execute(
        "SELECT ts_utc, session_energy_kwh, total_power_w, status FROM easee_samples "
        "WHERE charger_id = ? ORDER BY ts_utc DESC LIMIT 500",
        (charger_id,),
    ).fetchall()
    if not rows:
        return None
    rows = list(reversed(rows))  # chronological

    session_start_idx = len(rows) - 1
    for i in range(len(rows) - 1, 0, -1):
        prev, cur = rows[i - 1], rows[i]
        if prev["session_energy_kwh"] is None or cur["session_energy_kwh"] is None:
            break
        if cur["session_energy_kwh"] < prev["session_energy_kwh"] - 0.01:
            break  # a real decrease: a new session started here
        session_start_idx = i - 1
    session_rows = rows[session_start_idx:]
    latest = session_rows[-1]

    if len(session_rows) < 2:
        return {
            "status": latest["status"],
            "session_energy_kwh": latest["session_energy_kwh"],
            "total_power_w": latest["total_power_w"],
            "session_cost_dkk": None,
            "session_started_at": latest["ts_utc"],
        }

    start_local = datetime.fromisoformat(session_rows[0]["ts_utc"]).astimezone(LOCAL_TZ).replace(
        minute=0, second=0, microsecond=0
    )
    end_local = datetime.fromisoformat(session_rows[-1]["ts_utc"]).astimezone(LOCAL_TZ).replace(
        minute=0, second=0, microsecond=0
    ) + timedelta(hours=1)
    hourly_totals = _hourly_totals(quarter_prices_with_total(conn, start_local, end_local, price_area, opts))

    cost = 0.0
    cost_known = True
    for prev, cur in zip(session_rows, session_rows[1:]):
        if prev["session_energy_kwh"] is None or cur["session_energy_kwh"] is None:
            continue
        delta = cur["session_energy_kwh"] - prev["session_energy_kwh"]
        if delta <= 0:
            continue
        hour_key = (
            datetime.fromisoformat(cur["ts_utc"]).astimezone(LOCAL_TZ).replace(
                minute=0, second=0, microsecond=0, tzinfo=None
            ).isoformat()
        )
        price = hourly_totals.get(hour_key)
        if price is None:
            cost_known = False
            continue
        cost += delta * price

    return {
        "status": latest["status"],
        "session_energy_kwh": latest["session_energy_kwh"],
        "total_power_w": latest["total_power_w"],
        "session_cost_dkk": round(cost, 2) if cost_known else None,
        "session_started_at": session_rows[0]["ts_utc"],
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
    session = easee_current_session(db, opts, opts["price_area"], charger_id)
    return jsonify(
        {
            "enabled": True,
            "charger_id": charger_id,
            "session": session,
            "last_sync": _get_app_state(db, "last_easee_sync"),
        }
    )


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


TRACKED_TABLES = ("prices", "consumption", "saveeye_samples", "easee_samples")


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
