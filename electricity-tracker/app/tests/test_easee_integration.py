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


def _seed_easee_sample(
    conn, ts_utc, session_energy_kwh, power_w=7200.0, status="CHARGING", charger_id="EH1", reason=None
):
    conn.execute(
        "INSERT INTO easee_samples (ts_utc, charger_id, status, session_energy_kwh, total_power_w, "
        "reason_for_no_current, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ts_utc, charger_id, status, session_energy_kwh, power_w, reason, ts_utc),
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


# --- Where one session ends and the next begins ---


def test_unplugging_ends_the_session(conn):
    """Easee's counter simply holds its final value after a charge, so without
    treating DISCONNECTED as a boundary the "current session" grows for as long
    as the sample window is deep — reporting a start time that recedes further
    into the past every day while describing a charge that already finished."""
    _seed_flat_day(conn)
    _seed_easee_sample(conn, "2026-08-16T08:00:00+00:00", session_energy_kwh=0.0)
    _seed_easee_sample(conn, "2026-08-16T08:30:00+00:00", session_energy_kwh=4.0)
    _seed_easee_sample(conn, "2026-08-16T09:00:00+00:00", session_energy_kwh=4.0, status="COMPLETED", power_w=0.0)
    # Car unplugged, then hours of idle samples that must not join the session.
    for hour in range(10, 14):
        _seed_easee_sample(
            conn, f"2026-08-16T{hour:02d}:00:00+00:00", session_energy_kwh=4.0,
            status="DISCONNECTED", power_w=0.0,
        )
    conn.commit()

    session = electricityapp.easee_current_session(conn, _flat_opts(), "DK2", "EH1")
    assert session["status"] == "DISCONNECTED"          # what it is doing now
    assert session["session_energy_kwh"] == 4.0          # the charge that happened
    assert session["session_started_at"] == "2026-08-16T08:00:00+00:00"
    # The charge ended when the counter stopped moving, not when the cable came
    # out an hour later — idle time with a cable in is not charging time.
    assert session["session_ended_at"] == "2026-08-16T08:30:00+00:00"


def test_a_completed_session_still_shows_its_figures(conn):
    """COMPLETED is the tail of the session, not a boundary — the card is meant
    to show the current *or most recent* charge."""
    _seed_flat_day(conn)
    _seed_easee_sample(conn, "2026-08-16T08:00:00+00:00", session_energy_kwh=0.0)
    _seed_easee_sample(conn, "2026-08-16T08:30:00+00:00", session_energy_kwh=6.0)
    _seed_easee_sample(conn, "2026-08-16T09:00:00+00:00", session_energy_kwh=6.0, status="COMPLETED", power_w=0.0)
    conn.commit()
    session = electricityapp.easee_current_session(conn, _flat_opts(), "DK2", "EH1")
    assert session["status"] == "COMPLETED"
    assert session["session_energy_kwh"] == 6.0
    assert session["session_cost_dkk"] == round(6.0 * 1.2, 2)
    # COMPLETED at 09:00 is the charger still reporting, but the last kWh
    # arrived at 08:30 — that is when the charge finished.
    assert session["session_ended_at"] == "2026-08-16T08:30:00+00:00"


def test_a_new_plug_in_at_zero_does_not_hide_the_last_charge(conn):
    """Plugged in again but not charging yet. A zero-energy segment is not a
    session worth showing, so the previous charge stays on the card."""
    _seed_flat_day(conn)
    _seed_easee_sample(conn, "2026-08-16T08:00:00+00:00", session_energy_kwh=0.0)
    _seed_easee_sample(conn, "2026-08-16T08:30:00+00:00", session_energy_kwh=5.0)
    _seed_easee_sample(conn, "2026-08-16T09:00:00+00:00", session_energy_kwh=5.0, status="DISCONNECTED", power_w=0.0)
    _seed_easee_sample(conn, "2026-08-16T10:00:00+00:00", session_energy_kwh=0.0,
                       status="AWAITING_START", power_w=0.0)
    conn.commit()
    session = electricityapp.easee_current_session(conn, _flat_opts(), "DK2", "EH1")
    assert session["status"] == "AWAITING_START"   # now
    assert session["session_energy_kwh"] == 5.0    # the charge that happened


def test_status_and_power_always_come_from_the_newest_sample(conn):
    """Two different questions: what is it doing now, and what did the last
    charge cost. They are answered from two different rows."""
    _seed_flat_day(conn)
    _seed_easee_sample(conn, "2026-08-16T08:00:00+00:00", session_energy_kwh=0.0)
    _seed_easee_sample(conn, "2026-08-16T08:30:00+00:00", session_energy_kwh=3.0, power_w=7200.0)
    _seed_easee_sample(conn, "2026-08-16T09:00:00+00:00", session_energy_kwh=3.0,
                       status="DISCONNECTED", power_w=0.0)
    conn.commit()
    session = electricityapp.easee_current_session(conn, _flat_opts(), "DK2", "EH1")
    assert session["total_power_w"] == 0.0
    assert session["measured_at"] == "2026-08-16T09:00:00+00:00"


def test_charging_with_no_power_is_reported_as_paused_with_the_reason(conn):
    """The original complaint, end to end: the card said CHARGING next to
    0.00 kW because chargerOpMode alone was trusted."""
    _seed_flat_day(conn)
    _seed_easee_sample(conn, "2026-08-16T08:00:00+00:00", session_energy_kwh=0.0)
    _seed_easee_sample(conn, "2026-08-16T08:30:00+00:00", session_energy_kwh=8.0)
    _seed_easee_sample(conn, "2026-08-16T09:00:00+00:00", session_energy_kwh=8.0, power_w=0.0, reason=81)
    conn.commit()
    session = electricityapp.easee_current_session(conn, _flat_opts(), "DK2", "EH1")
    assert session["raw_status"] == "CHARGING"   # what Easee said
    assert session["status"] == "PAUSED"          # what is actually happening
    assert session["charging"] is False
    assert session["reason"] == "limited by EV"


def test_an_active_charge_is_not_second_guessed(conn):
    _seed_flat_day(conn)
    _seed_easee_sample(conn, "2026-08-16T08:00:00+00:00", session_energy_kwh=0.0)
    _seed_easee_sample(conn, "2026-08-16T08:30:00+00:00", session_energy_kwh=3.0, power_w=7200.0)
    conn.commit()
    session = electricityapp.easee_current_session(conn, _flat_opts(), "DK2", "EH1")
    assert session["status"] == "CHARGING"
    assert session["charging"] is True
    assert session["reason"] is None


def test_two_charges_split_by_unplugging_are_costed_separately(conn):
    """Both start at 0, so no decrease ever occurs — only the unplug separates
    them, and without it the two would be billed as one."""
    _seed_flat_day(conn)
    _seed_easee_sample(conn, "2026-08-16T08:00:00+00:00", session_energy_kwh=0.0)
    _seed_easee_sample(conn, "2026-08-16T08:30:00+00:00", session_energy_kwh=10.0)
    _seed_easee_sample(conn, "2026-08-16T09:00:00+00:00", session_energy_kwh=10.0,
                       status="DISCONNECTED", power_w=0.0)
    _seed_easee_sample(conn, "2026-08-16T10:00:00+00:00", session_energy_kwh=0.0)
    _seed_easee_sample(conn, "2026-08-16T10:30:00+00:00", session_energy_kwh=2.0)
    conn.commit()
    session = electricityapp.easee_current_session(conn, _flat_opts(), "DK2", "EH1")
    assert session["session_energy_kwh"] == 2.0
    assert session["session_cost_dkk"] == round(2.0 * 1.2, 2)


def test_offline_does_not_end_a_session(conn):
    """OFFLINE means the charger is unreachable, which says nothing about
    whether the cable is still in."""
    _seed_flat_day(conn)
    _seed_easee_sample(conn, "2026-08-16T08:00:00+00:00", session_energy_kwh=0.0)
    _seed_easee_sample(conn, "2026-08-16T08:30:00+00:00", session_energy_kwh=2.0, status="OFFLINE", power_w=None)
    _seed_easee_sample(conn, "2026-08-16T09:00:00+00:00", session_energy_kwh=5.0)
    conn.commit()
    session = electricityapp.easee_current_session(conn, _flat_opts(), "DK2", "EH1")
    assert session["session_energy_kwh"] == 5.0
    assert session["session_started_at"] == "2026-08-16T08:00:00+00:00"


def test_migration_adds_the_reason_column_to_an_existing_database(conn, tmp_path):
    """An installed add-on upgrades in place; CREATE TABLE IF NOT EXISTS would
    leave the new column missing for everyone who already has a database."""
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(str(path))
    old.execute(
        "CREATE TABLE easee_samples (ts_utc TEXT NOT NULL, charger_id TEXT NOT NULL, status TEXT, "
        "session_energy_kwh REAL, total_power_w REAL, fetched_at TEXT NOT NULL, "
        "PRIMARY KEY (ts_utc, charger_id))"
    )
    old.execute(
        "INSERT INTO easee_samples VALUES ('2026-08-16T08:00:00+00:00', 'EH1', 'CHARGING', 1.0, 7200.0, 'x')"
    )
    old.commit()

    electricityapp._migrate_columns(old)
    old.commit()

    columns = {row[1] for row in old.execute("PRAGMA table_info(easee_samples)")}
    assert "reason_for_no_current" in columns
    # No backfill: a sample taken before the column existed has no reason, and
    # inventing 0 ("nothing wrong") would assert something never observed.
    assert old.execute("SELECT reason_for_no_current FROM easee_samples").fetchone()[0] is None
    old.close()


def test_migration_is_idempotent(conn):
    electricityapp._migrate_columns(conn)
    electricityapp._migrate_columns(conn)
    columns = [row[1] for row in conn.execute("PRAGMA table_info(easee_samples)")]
    assert columns.count("reason_for_no_current") == 1


# --- Charging history ---


def _charge(conn, day, start_hour, kwh_steps, charger_id="EH1"):
    """One charge: samples climbing through kwh_steps, then an unplug."""
    for i, kwh in enumerate(kwh_steps):
        _seed_easee_sample(
            conn, f"2026-08-{day:02d}T{start_hour + i:02d}:00:00+00:00",
            session_energy_kwh=kwh, charger_id=charger_id,
        )
    _seed_easee_sample(
        conn, f"2026-08-{day:02d}T{start_hour + len(kwh_steps):02d}:00:00+00:00",
        session_energy_kwh=kwh_steps[-1], status="DISCONNECTED", power_w=0.0, charger_id=charger_id,
    )


def _seed_flat_month(conn, price=1.2):
    for day in range(10, 25):
        for hour in range(24):
            for minute in (0, 15, 30, 45):
                _seed_price(conn, f"2026-08-{day:02d}T{hour:02d}:{minute:02d}:00", spot=price)


def _now():
    from datetime import datetime as _dt

    return _dt(2026, 8, 24, 12, 0, tzinfo=electricityapp.LOCAL_TZ)


def test_history_lists_each_charge_separately(conn):
    _seed_flat_month(conn)
    _charge(conn, 20, 8, [0.0, 3.0, 6.0])
    _charge(conn, 22, 9, [0.0, 4.0])
    conn.commit()
    sessions = electricityapp.easee_sessions(conn, _flat_opts(), "DK2", "EH1", days=30, now_local=_now())
    assert [s["energy_kwh"] for s in sessions] == [4.0, 6.0]  # newest first
    assert [s["day"] for s in sessions] == ["2026-08-22", "2026-08-20"]


def test_history_costs_each_session_like_the_live_card_does(conn):
    _seed_flat_month(conn)
    _charge(conn, 20, 8, [0.0, 3.0, 6.0])
    conn.commit()
    session = electricityapp.easee_sessions(conn, _flat_opts(), "DK2", "EH1", days=30, now_local=_now())[0]
    assert session["cost_dkk"] == round(6.0 * 1.2, 2)
    assert session["avg_dkk_kwh"] == 1.2
    assert session["cost_is_partial"] is False


def test_history_reports_duration_and_whether_a_charge_is_still_running(conn):
    _seed_flat_month(conn)
    _charge(conn, 20, 8, [0.0, 3.0, 6.0])  # 08:00 -> 10:00, then unplugged at 11:00
    _seed_easee_sample(conn, "2026-08-23T09:00:00+00:00", session_energy_kwh=0.0)
    _seed_easee_sample(conn, "2026-08-23T10:00:00+00:00", session_energy_kwh=2.0)
    conn.commit()
    sessions = electricityapp.easee_sessions(conn, _flat_opts(), "DK2", "EH1", days=30, now_local=_now())
    assert sessions[0]["ongoing"] is True
    assert sessions[0]["duration_minutes"] == 60
    assert sessions[1]["ongoing"] is False
    assert sessions[1]["duration_minutes"] == 120


def test_history_skips_a_plug_in_that_never_drew_anything(conn):
    _seed_flat_month(conn)
    _seed_easee_sample(conn, "2026-08-20T08:00:00+00:00", session_energy_kwh=0.0, status="AWAITING_START")
    _seed_easee_sample(conn, "2026-08-20T09:00:00+00:00", session_energy_kwh=0.0, status="DISCONNECTED")
    conn.commit()
    assert electricityapp.easee_sessions(conn, _flat_opts(), "DK2", "EH1", days=30, now_local=_now()) == []


def test_history_excludes_sessions_that_ended_before_the_window(conn):
    _seed_flat_month(conn)
    _charge(conn, 12, 8, [0.0, 5.0])
    _charge(conn, 22, 8, [0.0, 3.0])
    conn.commit()
    sessions = electricityapp.easee_sessions(conn, _flat_opts(), "DK2", "EH1", days=3, now_local=_now())
    assert [s["day"] for s in sessions] == ["2026-08-22"]


def test_the_lead_in_stops_the_oldest_session_looking_partial(conn):
    """Without querying a day beyond the window, the first session in range
    starts at the first row and so reads as a charge whose start was never
    seen — marked partial purely because of where the window was cut."""
    _seed_flat_month(conn)
    _charge(conn, 21, 8, [0.0, 4.0])   # ends 21st, just before a 3-day window
    _charge(conn, 22, 8, [0.0, 6.0])
    conn.commit()
    sessions = electricityapp.easee_sessions(conn, _flat_opts(), "DK2", "EH1", days=3, now_local=_now())
    assert all(s["cost_is_partial"] is False for s in sessions)


def test_totals_roll_the_sessions_up(conn):
    _seed_flat_month(conn)
    _charge(conn, 20, 8, [0.0, 6.0])
    _charge(conn, 22, 8, [0.0, 4.0])
    conn.commit()
    sessions = electricityapp.easee_sessions(conn, _flat_opts(), "DK2", "EH1", days=30, now_local=_now())
    totals = electricityapp.easee_charging_totals(sessions)
    assert totals["sessions"] == 2
    assert totals["energy_kwh"] == 10.0
    assert totals["cost_dkk"] == round(10.0 * 1.2, 2)
    assert totals["avg_dkk_kwh"] == 1.2
    assert totals["partial_sessions"] == 0


def test_totals_count_partial_sessions_rather_than_hiding_them(conn):
    """A month total quietly missing some of its cost would look like a cheap
    month; the count is what stops that being invisible."""
    _seed_flat_month(conn)
    # No boundary before this one: the window simply starts mid-charge.
    _seed_easee_sample(conn, "2026-08-20T08:00:00+00:00", session_energy_kwh=20.0)
    _seed_easee_sample(conn, "2026-08-20T09:00:00+00:00", session_energy_kwh=22.0)
    _seed_easee_sample(conn, "2026-08-20T10:00:00+00:00", session_energy_kwh=22.0, status="DISCONNECTED")
    conn.commit()
    sessions = electricityapp.easee_sessions(conn, _flat_opts(), "DK2", "EH1", days=30, now_local=_now())
    totals = electricityapp.easee_charging_totals(sessions)
    assert totals["partial_sessions"] == 1
    assert totals["energy_kwh"] == 22.0
    assert totals["cost_dkk"] == round(2.0 * 1.2, 2)
    # The rate is against what was actually priced, not the full 22 kWh.
    assert totals["avg_dkk_kwh"] == 1.2


def test_empty_history_rolls_up_without_dividing_by_zero(conn):
    totals = electricityapp.easee_charging_totals([])
    assert totals == {"sessions": 0, "energy_kwh": 0, "cost_dkk": None, "avg_dkk_kwh": None,
                      "partial_sessions": 0, "longest_minutes": 0}


def test_daily_rollup_fills_the_days_with_no_charging(conn):
    """A gap has to be a zero: a line drawn straight between the 20th and the
    23rd implies charging on days nothing was plugged in."""
    _seed_flat_month(conn)
    _charge(conn, 20, 8, [0.0, 6.0])
    _charge(conn, 23, 8, [0.0, 4.0])
    conn.commit()
    sessions = electricityapp.easee_sessions(conn, _flat_opts(), "DK2", "EH1", days=30, now_local=_now())
    daily = electricityapp.easee_daily_charging(sessions)
    assert [d["day"] for d in daily] == ["2026-08-20", "2026-08-21", "2026-08-22", "2026-08-23"]
    assert [d["kwh"] for d in daily] == [6.0, 0.0, 0.0, 4.0]
    assert [d["sessions"] for d in daily] == [1, 0, 0, 1]


def test_daily_rollup_sums_two_charges_on_one_day(conn):
    _seed_flat_month(conn)
    _charge(conn, 20, 6, [0.0, 3.0])
    _charge(conn, 20, 14, [0.0, 5.0])
    conn.commit()
    sessions = electricityapp.easee_sessions(conn, _flat_opts(), "DK2", "EH1", days=30, now_local=_now())
    daily = electricityapp.easee_daily_charging(sessions)
    assert len(daily) == 1
    assert daily[0]["kwh"] == 8.0
    assert daily[0]["sessions"] == 2


def test_history_route_shape(conn, client, set_options):
    set_options(easee_enabled=True, easee_username="u", easee_password="p", easee_charger_id="EH1")
    _seed_flat_month(conn)
    _charge(conn, 20, 8, [0.0, 6.0])
    conn.commit()
    data = client.get("/api/easee/history?days=30").get_json()
    assert data["enabled"] is True
    assert len(data["sessions"]) == 1
    assert data["totals"]["energy_kwh"] == 6.0
    assert data["daily"][0]["kwh"] == 6.0


def test_history_route_is_quiet_when_easee_is_off(client):
    """Quiet, but the same shape as when it is on — a caller reading
    data.monthly should get an empty roll-up, not undefined."""
    data = client.get("/api/easee/history").get_json()
    assert data == {
        "enabled": False, "sessions": [], "daily": [], "totals": None,
        "monthly": {"months": [], "average": None},
    }


# --- A session is the stretch where energy actually moved ---


def test_idle_time_with_the_cable_in_is_not_charging_time(conn):
    """A car left plugged in for days used to report a 159-hour session: the
    counter holds its final value, so nothing closed the run."""
    _seed_flat_month(conn)
    _seed_easee_sample(conn, "2026-08-20T08:00:00+00:00", session_energy_kwh=0.0, power_w=0.0,
                       status="AWAITING_START")
    _seed_easee_sample(conn, "2026-08-20T09:00:00+00:00", session_energy_kwh=5.0)
    _seed_easee_sample(conn, "2026-08-20T10:00:00+00:00", session_energy_kwh=9.0)
    for hour in range(11, 24):  # plugged in, drawing nothing, for the rest of the day
        _seed_easee_sample(conn, f"2026-08-20T{hour:02d}:00:00+00:00", session_energy_kwh=9.0,
                           status="COMPLETED", power_w=0.0)
    conn.commit()

    session = electricityapp.easee_sessions(conn, _flat_opts(), "DK2", "EH1", days=30, now_local=_now())[0]
    assert session["energy_kwh"] == 9.0
    assert session["duration_minutes"] == 120  # 08:00 -> 10:00, not 08:00 -> 23:00
    assert session["started_at"] == "2026-08-20T08:00:00+00:00"
    assert session["ended_at"] == "2026-08-20T10:00:00+00:00"


def test_waiting_before_a_charge_is_not_charging_time_either(conn):
    _seed_flat_month(conn)
    for hour in range(6, 9):  # plugged in at 06:00, charge starts at 09:00
        _seed_easee_sample(conn, f"2026-08-20T{hour:02d}:00:00+00:00", session_energy_kwh=0.0,
                           status="AWAITING_START", power_w=0.0)
    _seed_easee_sample(conn, "2026-08-20T09:00:00+00:00", session_energy_kwh=4.0)
    conn.commit()
    session = electricityapp.easee_sessions(conn, _flat_opts(), "DK2", "EH1", days=30, now_local=_now())[0]
    assert session["started_at"] == "2026-08-20T08:00:00+00:00"  # the sample the charge began from
    assert session["duration_minutes"] == 60


def test_a_counter_that_never_moved_is_not_listed_as_a_session(conn):
    """The 26.51 kWh row with "cost covers 0.0 kWh": a value left over from a
    charge that happened before the samples begin. Nothing here saw it arrive,
    so it is not a session — and it must not inflate the range's kWh total."""
    _seed_flat_month(conn)
    for hour in range(8, 20):
        _seed_easee_sample(conn, f"2026-08-20T{hour:02d}:00:00+00:00", session_energy_kwh=26.51,
                           status="COMPLETED", power_w=0.0)
    conn.commit()
    assert electricityapp.easee_sessions(conn, _flat_opts(), "DK2", "EH1", days=30, now_local=_now()) == []


def test_a_stale_counter_does_not_hide_a_real_charge_on_the_card(conn):
    """The live card still has to show the charge that happened, not the idle
    counter reading sitting after it."""
    _seed_flat_day(conn)
    _seed_easee_sample(conn, "2026-08-16T07:00:00+00:00", session_energy_kwh=0.0)
    _seed_easee_sample(conn, "2026-08-16T08:00:00+00:00", session_energy_kwh=6.0)
    _seed_easee_sample(conn, "2026-08-16T09:00:00+00:00", session_energy_kwh=6.0,
                       status="DISCONNECTED", power_w=0.0)
    for hour in range(10, 14):  # a new plug-in whose counter never moves
        _seed_easee_sample(conn, f"2026-08-16T{hour:02d}:00:00+00:00", session_energy_kwh=2.0,
                           status="AWAITING_START", power_w=0.0)
    conn.commit()
    session = electricityapp.easee_current_session(conn, _flat_opts(), "DK2", "EH1")
    assert session["session_energy_kwh"] == 6.0        # the charge we watched
    assert session["status"] == "AWAITING_START"        # what it is doing now


def test_a_paused_and_resumed_charge_stays_one_session(conn):
    """Not split on idle: the counter is cumulative, so a second segment would
    report the whole session's energy as its own and double-count it."""
    _seed_flat_month(conn)
    _seed_easee_sample(conn, "2026-08-20T08:00:00+00:00", session_energy_kwh=0.0)
    _seed_easee_sample(conn, "2026-08-20T09:00:00+00:00", session_energy_kwh=5.0)
    for hour in range(10, 14):  # a long car-side pause, cable still in
        _seed_easee_sample(conn, f"2026-08-20T{hour:02d}:00:00+00:00", session_energy_kwh=5.0, power_w=0.0)
    _seed_easee_sample(conn, "2026-08-20T14:00:00+00:00", session_energy_kwh=8.0)
    conn.commit()
    sessions = electricityapp.easee_sessions(conn, _flat_opts(), "DK2", "EH1", days=30, now_local=_now())
    assert len(sessions) == 1
    assert sessions[0]["energy_kwh"] == 8.0  # not 5 + 8
    assert sessions[0]["duration_minutes"] == 360


# --- A charger whose numbers have frozen ---
#
# Shaped from a real database: Easee reported CHARGING at 10.64 kW for 158
# hours straight with sessionEnergy unchanged at 26.510 — 1,677 kWh, had it been
# real, through one car. Easee serves a charger's last known state when it
# cannot reach it, with nothing to say it is doing so.


def _frozen_run(conn, hours=6, kwh=26.51, power_w=10643.0, start_hour=8):
    """Samples five minutes apart with the counter and power both unmoving."""
    for i in range(hours * 12):
        minute = i * 5
        ts = f"2026-08-16T{start_hour + minute // 60:02d}:{minute % 60:02d}:00+00:00"
        _seed_easee_sample(conn, ts, session_energy_kwh=kwh, power_w=power_w)
    conn.commit()


def test_a_frozen_reading_is_not_reported_as_charging(conn):
    _seed_flat_day(conn)
    _frozen_run(conn)
    session = electricityapp.easee_current_session(conn, _flat_opts(), "DK2", "EH1")
    assert session["raw_status"] == "CHARGING"   # what Easee said
    assert session["status"] == "STALE"           # what is actually knowable
    assert session["charging"] is False


def test_the_stale_report_says_why_it_cannot_be_true(conn):
    _seed_flat_day(conn)
    _frozen_run(conn, hours=6)
    stale = electricityapp.easee_current_session(conn, _flat_opts(), "DK2", "EH1")["stale_reading"]
    assert stale["claimed_kw"] == 10.64
    assert stale["hours"] >= 5.9
    # 10.64 kW for ~6 hours is ~64 kWh that the counter never recorded.
    assert stale["expected_kwh"] > 60


def test_genuine_charging_is_never_called_stale(conn):
    """The counter moving is the whole difference, and a real charge moves it
    on every poll."""
    _seed_flat_day(conn)
    for i in range(72):
        minute = i * 5
        ts = f"2026-08-16T{8 + minute // 60:02d}:{minute % 60:02d}:00+00:00"
        _seed_easee_sample(conn, ts, session_energy_kwh=round(i * 0.887, 3), power_w=10643.0)
    conn.commit()
    session = electricityapp.easee_current_session(conn, _flat_opts(), "DK2", "EH1")
    assert session["status"] == "CHARGING"
    assert session["stale_reading"] is None


def test_a_trickle_charge_is_not_called_stale(conn):
    """Scaled by the claimed power, not a fixed window: 60 W over ten minutes is
    0.01 kWh, which could round away in the counter and proves nothing."""
    _seed_flat_day(conn)
    for i in range(3):
        _seed_easee_sample(conn, f"2026-08-16T08:{i * 5:02d}:00+00:00",
                           session_energy_kwh=5.0, power_w=60.0)
    conn.commit()
    assert electricityapp.easee_current_session(conn, _flat_opts(), "DK2", "EH1")["stale_reading"] is None


def test_an_idle_charger_is_not_called_stale(conn):
    """Nothing plugged in: the counter is meant to sit still."""
    _seed_flat_day(conn)
    for i in range(24):
        minute = i * 5
        ts = f"2026-08-16T{8 + minute // 60:02d}:{minute % 60:02d}:00+00:00"
        _seed_easee_sample(conn, ts, session_energy_kwh=4.0, status="DISCONNECTED", power_w=0.0)
    conn.commit()
    assert electricityapp.easee_current_session(conn, _flat_opts(), "DK2", "EH1")["stale_reading"] is None


def test_a_frozen_run_contributes_no_energy_to_the_history(conn):
    """The session logic already trims it, since no energy ever moved — this
    pins that, because a frozen week must never become a 1,677 kWh session."""
    _seed_flat_day(conn)
    _frozen_run(conn, hours=6)
    sessions = electricityapp.easee_sessions(conn, _flat_opts(), "DK2", "EH1", days=30,
                                             now_local=_now())
    assert sessions == []
