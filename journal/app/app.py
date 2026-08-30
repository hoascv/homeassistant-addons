"""Journal — an encrypted daily journal behind a master password.

Write the day into a handful of sections you choose, rate it, tag it, and note
what moved on the goals you are chasing. Open any past date and read it back.

What makes it different from the other add-ons here is what the database is
allowed to know. Every word is AES-256-GCM at rest under a key derived from a
master password that is never stored — see crypto.py — so the add-on itself
cannot read a single entry unless someone has just typed the password in. What
stays in the clear is the skeleton: which dates have an entry, which goals
exist. That is enough for a streak sensor and a nightly nudge, and nothing
else leaves.

Two consequences worth stating plainly, both of them deliberate:

- There is no password recovery. None. A forgotten password is a lost journal.
- The add-on is reachable only through Home Assistant's ingress. There is no
  direct port and no API token, because a token that returns decrypted entries
  would be a second key to the same lock.
"""
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
from datetime import date, datetime, time as dtime

from flask import Flask, Response, g, jsonify, render_template, request, send_file

import crypto
import store

APP_VERSION = "1.1.0"  # keep in sync with the "version" field in config.yaml

DB_PATH = os.environ.get("JOURNAL_DB_PATH", "/data/journal.db")
OPTIONS_PATH = os.environ.get("JOURNAL_OPTIONS_PATH", "/data/options.json")

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
CORE_API = "http://supervisor/core/api"

# The session token travels in a header the app sets on every request, kept in
# the tab's sessionStorage — not in a cookie. Ingress puts every add-on on one
# origin under different path prefixes, so a cookie scoped even slightly too
# wide would be handed to the neighbouring add-on on its way past; and a header
# the page has to set on purpose cannot be replayed by a cross-site form.
SESSION_HEADER = "X-Journal-Session"

# Sweeping idle sessions and rolling the day over need no better resolution.
BACKGROUND_TICK_SECONDS = 60

app = Flask(__name__)

SESSIONS = crypto.SessionStore()
UNLOCK_THROTTLE = crypto.Throttle()


def _log(msg):
    print(f"[Journal] {datetime.now().isoformat()} {msg}", flush=True)


def _today():
    """Local calendar date. Naive on purpose: Supervisor gives the container
    Home Assistant's timezone, and a journal's "today" is the day the person
    writing it is having."""
    return date.today()


