"""Tests for the Garmin Connect integration.

The real `garminconnect` library is never imported: every test monkeypatches
`app.garmin_client` so the sync/login logic is exercised without a network or a
Garmin account.
"""
import app as gymapp


# --- Fakes -----------------------------------------------------------------


class _FakeClient:
    pass


def _patch_data(monkeypatch, day_fields, activities, days_with_data=None, device_upload=None):
    """Make garmin_client return canned data for a sync.

    `days_with_data` limits which YYYY-MM-DD strings have anything at all —
    every other day answers the way Garmin does for a day the watch has not
    uploaded: the fields are there but empty.
    """
    empty = {k: None for k in day_fields}

    def _fetch_day(client, day):
        if days_with_data is not None and day not in days_with_data:
            return dict(empty)
        return dict(day_fields)

    monkeypatch.setattr(gymapp.garmin_client, "is_connected", lambda: True)
    monkeypatch.setattr(gymapp.garmin_client, "get_client", lambda: _FakeClient())
    monkeypatch.setattr(gymapp.garmin_client, "fetch_day", _fetch_day)
    monkeypatch.setattr(gymapp.garmin_client, "fetch_activities", lambda client, s, e: list(activities))
    monkeypatch.setattr(gymapp.garmin_client, "device_last_upload", lambda client: device_upload)


def _recent_days(n=gymapp.GARMIN_SYNC_DAYS):
    from datetime import date, timedelta

    today = date.today()
    return {(today - timedelta(days=i)).isoformat() for i in range(n)}


# --- Schema ----------------------------------------------------------------


def test_save_tokens_uses_client_and_is_connected(monkeypatch, tmp_path):
    # Guards the real-library contract: tokens are persisted via garmin.client
    # .dump() (not a .garth attribute) into a single garmin_tokens.json file,
    # and is_connected()/disconnect() key off exactly that file.
    import os

    store = str(tmp_path / "garmin")
    monkeypatch.setattr(gymapp.garmin_client, "TOKENSTORE", store)

    class _Client:
        def dump(self, path):
            os.makedirs(path, exist_ok=True)
            with open(os.path.join(path, "garmin_tokens.json"), "w") as f:
                f.write("{}")

    class _Garmin:
        client = _Client()

    assert gymapp.garmin_client.is_connected() is False
    gymapp.garmin_client._save_tokens(_Garmin())  # would AttributeError on a .garth-based impl
    assert gymapp.garmin_client.is_connected() is True

    gymapp.garmin_client.disconnect()
    assert gymapp.garmin_client.is_connected() is False


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
    _patch_data(monkeypatch, day_fields, activities, days_with_data=_recent_days())

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


# --- An unsynced watch -----------------------------------------------------


def test_empty_day_never_overwrites_stored_data(conn, monkeypatch):
    # A day syncs fine...
    day_fields = {"sleep_seconds": 27000, "stress_avg": 30, "body_battery_high": 90}
    _patch_data(monkeypatch, day_fields, [], days_with_data=_recent_days())
    gymapp._garmin_do_sync(conn)
    stored = conn.execute("SELECT COUNT(*) FROM garmin_daily").fetchone()[0]

    # ...then the watch falls behind and Garmin answers with empty fields.
    _patch_data(monkeypatch, day_fields, [], days_with_data=set())
    gymapp._garmin_do_sync(conn)

    rows = conn.execute("SELECT sleep_seconds, stress_avg FROM garmin_daily").fetchall()
    assert len(rows) == stored  # nothing dropped
    assert all(r["sleep_seconds"] == 27000 and r["stress_avg"] == 30 for r in rows)


def test_partial_day_keeps_the_metrics_that_stopped_coming(conn, monkeypatch):
    _patch_data(
        monkeypatch,
        {"sleep_seconds": 27000, "stress_avg": 30, "body_battery_high": 90},
        [],
        days_with_data=_recent_days(),
    )
    gymapp._garmin_do_sync(conn)

    # Sleep still reports, the other two go empty: the stored values stay.
    _patch_data(
        monkeypatch,
        {"sleep_seconds": 30000, "stress_avg": None, "body_battery_high": None},
        [],
        days_with_data=_recent_days(),
    )
    gymapp._garmin_do_sync(conn)

    row = conn.execute("SELECT * FROM garmin_daily ORDER BY day DESC LIMIT 1").fetchone()
    assert row["sleep_seconds"] == 30000  # updated
    assert row["stress_avg"] == 30 and row["body_battery_high"] == 90  # not erased


