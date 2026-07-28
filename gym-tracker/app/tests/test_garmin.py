"""Tests for the Garmin Connect integration.

The real `garminconnect` library is never imported: every test monkeypatches
`app.garmin_client` so the sync/login logic is exercised without a network or a
Garmin account.
"""
import app as gymapp


# --- Fakes -----------------------------------------------------------------


class _FakeClient:
    pass


def _patch_data(monkeypatch, day_fields, activities):
    """Make garmin_client return canned data for a sync."""
    monkeypatch.setattr(gymapp.garmin_client, "is_connected", lambda: True)
    monkeypatch.setattr(gymapp.garmin_client, "get_client", lambda: _FakeClient())
    monkeypatch.setattr(gymapp.garmin_client, "fetch_day", lambda client, day: dict(day_fields))
    monkeypatch.setattr(gymapp.garmin_client, "fetch_activities", lambda client, s, e: list(activities))


# --- Schema ----------------------------------------------------------------


def test_schema_tables_created(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"garmin_daily", "garmin_activities"}.issubset(tables)


# --- Sync ------------------------------------------------------------------


def test_sync_upserts_and_dedupes(conn, monkeypatch):
    day_fields = {"sleep_seconds": 27000, "stress_avg": 30, "body_battery_high": 90}
    activities = [
        {"activity_id": 1, "start_time": "2026-07-27T07:00:00", "activity_type": "running",
         "name": "Morning Run", "duration_sec": 1800, "distance_m": 5000, "calories": 300,
         "avg_hr": 150, "max_hr": 170},
        {"activity_id": 2, "start_time": "2026-07-28T18:00:00", "activity_type": "strength_training",
         "name": "Gym", "duration_sec": 2400, "distance_m": None, "calories": 250,
         "avg_hr": 120, "max_hr": 140},
    ]
    _patch_data(monkeypatch, day_fields, activities)

    first = gymapp._garmin_do_sync(conn)
    assert first["days"] == gymapp.GARMIN_SYNC_DAYS  # one row per day in the window
    assert first["activities"] == 2

    days_after_first = conn.execute("SELECT COUNT(*) FROM garmin_daily").fetchone()[0]
    acts_after_first = conn.execute("SELECT COUNT(*) FROM garmin_activities").fetchone()[0]

    # A second identical sync must not create duplicates.
    gymapp._garmin_do_sync(conn)
    assert conn.execute("SELECT COUNT(*) FROM garmin_daily").fetchone()[0] == days_after_first
    assert conn.execute("SELECT COUNT(*) FROM garmin_activities").fetchone()[0] == acts_after_first

    # Values are stored and updatable.
    row = conn.execute("SELECT sleep_seconds, stress_avg FROM garmin_daily LIMIT 1").fetchone()
    assert row["sleep_seconds"] == 27000 and row["stress_avg"] == 30
    assert gymapp._get_app_state(conn, "garmin_last_sync")


def test_sync_records_error_and_raises(conn, monkeypatch):
    monkeypatch.setattr(gymapp.garmin_client, "is_connected", lambda: True)

    def _boom():
        raise RuntimeError("garmin exploded")

    monkeypatch.setattr(gymapp.garmin_client, "get_client", _boom)
    try:
        gymapp._garmin_do_sync(conn)
        assert False, "expected the sync to re-raise"
    except RuntimeError:
        pass
    assert gymapp._get_app_state(conn, "garmin_last_error") == "garmin exploded"


# --- Background tick -------------------------------------------------------


def test_tick_respects_interval_and_auto_sync(conn, monkeypatch, set_options):
    calls = []
    monkeypatch.setattr(gymapp.garmin_client, "is_connected", lambda: True)
    monkeypatch.setattr(gymapp, "_garmin_do_sync", lambda c: calls.append(1))

    from datetime import datetime, timedelta

    now = datetime(2026, 7, 28, 12, 0, 0)

    # auto_sync off -> never syncs
    set_options(garmin_auto_sync=False)
    gymapp._garmin_sync_tick(now, conn)
    assert not calls

    # auto_sync on, never synced -> syncs
    set_options(garmin_auto_sync=True, garmin_sync_interval_hours=6)
    gymapp._garmin_sync_tick(now, conn)
    assert len(calls) == 1

    # synced 1h ago -> within interval, skip
    gymapp._set_app_state(conn, "garmin_last_sync", (now - timedelta(hours=1)).isoformat())
    gymapp._garmin_sync_tick(now, conn)
    assert len(calls) == 1

    # synced 7h ago -> past interval, sync again
    gymapp._set_app_state(conn, "garmin_last_sync", (now - timedelta(hours=7)).isoformat())
    gymapp._garmin_sync_tick(now, conn)
    assert len(calls) == 2


