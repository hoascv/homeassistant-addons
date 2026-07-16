import json
import os
import platform
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, time as dtime, timedelta

import flask
from flask import Flask, g, jsonify, render_template, request, send_file

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
    for column, coltype in (("price", "REAL"), ("cost", "REAL"), ("category", "TEXT")):
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE logs ADD COLUMN {column} {coltype}")

    conn.commit()
    conn.close()


def _db_connect_standalone():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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


def _reminder_tick(now, conn):
    global _reminder_last_checked_date
    cfg = get_reminder_config()
    if not (cfg["enabled"] and cfg["notify_service"]):
        return
    target = _parse_hhmm(cfg["check_time"])
    if target is None or now.time() < target:
        return
    if _reminder_last_checked_date == now.date():
        return  # already evaluated today

    _reminder_last_checked_date = now.date()

    last_ts = _last_egg_collection(conn)
    overdue = last_ts is None or (now - last_ts) >= timedelta(days=cfg["threshold_days"])
    if overdue:
        send_notification(
            f"No eggs collected in {cfg['threshold_days']}+ days — check the coop!",
            title="Coop Tracker reminder",
        )


def _background_loop():
    if not SUPERVISOR_TOKEN:
        app.logger.info("SUPERVISOR_TOKEN not set; reminder disabled (local/dev mode)")
        return
    while True:
        try:
            conn = _db_connect_standalone()
            try:
                _reminder_tick(datetime.now(), conn)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - keep the loop alive across any single failure
            app.logger.exception("reminder loop iteration failed")
        time.sleep(60)


@app.route("/")
def index():
    currency = get_currency()
    return render_template(
        "index.html",
        currency_symbol=currency["symbol"],
        currency_position=currency["position"],
        currency_decimals=currency["decimals"],
    )


def _month_bounds(year, month):
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)
    return start, end


@app.route("/api/summary")
def api_summary():
    db = get_db()
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())

    eggs_today = db.execute(
        "SELECT COALESCE(SUM(count), 0) AS total FROM logs WHERE type = 'egg' AND ts >= ?",
        (today_start.isoformat(),),
    ).fetchone()["total"]

    eggs_week = db.execute(
        "SELECT COALESCE(SUM(count), 0) AS total FROM logs WHERE type = 'egg' AND ts >= ?",
        (week_start.isoformat(),),
    ).fetchone()["total"]

    last_cleaning = db.execute(
        "SELECT ts FROM logs WHERE type = 'cleaning' ORDER BY ts DESC LIMIT 1"
    ).fetchone()

    last_feeding = db.execute(
        "SELECT ts FROM logs WHERE type = 'feeding' ORDER BY ts DESC LIMIT 1"
    ).fetchone()

    month_param = request.args.get("month")
    try:
        year, month = (int(part) for part in month_param.split("-"))
    except (AttributeError, ValueError):
        year, month = now.year, now.month
    month_start, month_end = _month_bounds(year, month)

    eggs_collected_total = db.execute(
        "SELECT COALESCE(SUM(count), 0) AS total FROM logs WHERE type = 'egg'"
    ).fetchone()["total"]

    eggs_sold_total = db.execute(
        "SELECT COALESCE(SUM(count), 0) AS total FROM logs WHERE type = 'sale'"
    ).fetchone()["total"]

    eggs_used_total = db.execute(
        "SELECT COALESCE(SUM(count), 0) AS total FROM logs WHERE type = 'used'"
    ).fetchone()["total"]

    revenue_month = db.execute(
        "SELECT COALESCE(SUM(price), 0) AS total FROM logs WHERE type = 'sale' AND ts >= ? AND ts < ?",
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchone()["total"]

    cost_month = db.execute(
        "SELECT COALESCE(SUM(cost), 0) AS total FROM logs WHERE type = 'expense' AND ts >= ? AND ts < ?",
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchone()["total"]

    return jsonify(
        {
            "eggs_today": eggs_today,
            "eggs_week": eggs_week,
            "last_cleaning": last_cleaning["ts"] if last_cleaning else None,
            "last_feeding": last_feeding["ts"] if last_feeding else None,
            "eggs_available": eggs_collected_total - eggs_sold_total - eggs_used_total,
            "month": f"{year:04d}-{month:02d}",
            "revenue_month": revenue_month,
            "cost_month": cost_month,
            "net_month": revenue_month - cost_month,
        }
    )


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
        INSERT INTO logs (type, ts, count, food_type, amount, notes, price, cost, category)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (entry_type, ts, count, food_type, amount, notes, price, cost, category),
    )
    db.commit()

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

    db.execute(
        """
        UPDATE logs
        SET ts = ?, count = ?, food_type = ?, amount = ?, notes = ?, price = ?, cost = ?, category = ?
        WHERE id = ?
        """,
        (ts, count, food_type, amount, notes, price, cost, category, entry_id),
    )
    db.commit()

    return jsonify({"id": entry_id, "ts": ts}), 200


@app.route("/api/entries/<int:entry_id>", methods=["DELETE"])
def api_delete_entry(entry_id):
    db = get_db()
    db.execute("DELETE FROM logs WHERE id = ?", (entry_id,))
    db.commit()
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
                _reminder_last_checked_date.isoformat() if _reminder_last_checked_date else None
            ),
            "python_version": sys.version.split()[0],
            "flask_version": flask.__version__,
            "platform": platform.platform(),
        }
    )


@app.route("/api/backup")
def api_backup():
    db = get_db()
    db.commit()
    filename = f"coop-tracker-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    return send_file(DB_PATH, as_attachment=True, download_name=filename)


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
    init_db()
    _log_startup_debug_info()
    threading.Thread(target=_background_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8099)
