"""Tests for how app.py consumes Easee: config parsing, token caching,
charger discovery, and session-cost derivation from sessionEnergy deltas.
The REST client itself is covered by test_easee.py."""
from datetime import datetime, timedelta
from unittest.mock import patch

import app as electricityapp
import easee


def test_get_easee_config_defaults():
    cfg = electricityapp.get_easee_config({})
    assert cfg == {"enabled": False, "username": "", "password": "", "charger_id": None}


def test_get_easee_config_reads_values():
    cfg = electricityapp.get_easee_config(
        {"easee_enabled": True, "easee_username": " user@example.com ", "easee_password": "pw", "easee_charger_id": " EH1 "}
    )
    assert cfg == {"enabled": True, "username": "user@example.com", "password": "pw", "charger_id": "EH1"}


def test_get_easee_access_token_logs_in_and_caches(monkeypatch):
    calls = {"login": 0}

    def fake_login(username, password, timeout=15):
        calls["login"] += 1
        return {"access_token": "acc-1", "refresh_token": "ref-1", "expires_at": 9999999999.0}

    monkeypatch.setattr(easee, "login", fake_login)
    t1 = electricityapp._get_easee_access_token("user", "pw")
    t2 = electricityapp._get_easee_access_token("user", "pw")
    assert t1 == t2 == "acc-1"
    assert calls["login"] == 1  # second call served from cache


def test_get_easee_access_token_refreshes_when_expired(monkeypatch):
    electricityapp._easee_token_cache.update(
        username="user", access_token="stale", refresh_token="ref-1", expires_at=0.0
    )
    calls = {"refresh": 0, "login": 0}

    def fake_refresh(access_token, refresh_token_value, timeout=15):
        calls["refresh"] += 1
        return {"access_token": "acc-2", "refresh_token": "ref-2", "expires_at": 9999999999.0}

    monkeypatch.setattr(easee, "refresh_token", fake_refresh)
    monkeypatch.setattr(easee, "login", lambda *a, **k: calls.__setitem__("login", calls["login"] + 1) or {})
    token = electricityapp._get_easee_access_token("user", "pw")
    assert token == "acc-2"
    assert calls["refresh"] == 1
    assert calls["login"] == 0


def test_get_easee_access_token_falls_back_to_login_when_refresh_fails(monkeypatch):
    electricityapp._easee_token_cache.update(
        username="user", access_token="stale", refresh_token="bad-refresh", expires_at=0.0
    )
    monkeypatch.setattr(easee, "refresh_token", lambda *a, **k: (_ for _ in ()).throw(easee.EaseeError("nope")))
    monkeypatch.setattr(
        easee, "login", lambda *a, **k: {"access_token": "fresh", "refresh_token": "fresh-r", "expires_at": 9999999999.0}
    )
    token = electricityapp._get_easee_access_token("user", "pw")
    assert token == "fresh"


def test_resolve_easee_charger_id_uses_configured_value(conn):
    charger_id = electricityapp._resolve_easee_charger_id("acc-1", "EH-configured")
    assert charger_id == "EH-configured"


def test_resolve_easee_charger_id_discovers_and_caches(monkeypatch):
    monkeypatch.setattr(easee, "get_chargers", lambda access_token: [{"id": "EH-discovered", "name": "Garage"}])
    charger_id = electricityapp._resolve_easee_charger_id("acc-1", None)
    assert charger_id == "EH-discovered"
    assert electricityapp._easee_token_cache["charger_id"] == "EH-discovered"
    # Second call should not need get_chargers again — remove it to prove the cache is used.
    monkeypatch.setattr(easee, "get_chargers", lambda access_token: (_ for _ in ()).throw(AssertionError("called again")))
    assert electricityapp._resolve_easee_charger_id("acc-1", None) == "EH-discovered"


def test_resolve_easee_charger_id_none_when_no_chargers(monkeypatch):
    monkeypatch.setattr(easee, "get_chargers", lambda access_token: [])
    assert electricityapp._resolve_easee_charger_id("acc-1", None) is None


def test_sync_easee_skipped_when_disabled(conn):
    electricityapp.sync_easee(conn, {"easee_enabled": False})
    assert conn.execute("SELECT COUNT(*) AS n FROM easee_samples").fetchone()["n"] == 0


def test_sync_easee_skipped_without_credentials(conn):
    electricityapp.sync_easee(conn, {"easee_enabled": True})
    assert conn.execute("SELECT COUNT(*) AS n FROM easee_samples").fetchone()["n"] == 0