def _read_options():
    try:
        with open(OPTIONS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def get_config(options=None):
    opts = _read_options() if options is None else options

    def _int(key, default, low, high):
        try:
            value = int(opts.get(key, default))
        except (TypeError, ValueError):
            return default
        return max(low, min(high, value))

    return {
        "auto_lock_minutes": _int("auto_lock_minutes", 60, 0, 1440),
        "goal_nudge_days": _int("goal_nudge_days", 7, 0, 90),
        "notify_service": (opts.get("notify_service") or "").strip(),
        "reminder_enabled": bool(opts.get("daily_reminder_enabled", False)),
        "reminder_time": (opts.get("daily_reminder_time") or "21:00").strip(),
    }


# --- Access control (Home Assistant's login, then the password) ---
#
# Ingress passes the authenticated Home Assistant user's ID. That is the outer
# door and the only thing that can narrow *who* may reach the add-on; the
# master password is the inner one and decides what they can read.
INGRESS_USER_ID_HEADER = "X-Remote-User-ID"


def get_allowed_user_ids():
    raw = _read_options().get("restrict_to_user_ids", "") or ""
    return {uid.strip() for uid in raw.replace("\n", ",").replace(" ", ",").split(",") if uid.strip()}


@app.before_request
def _enforce_access():
    # The idle timeout is a setting, so it has to be picked up as it changes
    # rather than at import.
    SESSIONS.set_ttl(get_config()["auto_lock_minutes"] * 60)

    user_id = request.headers.get(INGRESS_USER_ID_HEADER)
    if not user_id:
        return Response(
            json.dumps(
                {
                    "error": "unauthorized",
                    "detail": (
                        "This add-on is reachable only through Home Assistant's ingress "
                        "(the sidebar entry). It publishes no direct port."
                    ),
                }
            ),
            status=401,
            mimetype="application/json",
        )

    allowed = get_allowed_user_ids()
    if allowed and user_id not in allowed:
        return Response(_access_denied_html(user_id), status=403, mimetype="text/html")
    return None


def _access_denied_html(user_id):
    return (
        "<!doctype html><meta charset='utf-8'>"
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;"
        "padding:2rem;max-width:34rem;margin:0 auto;line-height:1.6}code{background:#eee;padding:.1rem .3rem}</style>"
        "<h2>Not your journal</h2>"
        "<p>This add-on is restricted to specific Home Assistant users.</p>"
        f"<p>Your user ID is <code>{user_id}</code>. To allow it, add it to "
        "<code>restrict_to_user_ids</code> in the add-on's Configuration tab.</p>"
    )


# --- Database ---


def _connect(path=None):
    return store.connect(path or DB_PATH)


def get_db():
    if "db" not in g:
        g.db = _connect()
    return g.db


@app.teardown_appcontext
def _close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# --- The unlocked session ---


def session_key():
    """The decryption key for this request, or None when locked."""
    return SESSIONS.key_for(request.headers.get(SESSION_HEADER))


def locked_response():
    return jsonify({"error": "locked", "detail": "Unlock the journal with the master password."}), 401


def unlocked(view):
    """Every route that touches a person's words goes through here. Being past
    the ingress door is not enough; the key has to be in memory."""

    def wrapper(*args, **kwargs):
        key = session_key()
        if key is None:
            return locked_response()
        return view(key, *args, **kwargs)

    wrapper.__name__ = view.__name__
    return wrapper


# --- Home Assistant ---


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


def send_notification(message, title="Journal"):
    service = get_config()["notify_service"]
    if not service:
        return False, "no notify service configured"
    _, err = _ha_api(f"/services/notify/{service}", method="POST", payload={"message": message, "title": title})
    return err is None, err


def get_notify_services():
    data, err = _ha_api("/services")
    if err or not data:
        return [], err
    for entry in data:
        if entry.get("domain") == "notify":
            return sorted(entry.get("services", {}).keys()), None
    return [], None


def publish_sensors(conn, today=None):
    """Counts and dates, never content.

    This runs from the background loop, which has no key and never will, so
    the sensor is incapable of carrying a word of the journal into Home
    Assistant's state machine — where it would be recorded, graphed, and
    included in every backup in the clear.
    """
    figures = store.stats(conn, today or _today())
    return push_sensor(
        "sensor.journal_streak",
        figures["streak"],
        {
            "friendly_name": "Journal streak",
            "unit_of_measurement": "days",
            "icon": "mdi:notebook-edit",
            "entries": figures["entries"],
            "longest_streak": figures["longest_streak"],
            "last_entry_on": figures["last_entry_on"],
            "written_today": figures["has_entry_today"],
            "goals_active": figures["goals_active"],
            "goals_done": figures["goals_done"],
            "unlocked_sessions": SESSIONS.count(),
        },
    )


def _parse_hhmm(value):
    try:
        hh, mm = str(value).split(":")
        return dtime(int(hh), int(mm))
    except (ValueError, AttributeError):
        return dtime(21, 0)


def maybe_send_reminder(conn, now=None):
    """One nudge a day, if the day is still unwritten. The message says only
    that — the loop could not quote an entry if it wanted to."""
    cfg = get_config()
    if not cfg["reminder_enabled"] or not cfg["notify_service"]:
        return False
    now = now or datetime.now()
    today_iso = now.date().isoformat()
    if store.get_app_state(conn, "reminder_last_sent") == today_iso:
        return False
    if now.time() < _parse_hhmm(cfg["reminder_time"]):
        return False
    figures = store.stats(conn, now.date())
    if figures["has_entry_today"]:
        return False

    message = "Nothing written today yet."
    if figures["streak"]:
        message += f" You are on a {figures['streak']}-day streak."
    send_notification(message)
    store.set_app_state(conn, "reminder_last_sent", today_iso)
    conn.commit()
    return True


def _background_loop():
    if not SUPERVISOR_TOKEN:
        _log("SUPERVISOR_TOKEN not set; sensor push and reminders disabled (local/dev mode)")
    while True:
        try:
            # Dropping idle keys is the whole point of an auto-lock, so it
            # cannot wait for someone to make a request that notices.
            SESSIONS.sweep()
            conn = _connect()
            try:
                if SUPERVISOR_TOKEN:
                    maybe_send_reminder(conn)
                    publish_sensors(conn)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - keep the loop alive across any single failure
            app.logger.exception("background loop iteration failed")
        time.sleep(BACKGROUND_TICK_SECONDS)


# --- Routes: page ---


@app.route("/")
def index():
    return render_template("index.html", app_version=APP_VERSION)


# --- Routes: the lock ---


@app.route("/api/state")
def api_state():
    """What the page needs before anything is unlocked: whether a password has
    been set, whether this session still holds a key, and the counts that are
    not secret."""
    conn = get_db()
    cfg = get_config()
    figures = store.stats(conn, _today())
    return jsonify(
        {
            "app_version": APP_VERSION,
            "vault_exists": store.vault_exists(conn),
            "unlocked": session_key() is not None,
            "today": _today().isoformat(),
            "auto_lock_minutes": cfg["auto_lock_minutes"],
            "goal_nudge_days": cfg["goal_nudge_days"],
            "stats": figures,
        }
    )


@app.route("/api/vault", methods=["POST"])
def api_create_vault():
    body = request.get_json(silent=True) or {}
    conn = get_db()
    try:
        key = store.create_vault(conn, body.get("password") or "")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    _log("master password set; vault created")
    return jsonify({"token": SESSIONS.open(key), "created": True})


@app.route("/api/unlock", methods=["POST"])
def api_unlock():
    body = request.get_json(silent=True) or {}
    conn = get_db()
    wait = UNLOCK_THROTTLE.seconds_remaining()
    if wait > 0:
        return jsonify({"error": "too many attempts", "retry_after": int(wait) + 1}), 429
    try:
        key = store.unlock_key(conn, body.get("password") or "")
    except crypto.WrongPassword:
        failures = UNLOCK_THROTTLE.record_failure()
        _log(f"failed unlock attempt ({failures} in a row)")
        return jsonify({"error": "wrong master password", "retry_after": int(UNLOCK_THROTTLE.seconds_remaining())}), 401
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    UNLOCK_THROTTLE.record_success()
    return jsonify({"token": SESSIONS.open(key)})


@app.route("/api/lock", methods=["POST"])
def api_lock():
    """Locks the journal everywhere, not just in this tab. Someone reaching
    for a padlock means the journal, not the window."""
    SESSIONS.close_all()
    return jsonify({"locked": True})


@app.route("/api/password", methods=["POST"])
@unlocked
def api_change_password(_key):
    body = request.get_json(silent=True) or {}
    conn = get_db()
    try:
        new_key = store.change_password(conn, body.get("old_password") or "", body.get("new_password") or "")
    except crypto.WrongPassword:
        return jsonify({"error": "the current password is wrong"}), 401
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    SESSIONS.rekey(new_key)
    _log("master password changed; journal re-encrypted")
    return jsonify({"changed": True})


# --- Routes: the day ---


@app.route("/api/entry")
@unlocked
def api_get_entry(key):
    conn = get_db()
    try:
        day = store.normalise_day(request.args.get("day") or _today())
    except ValueError:
        return jsonify({"error": "day must be a date, as YYYY-MM-DD"}), 400
    entry = store.get_entry(conn, key, day)
    return jsonify(
        {
            "day": day,
            "entry": entry,
            "sections": store.get_sections(conn, key),
            "goals": store.goals_with_activity(conn, key, _today(), get_config()["goal_nudge_days"]),
            "on_this_day": store.on_this_day(conn, key, day),
            "neighbours": _neighbours(conn, day),
        }
    )


def _neighbours(conn, day):
    """The nearest written days either side, so the arrows can skip an empty
    fortnight instead of walking it a click at a time."""
    previous = conn.execute("SELECT MAX(day) AS d FROM entries WHERE day < ?", (day,)).fetchone()["d"]
    following = conn.execute("SELECT MIN(day) AS d FROM entries WHERE day > ?", (day,)).fetchone()["d"]
    return {"previous_written": previous, "next_written": following}


@app.route("/api/entry", methods=["PUT"])
@unlocked
def api_save_entry(key):
    body = request.get_json(silent=True) or {}
    conn = get_db()
    try:
        day = store.normalise_day(body.get("day") or _today())
    except ValueError:
        return jsonify({"error": "day must be a date, as YYYY-MM-DD"}), 400
    saved = store.save_entry(conn, key, day, body)
    return jsonify({"day": day, "entry": saved, "saved_at": store.now_iso(), "deleted": saved is None})


@app.route("/api/calendar")
@unlocked
def api_calendar(key):
    conn = get_db()
    try:
        start = store.normalise_day(request.args.get("start") or _today())
        end = store.normalise_day(request.args.get("end") or _today())
    except ValueError:
        return jsonify({"error": "start and end must be dates, as YYYY-MM-DD"}), 400
    return jsonify({"days": store.calendar(conn, key, start, end)})


@app.route("/api/search")
@unlocked
def api_search(key):
    query = request.args.get("q", "")
    return jsonify({"query": query, "results": store.search(get_db(), key, query)})


# --- Routes: sections ---


@app.route("/api/sections", methods=["GET", "PUT"])
@unlocked
def api_sections(key):
    conn = get_db()
    if request.method == "PUT":
        body = request.get_json(silent=True) or {}
        try:
            sections = store.save_sections(conn, key, body.get("sections"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"sections": sections})
    return jsonify({"sections": store.get_sections(conn, key)})


# --- Routes: goals ---


@app.route("/api/goals", methods=["GET", "POST"])
@unlocked
def api_goals(key):
    conn = get_db()
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        try:
            goal_id = store.create_goal(conn, key, body.get("title"), body.get("why", ""), body.get("target_date"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"id": goal_id}), 201
    return jsonify({"goals": store.goals_with_activity(conn, key, _today(), get_config()["goal_nudge_days"])})


@app.route("/api/goals/<goal_id>", methods=["PATCH", "DELETE"])
@unlocked
def api_goal(key, goal_id):
    conn = get_db()
    if request.method == "DELETE":
        store.delete_goal(conn, goal_id)
        return jsonify({"deleted": True})
    body = request.get_json(silent=True) or {}
    try:
        store.update_goal(
            conn,
            key,
            goal_id,
            title=body.get("title"),
            why=body.get("why"),
            target_date=body.get("target_date"),
            status=body.get("status"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"id": goal_id})


@app.route("/api/goals/<goal_id>/timeline")
@unlocked
def api_goal_timeline(key, goal_id):
    return jsonify({"id": goal_id, "timeline": store.goal_timeline(get_db(), key, goal_id)})


# --- Routes: the rest ---


@app.route("/api/backup")
@unlocked
def api_backup(key):  # noqa: ARG001 - @unlocked passes the key; the file is already ciphertext
    """The whole database as a file, for keeping or moving to another install.

    Unlike every other add-on here, this backup is *already unreadable*: what it
    contains is the same AES-256-GCM ciphertext that sits on disk, and the same
    master password opens it or nothing does. That is what makes it the right
    thing to hand out rather than the plain-text export next to it.

    It is still behind `@unlocked`. The file is safe on its own, but requiring
    the password to obtain it means an ingress-admin who does not know it cannot
    walk off with the ciphertext and attack it offline at leisure — which is
    exactly the threat the encryption exists for. Somebody who can unlock can
    already read the journal, so this costs them nothing.

    Copied through SQLite's own backup API rather than streamed off disk: the
    background loop writes on its own connection, so sending the file directly
    could hand out a snapshot taken mid-write.
    """
    db = get_db()
    db.commit()
    filename = f"journal-backup-{_today().isoformat()}.db"

    # A unique path per request, read into memory and deleted before the
    # response is built rather than through response.call_on_close — that
    # callback does not reliably fire, and what it would leave behind here is a
    # complete copy of somebody's journal sitting in the temp directory.
    handle, snapshot = tempfile.mkstemp(prefix="journal-backup-", suffix=".db")
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

    Checked before anything is replaced. Restoring another add-on's backup here
    would swap a working journal for a database with none of the right tables,
    and there is no undo: the file it overwrote was the only copy on the
    machine. `vault` is the table that makes it a journal rather than merely
    SQLite — without it there is nothing a password could ever open.
    """
    try:
        conn = sqlite3.connect(path)
        try:
            tables = {
                row[0] for row in
                conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    return {"vault", "entries", "goals"}.issubset(tables)


@app.route("/api/restore", methods=["POST"])
def api_restore():
    """Replace the database with an uploaded backup.

    The lock rule here is deliberately not a plain `@unlocked`, because that
    would make the main use for this impossible. A fresh install has no vault at
    all, so nothing can ever unlock it — and "move my journal to a new machine"
    is precisely the case where the journal being replaced does not exist yet.

    So: an existing vault must be unlocked first. Restoring destroys writing
    that cannot be recovered, and the password is the proof that it is yours to
    destroy. An empty vault has nothing to protect and lets the file in.

    Note the restored journal comes with its *own* password — the one that was
    set when the backup was taken, which is not necessarily the one just used to
    unlock. Every open session is dropped for that reason: the keys held in
    memory belong to a vault that no longer exists.
    """
    conn = get_db()
    if store.vault_exists(conn) and session_key() is None:
        return locked_response()

    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "no file provided"}), 400

    tmp_path = DB_PATH + ".upload"
    uploaded.save(tmp_path)
    if not _is_valid_backup(tmp_path):
        os.remove(tmp_path)
        return jsonify({
            "error": "not a valid Journal backup file",
            "detail": "This file is not a Journal database. Nothing was changed.",
        }), 400

    # Close this request's handle before the swap; a connection left open on the
    # replaced file would keep writing to a database nobody can reach any more.
    db = g.pop("db", None)
    if db is not None:
        db.close()
    os.replace(tmp_path, DB_PATH)
    # Backfill anything added to the schema since the backup was taken, so an
    # older file comes back usable rather than missing a column.
    store.init_db(DB_PATH)
    SESSIONS.close_all()
    _log("database restored from an uploaded backup; all sessions closed")
    return jsonify({"status": "restored", "locked": True}), 200


@app.route("/api/export")
@unlocked
def api_export(key):
    payload = store.export_all(get_db(), key)
    return Response(
        json.dumps(payload, indent=2, ensure_ascii=False),
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename=journal-{_today().isoformat()}.json"},
    )


@app.route("/api/notify-services")
def api_notify_services():
    services, err = get_notify_services()
    return jsonify({"services": services, "error": err})


# --- Shutdown + entrypoint ---


def _handle_shutdown_signal(signum, _frame):
    _log(f"received signal {signum}, shutting down")
    # The keys die with the process either way; dropping them first makes that
    # a decision rather than a side effect.
    SESSIONS.close_all()
    sys.exit(0)


if __name__ == "__main__":
    from waitress import serve

    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    store.init_db(DB_PATH)
    _log(f"starting Journal {APP_VERSION}")
    threading.Thread(target=_background_loop, daemon=True).start()
    port = int(os.environ.get("JOURNAL_PORT", "8099"))
    _log(f"serving on 0.0.0.0:{port} (waitress)")
    serve(app, host="0.0.0.0", port=port)
