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
from datetime import datetime

# garth dumps these two files into the token directory; their presence is how
# we tell "connected" from "not connected" without constructing a client.
# garminconnect persists a single token file (the DI refresh token) inside the
# token directory; its presence is how we tell "connected" without a client.
_TOKEN_FILE = "garmin_tokens.json"

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
    # garminconnect's own client.dump() creates the directory (0700) and writes
    # garmin_tokens.json (0600) itself — no separate garth object is involved.
    garmin.client.dump(TOKENSTORE)


def is_connected():
    return os.path.isfile(os.path.join(TOKENSTORE, _TOKEN_FILE))


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


# Body Battery comes from two places and neither is guaranteed. The daily user
# summary carries the four values outright and is what Garmin's own app shows;
# the reports/daily endpoint carries a series to reduce, whose row layout
# varies by account and firmware. The summary is tried first, and the series
# only fills what it left empty.
_BB_SUMMARY_KEYS = {
    "body_battery_high": "bodyBatteryHighestValue",
    "body_battery_low": "bodyBatteryLowestValue",
    "body_battery_charged": "bodyBatteryChargedValue",
    "body_battery_drained": "bodyBatteryDrainedValue",
}


def _body_battery_from_summary(client, day):
    try:
        data = client.get_user_summary(day) or {}
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for field, key in _BB_SUMMARY_KEYS.items():
        value = _num(data.get(key))
        if value is not None:
            out[field] = value
    return out


def _bb_level_index(entry):
    """Which column of a bodyBatteryValuesArray row holds the level. The
    response describes its own layout, so the descriptor wins over the
    historical index of 2."""
    for descriptor in entry.get("bodyBatteryValueDescriptorDTOList") or []:
        if not isinstance(descriptor, dict):
            continue
        key = descriptor.get("bodyBatteryValueDescriptorKey") or descriptor.get("key") or ""
        if "level" in str(key).lower():
            index = descriptor.get("bodyBatteryValueDescriptorIndex", descriptor.get("index"))
            if isinstance(index, int) and not isinstance(index, bool):
                return index
    return None


def _bb_row_level(row, index):
    if not isinstance(row, (list, tuple)) or len(row) < 2:
        return None
    candidates = [index] if index is not None else []
    # [timestamp, status, level, version] historically; [timestamp, level] on
    # the accounts that return the short form.
    candidates += [2, 1] if len(row) > 2 else [1]
    for i in candidates:
        if i is None or i >= len(row):
            continue
        value = row[i]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    return None


def _body_battery_from_series(client, day):
    try:
        data = client.get_body_battery(day, day) or []
    except Exception:  # noqa: BLE001
        return {}
    entry = data[0] if isinstance(data, list) and data else {}
    if not isinstance(entry, dict):
        return {}
    index = _bb_level_index(entry)
    levels = []
    for row in entry.get("bodyBatteryValuesArray") or []:
        level = _bb_row_level(row, index)
        if level is not None:
            levels.append(level)
    out = {}
    if levels:
        out["body_battery_high"] = _num(max(levels))
        out["body_battery_low"] = _num(min(levels))
    for field, key in (("body_battery_charged", "charged"), ("body_battery_drained", "drained")):
        value = _num(entry.get(key))
        if value is not None:
            out[field] = value
    return out


def _body_battery_fields(client, day):
    fields = _body_battery_from_summary(client, day)
    if fields.get("body_battery_high") is None or fields.get("body_battery_low") is None:
        for key, value in _body_battery_from_series(client, day).items():
            if fields.get(key) is None:
                fields[key] = value
    return fields


def diagnose_body_battery(client, day):
    """What each Body Battery source gives for one day. Reports the parsed
    values plus the shape of the series response, which is what a mismatch
    turns on — not the raw payload."""
    summary_keys, sample_row, descriptors = [], None, []
    try:
        summary = client.get_user_summary(day) or {}
        if isinstance(summary, dict):
            summary_keys = sorted(k for k in summary if "bodyBattery" in k)
    except Exception as e:  # noqa: BLE001
        summary_keys = [f"error: {e}"]
    try:
        data = client.get_body_battery(day, day) or []
        entry = data[0] if isinstance(data, list) and data else {}
        if isinstance(entry, dict):
            rows = entry.get("bodyBatteryValuesArray") or []
            sample_row = rows[0] if rows else None
            descriptors = entry.get("bodyBatteryValueDescriptorDTOList") or []
    except Exception as e:  # noqa: BLE001
        descriptors = [f"error: {e}"]
    return {
        "day": day,
        "from_summary": _body_battery_from_summary(client, day),
        "from_series": _body_battery_from_series(client, day),
        "merged": _body_battery_fields(client, day),
        "summary_body_battery_keys": summary_keys,
        "series_sample_row": sample_row,
        "series_level_index": _bb_level_index({"bodyBatteryValueDescriptorDTOList": descriptors})
        if descriptors
        else None,
        "series_descriptors": descriptors,
    }


def device_last_upload(client):
    """When the watch itself last uploaded to Garmin Connect, as a local ISO
    string (Garmin reports epoch milliseconds).

    This is the difference between "Garmin has no data for that day" and "the
    watch hasn't told Garmin about that day yet" — the add-on can talk to
    Garmin successfully every hour and still be looking at a watch that last
    uploaded on Tuesday.
    """
    try:
        data = client.get_device_last_used() or {}
    except Exception:  # noqa: BLE001 - never let this sink a sync
        return None
    raw = data.get("lastUsedDeviceUploadTime")
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(raw / 1000).isoformat(timespec="seconds")
        except (OverflowError, OSError, ValueError):
            return None
    return str(raw)


def fetch_day(client, day):
    """All wellness metrics for one YYYY-MM-DD, flattened into a single dict."""
    fields = {}
    fields.update(_sleep_fields(client, day))
    fields.update(_stress_fields(client, day))
    fields.update(_body_battery_fields(client, day))
    return fields


def fetch_heart_rate_series(client, day):
    """The day's heart-rate samples as [(epoch_seconds, bpm)], oldest first.

    Garmin records these every couple of minutes all day, which is what makes
    it possible to work out the heart rate for an exercise that was never
    started on the watch — and to do it later, once the watch has uploaded.
    Gaps come through as null and are dropped.
    """
    try:
        data = client.get_heart_rates(day) or {}
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, dict):
        return []
    out = []
    for row in data.get("heartRateValues") or []:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        at, bpm = row[0], row[1]
        if isinstance(at, bool) or isinstance(bpm, bool):
            continue
        if not isinstance(at, (int, float)) or not isinstance(bpm, (int, float)):
            continue
        out.append((at / 1000.0, int(bpm)))
    out.sort()
    return out


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
