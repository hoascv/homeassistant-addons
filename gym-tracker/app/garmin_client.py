"""Thin wrapper around the unofficial `garminconnect` library.

All third-party Garmin access is isolated here so `app.py` stays import-light
(the heavy `garminconnect`/`garth`/`curl_cffi` stack is imported lazily, inside
the functions that need it) and so tests can monkeypatch this whole module
without a real Garmin account.

Auth model: an in-app login collects the email/password once, completes any 2FA
challenge, and persists only the resulting OAuth tokens (never the password) to
a token directory under `/data` via garth. Those tokens auto-refresh, so after
the first connect the background sync just reloads them.
"""
import os
import shutil
import threading

# garth dumps these two files into the token directory; their presence is how
# we tell "connected" from "not connected" without constructing a client.
_TOKEN_FILES = ("oauth1_token.json", "oauth2_token.json")

# Persisted under the add-on's /data volume so the login survives restarts and
# updates. Overridable for tests.
TOKENSTORE = os.environ.get("GARMIN_TOKENSTORE", "/data/garmin")

# A 2FA login is two HTTP requests (submit credentials, then submit the code).
# The live Garmin client returned by the first step must survive until the
# second, so it's held here. Single admin user + a lock keeps this safe under
# waitress's threaded server.
_pending_lock = threading.Lock()
_pending = None  # (garmin_client, client_state) awaiting an MFA code


def _new_client(email=None, password=None, return_on_mfa=False):
    from garminconnect import Garmin

    return Garmin(email=email, password=password, return_on_mfa=return_on_mfa)


def _save_tokens(garmin):
    os.makedirs(TOKENSTORE, exist_ok=True)
    garmin.garth.dump(TOKENSTORE)


def is_connected():
    return all(os.path.isfile(os.path.join(TOKENSTORE, f)) for f in _TOKEN_FILES)


def begin_login(email, password):
    """Start a login. Returns {"status": "connected"} when no 2FA is needed, or
    {"status": "mfa_required"} — in which case complete_mfa() must follow."""
    global _pending
    garmin = _new_client(email=email, password=password, return_on_mfa=True)
    result1, result2 = garmin.login()
    if result1 == "needs_mfa":
        with _pending_lock:
            _pending = (garmin, result2)
        return {"status": "mfa_required"}
    _save_tokens(garmin)
    return {"status": "connected"}


def complete_mfa(code):
    """Finish a login started by begin_login() using the emailed/app 2FA code."""
    global _pending
    with _pending_lock:
        pending = _pending
    if not pending:
        raise RuntimeError("no login is awaiting a 2FA code — start again")
    garmin, client_state = pending
    garmin.resume_login(client_state, str(code).strip())
    _save_tokens(garmin)
    with _pending_lock:
        _pending = None
    return {"status": "connected"}


def disconnect():
    """Forget the Garmin connection: drop any pending login and delete tokens."""
    global _pending
    with _pending_lock:
        _pending = None
    shutil.rmtree(TOKENSTORE, ignore_errors=True)


def get_client():
    """Load the saved tokens into a ready-to-use client (auto-refreshing).
    Raises if not connected."""
    if not is_connected():
        raise RuntimeError("Garmin is not connected")
    garmin = _new_client()
    garmin.login(TOKENSTORE)
    return garmin


# --- Normalizers: turn the library's verbose payloads into flat dicts ---------
#
# The unofficial API's shapes drift, so every field is pulled defensively and a
# failure in one metric never sinks the others.


def _num(value):
    if value is None:
        return None
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return None


def _sleep_fields(client, day):
    try:
        data = client.get_sleep_data(day) or {}
        dto = data.get("dailySleepDTO") or {}
        scores = dto.get("sleepScores") or {}
        overall = scores.get("overall") or {}
        return {
            "sleep_seconds": _num(dto.get("sleepTimeSeconds")),
            "sleep_deep_seconds": _num(dto.get("deepSleepSeconds")),
            "sleep_light_seconds": _num(dto.get("lightSleepSeconds")),
            "sleep_rem_seconds": _num(dto.get("remSleepSeconds")),
            "sleep_awake_seconds": _num(dto.get("awakeSleepSeconds")),
            "sleep_score": _num(overall.get("value")),
        }
    except Exception:  # noqa: BLE001 - one bad metric shouldn't fail the day
        return {}


def _stress_fields(client, day):
    try:
        data = client.get_all_day_stress(day) or {}
        return {
            "stress_avg": _num(data.get("avgStressLevel")),
            "stress_max": _num(data.get("maxStressLevel")),
        }
    except Exception:  # noqa: BLE001
        return {}


def _body_battery_fields(client, day):
    try:
        data = client.get_body_battery(day, day) or []
        entry = data[0] if data else {}
        levels = [
            row[2]
            for row in (entry.get("bodyBatteryValuesArray") or [])
            if isinstance(row, (list, tuple)) and len(row) >= 3 and isinstance(row[2], (int, float))
        ]
        return {
            "body_battery_high": _num(max(levels)) if levels else None,
            "body_battery_low": _num(min(levels)) if levels else None,
            "body_battery_charged": _num(entry.get("charged")),
            "body_battery_drained": _num(entry.get("drained")),
        }
    except Exception:  # noqa: BLE001
        return {}


def fetch_day(client, day):
    """All wellness metrics for one YYYY-MM-DD, flattened into a single dict."""
    fields = {}
    fields.update(_sleep_fields(client, day))
    fields.update(_stress_fields(client, day))
    fields.update(_body_battery_fields(client, day))
    return fields


def fetch_activities(client, start, end):
    """Normalized activities between two YYYY-MM-DD dates (inclusive)."""
    try:
        raw = client.get_activities_by_date(start, end) or []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for a in raw:
        activity_id = a.get("activityId")
        if activity_id is None:
            continue
        atype = (a.get("activityType") or {}).get("typeKey")
        out.append(
            {
                "activity_id": int(activity_id),
                "start_time": a.get("startTimeLocal") or a.get("startTimeGMT"),
                "activity_type": atype,
                "name": a.get("activityName"),
                "duration_sec": _num(a.get("duration")),
                "distance_m": a.get("distance") if isinstance(a.get("distance"), (int, float)) else None,
                "calories": _num(a.get("calories")),
                "avg_hr": _num(a.get("averageHR")),
                "max_hr": _num(a.get("maxHR")),
            }
        )
    return out