def test_sync_easee_writes_a_sample(conn, monkeypatch):
    monkeypatch.setattr(
        easee, "login", lambda *a, **k: {"access_token": "acc-1", "refresh_token": "ref-1", "expires_at": 9999999999.0}
    )
    monkeypatch.setattr(easee, "get_chargers", lambda access_token: [{"id": "EH1", "name": "Garage"}])
    monkeypatch.setattr(
        easee,
        "get_charger_state",
        lambda access_token, charger_id: {
            "status": "CHARGING",
            "total_power_w": 7200.0,
            "session_energy_kwh": 1.5,
            "lifetime_energy_kwh": 500.0,
        },
    )
    electricityapp.sync_easee(
        conn, {"easee_enabled": True, "easee_username": "user@example.com", "easee_password": "pw"}
    )
    rows = conn.execute("SELECT * FROM easee_samples").fetchall()
    assert len(rows) == 1
    assert rows[0]["session_energy_kwh"] == 1.5
    assert electricityapp._get_app_state(conn, "last_easee_sync") is not None


def _seed_price(conn, time_dk, spot=1.0, price_area="DK2"):
    conn.execute(
        "INSERT INTO prices (time_dk, price_area, spot_price_dkk_kwh, fetched_at) VALUES (?, ?, ?, ?)",
        (time_dk, price_area, spot, "2026-08-16T00:00:00+00:00"),
    )


def _seed_easee_sample(conn, ts_utc, session_energy_kwh, power_w=7200.0, status="CHARGING", charger_id="EH1"):
    conn.execute(
        "INSERT INTO easee_samples (ts_utc, charger_id, status, session_energy_kwh, total_power_w, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (ts_utc, charger_id, status, session_energy_kwh, power_w, ts_utc),
    )


def test_easee_current_session_no_data_returns_none(conn):
    assert electricityapp.easee_current_session(conn, electricityapp.get_price_options({}), "DK2", "EH1") is None


def test_easee_current_session_single_sample_has_no_cost_yet(conn):
    _seed_easee_sample(conn, "2026-08-16T10:00:00+00:00", session_energy_kwh=0.5)
    conn.commit()
    session = electricityapp.easee_current_session(conn, electricityapp.get_price_options({}), "DK2", "EH1")
    assert session["session_energy_kwh"] == 0.5
    assert session["session_cost_dkk"] is None


def test_easee_current_session_computes_cost_from_deltas(conn):
    opts = electricityapp.get_price_options(
        {"grid_tariff_normal": 0.0, "transmission_tariff": 0.0, "electricity_tax": 0.0, "vat_rate": 0.0}
    )
    # Danish local 12:00-13:00 == UTC 10:00-11:00 (CEST).
    for minute in (0, 15, 30, 45):
        _seed_price(conn, f"2026-08-16T12:{minute:02d}:00", spot=2.0)
        _seed_price(conn, f"2026-08-16T13:{minute:02d}:00", spot=3.0)
    _seed_easee_sample(conn, "2026-08-16T10:00:00+00:00", session_energy_kwh=0.0)
    _seed_easee_sample(conn, "2026-08-16T10:30:00+00:00", session_energy_kwh=1.0)  # +1 kWh at 2.0 DKK/kWh
    _seed_easee_sample(conn, "2026-08-16T11:15:00+00:00", session_energy_kwh=2.0)  # +1 kWh at 3.0 DKK/kWh
    conn.commit()

    session = electricityapp.easee_current_session(conn, opts, "DK2", "EH1")
    assert session["session_energy_kwh"] == 2.0
    assert session["session_cost_dkk"] == 5.0  # 1*2.0 + 1*3.0


def test_easee_current_session_ignores_samples_before_a_reset(conn):
    opts = electricityapp.get_price_options(
        {"grid_tariff_normal": 0.0, "transmission_tariff": 0.0, "electricity_tax": 0.0, "vat_rate": 0.0}
    )
    for minute in (0, 15, 30, 45):
        _seed_price(conn, f"2026-08-16T12:{minute:02d}:00", spot=2.0)
    # First session: 0 -> 5 kWh (would be a huge cost if counted).
    _seed_easee_sample(conn, "2026-08-16T09:00:00+00:00", session_energy_kwh=0.0)
    _seed_easee_sample(conn, "2026-08-16T09:30:00+00:00", session_energy_kwh=5.0)
    # Reset: a new session starts, energy drops back to near zero.
    _seed_easee_sample(conn, "2026-08-16T10:00:00+00:00", session_energy_kwh=0.0)
    _seed_easee_sample(conn, "2026-08-16T10:30:00+00:00", session_energy_kwh=1.0)
    conn.commit()

    session = electricityapp.easee_current_session(conn, opts, "DK2", "EH1")
    assert session["session_energy_kwh"] == 1.0  # only the second session counted
    assert session["session_cost_dkk"] == 2.0


def test_api_easee_now_disabled_by_default(client):
    res = client.get("/api/easee/now")
    assert res.get_json() == {"enabled": False}


def test_api_easee_now_enabled_no_charger_yet(client, set_options):
    set_options(easee_enabled=True, easee_username="u", easee_password="p")
    data = client.get("/api/easee/now").get_json()
    assert data["enabled"] is True
    assert data["charger_id"] is None
    assert data["session"] is None


def test_api_easee_diagnose_without_credentials(client):
    res = client.get("/api/easee/diagnose")
    assert res.status_code == 400
    assert res.get_json()["ok"] is False
