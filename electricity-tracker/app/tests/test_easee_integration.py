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


# --- What the cost figure actually covers ---
#
# Every test above starts its session at exactly 0.0 kWh, which is why none of
# them noticed that the first sample's energy was never priced: at 0.0 there is
# nothing to miss. These start it somewhere else.


def _seed_flat_day(conn, price=1.2):
    """A whole day of flat prices, so any implied kr/kWh is checkable by eye."""
    for hour in range(24):
        for minute in (0, 15, 30, 45):
            _seed_price(conn, f"2026-08-16T{hour:02d}:{minute:02d}:00", spot=price)


def _flat_opts():
    return electricityapp.get_price_options(
        {"grid_tariff_normal": 0.0, "transmission_tariff": 0.0, "electricity_tax": 0.0, "vat_rate": 0.0}
    )


def test_cost_is_flagged_partial_when_the_session_started_before_our_samples(conn):
    """The add-on installed or restarted mid-charge. Easee's counter reports
    the whole session, but only the tail of it happened where prices could be
    attributed — and the two numbers sit side by side on the dashboard."""
    _seed_flat_day(conn)
    for ts, kwh in [
        ("2026-08-16T08:00:00+00:00", 24.00),  # already well into a charge
        ("2026-08-16T08:05:00+00:00", 25.00),
        ("2026-08-16T08:10:00+00:00", 26.00),
        ("2026-08-16T08:15:00+00:00", 26.83),
    ]:
        _seed_easee_sample(conn, ts, session_energy_kwh=kwh)
    conn.commit()

    session = electricityapp.easee_current_session(conn, _flat_opts(), "DK2", "EH1")
    assert session["session_energy_kwh"] == 26.83
    assert session["session_start_observed"] is False
    assert session["cost_is_partial"] is True
    # Only the 2.83 kWh actually observed is priced — the earlier 24 kWh was
    # consumed at prices nobody recorded, and is not invented.
    assert session["cost_covers_kwh"] == 2.83
    assert session["session_cost_dkk"] == round(2.83 * 1.2, 2)


def test_the_first_sample_of_an_observed_session_is_priced(conn):
    """The regression this pair exists for: a reset *was* seen, so the energy
    on the session's first sample belongs to it. Priced from deltas alone it
    was silently free, dragging the implied rate below the real one."""
    _seed_flat_day(conn)
    _seed_easee_sample(conn, "2026-08-16T07:55:00+00:00", session_energy_kwh=9.0, status="COMPLETED")
    # The counter reset, and by the next poll 0.9 kWh had already gone in.
    for ts, kwh in [
        ("2026-08-16T08:00:00+00:00", 0.90),
        ("2026-08-16T08:05:00+00:00", 1.90),
        ("2026-08-16T08:10:00+00:00", 2.83),
    ]:
        _seed_easee_sample(conn, ts, session_energy_kwh=kwh)
    conn.commit()

    session = electricityapp.easee_current_session(conn, _flat_opts(), "DK2", "EH1")
    assert session["session_start_observed"] is True
    assert session["cost_is_partial"] is False
    assert session["cost_covers_kwh"] == 2.83  # the whole session, baseline included
    assert session["session_cost_dkk"] == round(2.83 * 1.2, 2)


def test_cost_and_energy_agree_for_a_session_watched_end_to_end(conn):
    """The property that matters, stated directly: when nothing was missed,
    cost divided by energy is the price that was actually charged."""
    _seed_flat_day(conn, price=1.2)
    _seed_easee_sample(conn, "2026-08-16T07:55:00+00:00", session_energy_kwh=5.0, status="COMPLETED")
    for i, kwh in enumerate([0.0, 2.0, 4.0, 6.0, 8.0]):
        _seed_easee_sample(conn, f"2026-08-16T08:{i * 5:02d}:00+00:00", session_energy_kwh=kwh)
    conn.commit()

    session = electricityapp.easee_current_session(conn, _flat_opts(), "DK2", "EH1")
    assert session["cost_is_partial"] is False
    assert session["session_cost_dkk"] / session["session_energy_kwh"] == 1.2


