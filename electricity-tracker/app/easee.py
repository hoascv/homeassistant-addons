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


class EaseeError(Exception):
    pass


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
    return {
        "status": STATUS.get(op_mode, f"UNKNOWN({op_mode})"),
        "total_power_w": None if body.get("totalPower") is None else body["totalPower"] * 1000,
        "session_energy_kwh": body.get("sessionEnergy"),
        "lifetime_energy_kwh": body.get("lifetimeEnergy"),
    }