# --- Routes ----------------------------------------------------------------


def test_status_shape(client, monkeypatch):
    monkeypatch.setattr(gymapp.garmin_client, "is_connected", lambda: False)
    body = client.get("/api/garmin/status").get_json()
    assert body["connected"] is False
    assert body["auto_sync"] is True
    assert body["interval_hours"] == 6


def test_connect_requires_credentials(client):
    res = client.post("/api/garmin/connect", json={"email": "", "password": ""})
    assert res.status_code == 400


def test_connect_mfa_then_complete(client, monkeypatch):
    monkeypatch.setattr(gymapp.garmin_client, "begin_login",
                        lambda email, password: {"status": "mfa_required"})
    monkeypatch.setattr(gymapp.garmin_client, "complete_mfa",
                        lambda code: {"status": "connected"})

    r1 = client.post("/api/garmin/connect", json={"email": "a@b.com", "password": "pw"})
    assert r1.get_json()["status"] == "mfa_required"

    r2 = client.post("/api/garmin/mfa", json={"code": "123456"})
    assert r2.get_json()["status"] == "connected"


def test_connect_without_mfa(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(gymapp.garmin_client, "begin_login",
                        lambda email, password: seen.update(email=email, password=password) or {"status": "connected"})
    res = client.post("/api/garmin/connect", json={"email": "a@b.com", "password": "secret"})
    assert res.get_json()["status"] == "connected"
    # The password is forwarded to the client layer but never echoed back.
    assert seen["password"] == "secret"
    assert "secret" not in res.get_data(as_text=True)


def test_sync_requires_connection(client, monkeypatch):
    monkeypatch.setattr(gymapp.garmin_client, "is_connected", lambda: False)
    res = client.post("/api/garmin/sync")
    assert res.status_code == 409


def test_sync_route_returns_import_counts(client, monkeypatch):
    _patch_data(monkeypatch, {"sleep_seconds": 100}, [
        {"activity_id": 9, "start_time": "2026-07-28T07:00:00", "activity_type": "running",
         "name": "Run", "duration_sec": 600, "distance_m": 2000, "calories": 100,
         "avg_hr": 130, "max_hr": 150},
    ])
    res = client.post("/api/garmin/sync")
    body = res.get_json()
    assert body["status"] == "ok"
    assert body["imported"]["activities"] == 1


def test_summary_endpoint(client, conn, monkeypatch):
    monkeypatch.setattr(gymapp.garmin_client, "is_connected", lambda: True)
    conn.execute(
        "INSERT INTO garmin_daily (day, sleep_seconds, stress_avg, body_battery_high, body_battery_low) "
        "VALUES ('2026-07-28', 27000, 25, 88, 12)"
    )
    conn.execute(
        "INSERT INTO garmin_activities (activity_id, start_time, activity_type, name, duration_sec) "
        "VALUES (5, '2026-07-28T07:00:00', 'running', 'Run', 1800)"
    )
    conn.commit()

    body = client.get("/api/garmin/summary").get_json()
    assert body["connected"] is True
    assert body["latest"]["sleep_seconds"] == 27000
    assert len(body["activities"]) == 1
    assert body["activities"][0]["name"] == "Run"


def test_disconnect(client, monkeypatch):
    dropped = {"v": False}
    monkeypatch.setattr(gymapp.garmin_client, "disconnect", lambda: dropped.update(v=True))
    res = client.post("/api/garmin/disconnect")
    assert res.get_json()["status"] == "disconnected"
    assert dropped["v"] is True