def test_a_lone_sample_reports_unknown_cost_rather_than_zero(conn):
    """0.00 kr would be a claim; "–" is the truth when nothing has been priced."""
    _seed_flat_day(conn)
    _seed_easee_sample(conn, "2026-08-16T08:00:00+00:00", session_energy_kwh=4.0)
    conn.commit()
    session = electricityapp.easee_current_session(conn, _flat_opts(), "DK2", "EH1")
    assert session["session_cost_dkk"] is None
    assert session["cost_covers_kwh"] == 0.0
    assert session["cost_is_partial"] is True


def test_an_idle_charger_reports_zero_cost_not_unknown(conn):
    """Nothing plugged in: no energy, so no cost — and that genuinely is 0."""
    _seed_flat_day(conn)
    _seed_easee_sample(conn, "2026-08-16T08:00:00+00:00", session_energy_kwh=0.0, status="DISCONNECTED", power_w=0.0)
    conn.commit()
    session = electricityapp.easee_current_session(conn, _flat_opts(), "DK2", "EH1")
    assert session["session_cost_dkk"] == 0.0
    assert session["cost_is_partial"] is False


def test_a_missing_price_leaves_the_cost_unknown_rather_than_understated(conn):
    """Half-priced energy must not be reported as a whole cost."""
    # Prices for 10:00 local only; the session runs into an unpriced hour.
    for minute in (0, 15, 30, 45):
        _seed_price(conn, f"2026-08-16T10:{minute:02d}:00", spot=1.2)
    _seed_easee_sample(conn, "2026-08-16T07:55:00+00:00", session_energy_kwh=9.0, status="COMPLETED")
    _seed_easee_sample(conn, "2026-08-16T08:00:00+00:00", session_energy_kwh=1.0)
    _seed_easee_sample(conn, "2026-08-16T09:00:00+00:00", session_energy_kwh=5.0)
    conn.commit()
    session = electricityapp.easee_current_session(conn, _flat_opts(), "DK2", "EH1")
    assert session["session_cost_dkk"] is None


def test_the_session_reading_carries_the_time_it_was_taken(conn):
    """A status is only as current as its poll, and a failed sync writes no
    row — so the dashboard needs the reading's own timestamp to say so."""
    _seed_easee_sample(conn, "2026-08-16T08:00:00+00:00", session_energy_kwh=1.0)
    _seed_easee_sample(conn, "2026-08-16T08:05:00+00:00", session_energy_kwh=2.0)
    conn.commit()
    session = electricityapp.easee_current_session(conn, _flat_opts(), "DK2", "EH1")
    assert session["measured_at"] == "2026-08-16T08:05:00+00:00"


def test_summary_exposes_the_last_easee_sync(conn, client, set_options):
    set_options(easee_enabled=True, easee_username="u", easee_password="p", easee_charger_id="EH1")
    _seed_easee_sample(conn, "2026-08-16T08:00:00+00:00", session_energy_kwh=1.0)
    electricityapp._set_app_state(conn, "last_easee_sync", "2026-08-16T08:00:05+00:00")
    conn.commit()
    easee_block = client.get("/api/summary").get_json()["easee"]
    assert easee_block["last_sync"] == "2026-08-16T08:00:05+00:00"


def test_a_reset_still_bounds_the_session_when_the_baseline_is_large(conn):
    """The existing reset test starts the new session at 0.0; this one proves
    the boundary still holds when the first post-reset poll is already well in."""
    _seed_flat_day(conn)
    _seed_easee_sample(conn, "2026-08-16T07:00:00+00:00", session_energy_kwh=40.0)
    _seed_easee_sample(conn, "2026-08-16T08:00:00+00:00", session_energy_kwh=3.0)  # reset, already 3 kWh in
    _seed_easee_sample(conn, "2026-08-16T08:30:00+00:00", session_energy_kwh=4.0)
    conn.commit()
    session = electricityapp.easee_current_session(conn, _flat_opts(), "DK2", "EH1")
    assert session["session_energy_kwh"] == 4.0  # not 44
    assert session["cost_covers_kwh"] == 4.0
    assert session["session_cost_dkk"] == round(4.0 * 1.2, 2)