def test_day_with_no_data_stores_no_row(conn, monkeypatch):
    _patch_data(monkeypatch, {"sleep_seconds": 1, "stress_avg": 1}, [], days_with_data=set())
    result = gymapp._garmin_do_sync(conn)

    assert result["days"] == 0  # nothing imported, and it doesn't claim otherwise
    assert conn.execute("SELECT COUNT(*) FROM garmin_daily").fetchone()[0] == 0
    # The days are remembered as holes, so they can be chased later.
    assert conn.execute("SELECT COUNT(*) FROM garmin_day_probe").fetchone()[0] > 0


def test_backfills_days_older_than_the_refresh_window(conn, monkeypatch):
    from datetime import date, timedelta

    # The watch was off the charger for a fortnight and has just caught up, so
    # days 7-13 back — outside the refresh window — now have data.
    today = date.today()
    late = {(today - timedelta(days=i)).isoformat() for i in range(7, 14)}
    _patch_data(monkeypatch, {"sleep_seconds": 25000}, [], days_with_data=late)

    result = gymapp._garmin_do_sync(conn)

    assert result["days"] == 0  # nothing in the trailing window
    assert result["backfilled"] == len(late)
    stored = {r["day"] for r in conn.execute("SELECT day FROM garmin_daily")}
    assert stored == late


def test_holes_stop_being_re_asked_until_the_watch_uploads(conn, monkeypatch):
    calls = []

    def _track(client, day):
        calls.append(day)
        return {"sleep_seconds": None}

    monkeypatch.setattr(gymapp.garmin_client, "is_connected", lambda: True)
    monkeypatch.setattr(gymapp.garmin_client, "get_client", lambda: _FakeClient())
    monkeypatch.setattr(gymapp.garmin_client, "fetch_activities", lambda client, s, e: [])
    monkeypatch.setattr(gymapp.garmin_client, "fetch_day", _track)
    monkeypatch.setattr(gymapp.garmin_client, "device_last_upload", lambda client: "2026-07-01T08:00:00")

    # Sync until the backfill has swept the whole window and gone quiet. It
    # walks outwards GARMIN_BACKFILL_MAX days at a time, re-asking each hole
    # GARMIN_PROBE_ATTEMPTS times, so this takes a bounded number of passes.
    for _ in range(200):
        calls.clear()
        gymapp._garmin_do_sync(conn)
        if len(calls) == gymapp.GARMIN_SYNC_DAYS:
            break
    else:
        raise AssertionError("backfill never went quiet")

    # Same watch upload as before: nothing but the trailing window is touched.
    calls.clear()
    gymapp._garmin_do_sync(conn)
    assert len(calls) == gymapp.GARMIN_SYNC_DAYS

    # The watch uploads again -> the holes are worth another look.
    monkeypatch.setattr(gymapp.garmin_client, "device_last_upload", lambda client: "2026-07-20T09:00:00")
    calls.clear()
    gymapp._garmin_do_sync(conn)
    assert len(calls) > gymapp.GARMIN_SYNC_DAYS


def test_status_reports_when_the_watch_last_uploaded(client, conn, monkeypatch):
    _patch_data(
        monkeypatch,
        {"sleep_seconds": 27000},
        [],
        days_with_data=_recent_days(),
        device_upload="2026-07-28T21:14:00",
    )
    gymapp._garmin_do_sync(conn)

    status = client.get("/api/garmin/status").get_json()
    assert status["device_last_upload"] == "2026-07-28T21:14:00"


def test_device_last_upload_parses_garmin_epoch_millis(monkeypatch):
    from datetime import datetime

    class _C:
        def get_device_last_used(self):
            return {"lastUsedDeviceUploadTime": 1785312840000}

    got = gymapp.garmin_client.device_last_upload(_C())
    assert got == datetime.fromtimestamp(1785312840).isoformat(timespec="seconds")

    class _Broken:
        def get_device_last_used(self):
            raise RuntimeError("garmin said no")

    assert gymapp.garmin_client.device_last_upload(_Broken()) is None
