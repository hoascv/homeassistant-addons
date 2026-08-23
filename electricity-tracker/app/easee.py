"""Client for Easee's cloud REST API (api.easee.com), for an Easee Home
charger. Read-only here — no charging control, only state.

Endpoints and field names verified against Easee's own official Python
client (github.com/nordicopen/pyeasee) rather than guessed: this repository
has no free/public API documentation to fetch directly, and pyeasee's source
is the closest thing to one.
"""
import json
import time
import urllib.error
import urllib.request

API_BASE = "https://api.easee.com"

# chargerOpMode -> human-readable status, from pyeasee's STATUS table.
STATUS = {
    0: "OFFLINE",
    1: "DISCONNECTED",
    2: "AWAITING_START",
    3: "CHARGING",
    4: "COMPLETED",
    5: "ERROR",
    6: "READY_TO_CHARGE",
    7: "AWAITING_AUTHORIZATION",
    8: "DE_AUTHORIZING",
}


# reasonForNoCurrent -> why the charger is not delivering, from pyeasee's own
# table. That table carries the caveat that it is reverse-engineered from
# observation rather than documented by Easee, so it is used only to *explain*
# a state, never to decide one — the decision below rests on measured power.
REASON_FOR_NO_CURRENT = {
    0: "no reason — charging or ready to charge",
    1: "max circuit limit too low",
    2: "max dynamic circuit limit too low",
    3: "max dynamic offline limit too low",
    4: "circuit fuse too low",
    5: "waiting in queue",
    6: "waiting in fully",
    7: "illegal grid type",
    8: "no current request received",
    9: "not connected to master",
    10: "EQ current too low",
    11: "phase not connected",
    25: "limited by circuit fuse",
    26: "limited by circuit max limit",
    27: "limited by circuit dynamic limit",
    28: "limited by equalizer",
    29: "limited by load balancing",
    30: "limited by offline settings",
    50: "no car connected, or the secondary unit is not requesting current",
    51: "max charger limit too low",
    52: "max dynamic charger limit too low",
    53: "charger disabled",
    54: "waiting for schedule/auth",
    55: "pending auth",
    56: "charger in error state",
    57: "EV erratic",
    75: "limited by cable rating",
    76: "limited by schedule",
    77: "limited by charger max limit",
    78: "limited by charger dynamic limit",
    79: "EV is not charging",
    80: "limited by local adjustment",
    81: "limited by EV",
    100: "undefined",
}

# Below this, nothing is meaningfully flowing. A charger delivering the legal
# minimum (6 A at 230 V) is drawing about 1.4 kW, so there is no real charging
# anywhere near this figure — it only has to clear measurement noise.
CHARGING_POWER_THRESHOLD_W = 50.0


class EaseeError(Exception):
    pass


def describe_reason(code):
    """Human-readable `reasonForNoCurrent`, or None when there is nothing to
    explain (no code, or the code that means "nothing is wrong")."""
    if code is None or code == 0:
        return None
    return REASON_FOR_NO_CURRENT.get(code, f"unknown reason ({code})")


def effective_status(status, total_power_w, reason_code=None):
    """What the charger is *doing*, as opposed to what `chargerOpMode` says.

    Easee holds a session in CHARGING while delivering nothing at all — the car
    is full but still plugged in, the car's own schedule has paused it, load
    balancing has throttled to zero. `chargerOpMode` stays 3 throughout, so a
    dashboard that trusts it alone reports "CHARGING" next to 0.00 kW, which is
    the complaint that started this.

    Measured power is the arbiter because it is the one field that cannot be
    wrong about whether energy is moving. `reasonForNoCurrent` only supplies the
    explanation, and is deliberately not consulted for the decision: its codes
    are reverse-engineered, and a mis-mapped code should never be able to turn a
    charge that is visibly happening into a pause.
    """
    if status != "CHARGING":
        return status
    if total_power_w is None:
        # No power reading at all — nothing to contradict the charger with.
        return status
    return "CHARGING" if total_power_w >= CHARGING_POWER_THRESHOLD_W else "PAUSED"


def _request(path, method="GET", body=None, access_token=None, timeout=15):
    req = urllib.request.Request(f"{API_BASE}{path}", method=method)
    req.add_header("Accept", "application/json")
    if access_token:
        req.add_header("Authorization", f"Bearer {access_token}")
    data = None
    if body is not None:
        req.add_header("Content-Type", "application/json;charset=UTF-8")
        data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise EaseeError(f"HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise EaseeError(f"request failed: {exc}") from exc
    try:
        return json.loads(raw) if raw else {}
    except ValueError as exc:
        raise EaseeError(f"bad JSON: {exc}") from exc


def _parse_token(body):
    access_token = body.get("accessToken")
    refresh_token = body.get("refreshToken")
    expires_in = body.get("expiresIn")
    if not access_token or not refresh_token:
        raise EaseeError(f"no tokens in response: {body}")
    try:
        expires_in = int(expires_in)
    except (TypeError, ValueError):
        expires_in = 3600
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        # Refreshed a minute early to stay ahead of expiry.
        "expires_at": time.time() + max(60, expires_in - 60),
    }


def login(username, password, timeout=15):
    """Exchange an Easee account's email/password for a token pair."""
    body = _request(
        "/api/accounts/login", method="POST", body={"userName": username, "password": password}, timeout=timeout
    )
    return _parse_token(body)


def refresh_token(access_token, refresh_token_value, timeout=15):
    body = _request(
        "/api/accounts/refresh_token",
        method="POST",
        body={"accessToken": access_token, "refreshToken": refresh_token_value},
        timeout=timeout,
    )
    return _parse_token(body)


def get_chargers(access_token, timeout=15):
    """Every charger on the account: [{"id": str, "name": str, ...}]."""
    body = _request("/api/chargers", access_token=access_token, timeout=timeout)
    return body if isinstance(body, list) else []


def get_charger_state(access_token, charger_id, timeout=15):
    """Live state for one charger. Pulls out the handful of fields this
    add-on cares about; the raw response has many more.

    `session_energy_kwh` is the *current session's* accumulated energy —
    resets to (near) zero when a new charging session starts, which is what
    makes it possible to attribute cost to a session by diffing successive
    polls rather than needing Saveeye-style interpolation of an
    ever-increasing counter.
    """
    body = _request(f"/api/chargers/{charger_id}/state", access_token=access_token, timeout=timeout)
    op_mode = body.get("chargerOpMode")
    reason = body.get("reasonForNoCurrent")
    return {
        # Easee's own opMode name, stored as-is. The derived "what is it
        # actually doing" reading is computed at read time by effective_status,
        # so improving that judgement never needs a re-sync of stored history.
        "status": STATUS.get(op_mode, f"UNKNOWN({op_mode})"),
        "total_power_w": None if body.get("totalPower") is None else body["totalPower"] * 1000,
        "session_energy_kwh": body.get("sessionEnergy"),
        "lifetime_energy_kwh": body.get("lifetimeEnergy"),
        "reason_for_no_current": reason if isinstance(reason, int) else None,
    }
