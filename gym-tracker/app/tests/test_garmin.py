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


# --- Body Battery sources --------------------------------------------------


class _BBClient:
    """A Garmin client with configurable Body Battery responses."""

    def __init__(self, summary=None, series=None):
        self._summary = summary if summary is not None else {}
        self._series = series if series is not None else []

    def get_user_summary(self, day):
        return self._summary

    def get_body_battery(self, start, end=None):
        return self._series


def test_body_battery_read_from_the_daily_summary():
    client = _BBClient(
        summary={
            "bodyBatteryHighestValue": 92,
            "bodyBatteryLowestValue": 24,
            "bodyBatteryChargedValue": 61,
            "bodyBatteryDrainedValue": 48,
        }
    )
    assert gymapp.garmin_client._body_battery_fields(client, "2026-07-29") == {
        "body_battery_high": 92,
        "body_battery_low": 24,
        "body_battery_charged": 61,
        "body_battery_drained": 48,
    }


def test_body_battery_falls_back_to_the_series_when_the_summary_is_empty():
    # The historical row layout: [timestamp, status, level, version].
    client = _BBClient(
        summary={},
        series=[
            {
                "charged": 55,
                "drained": 40,
                "bodyBatteryValuesArray": [
                    [1785300000000, "MEASURED", 30, 1.0],
                    [1785303600000, "MEASURED", 88, 1.0],
                ],
            }
        ],
    )
    fields = gymapp.garmin_client._body_battery_fields(client, "2026-07-29")
    assert fields["body_battery_high"] == 88 and fields["body_battery_low"] == 30
    assert fields["body_battery_charged"] == 55 and fields["body_battery_drained"] == 40


def test_body_battery_series_honours_the_declared_level_column():
    # A layout that would have been read as the version number before.
    client = _BBClient(
        summary={},
        series=[
            {
                "bodyBatteryValueDescriptorDTOList": [
                    {"bodyBatteryValueDescriptorIndex": 0, "bodyBatteryValueDescriptorKey": "timestamp"},
                    {"bodyBatteryValueDescriptorIndex": 1, "bodyBatteryValueDescriptorKey": "bodyBatteryLevel"},
                ],
                "bodyBatteryValuesArray": [[1785300000000, 41], [1785303600000, 77]],
            }
        ],
    )
    fields = gymapp.garmin_client._body_battery_fields(client, "2026-07-29")
    assert fields["body_battery_high"] == 77 and fields["body_battery_low"] == 41


def test_body_battery_survives_a_source_that_raises():
    class _Broken(_BBClient):
        def get_user_summary(self, day):
            raise RuntimeError("garmin said no")

    client = _Broken(series=[{"bodyBatteryValuesArray": [[1, "MEASURED", 50, 1.0]]}])
    assert gymapp.garmin_client._body_battery_fields(client, "2026-07-29")["body_battery_high"] == 50

    class _AllBroken(_BBClient):
        def get_user_summary(self, day):
            raise RuntimeError("nope")

        def get_body_battery(self, start, end=None):
            raise RuntimeError("nope")

    assert gymapp.garmin_client._body_battery_fields(_AllBroken(), "2026-07-29") == {}


def test_diagnose_endpoint_reports_both_sources(client, monkeypatch):
    monkeypatch.setattr(gymapp.garmin_client, "is_connected", lambda: True)
    monkeypatch.setattr(
        gymapp.garmin_client,
        "get_client",
        lambda: _BBClient(
            summary={"bodyBatteryHighestValue": 90, "bodyBatteryLowestValue": 20, "steps": 1},
            series=[{"bodyBatteryValuesArray": [[1785300000000, "MEASURED", 44, 1.0]]}],
        ),
    )
    data = client.get("/api/garmin/diagnose?day=2026-07-29").get_json()
    assert data["from_summary"] == {"body_battery_high": 90, "body_battery_low": 20}
    assert data["from_series"]["body_battery_high"] == 44
    assert data["summary_body_battery_keys"] == ["bodyBatteryHighestValue", "bodyBatteryLowestValue"]
    assert data["series_sample_row"] == [1785300000000, "MEASURED", 44, 1.0]


def test_backfill_revisits_days_that_are_missing_a_metric(conn, monkeypatch):
    from datetime import date, timedelta

    # An old day stored back when Body Battery wasn't being parsed.
    old_day = (date.today() - timedelta(days=20)).isoformat()
    conn.execute(
        "INSERT INTO garmin_daily (day, sleep_seconds, stress_avg, synced_at) VALUES (?,?,?,?)",
        (old_day, 26000, 31, "2026-07-01T06:00:00"),
    )
    conn.commit()

    _patch_data(monkeypatch, {"body_battery_high": 88}, [], days_with_data={old_day})
    # The backfill works outwards a batch at a time, so a day 20 back is
    # reached after a few passes rather than on the first one.
    for _ in range(10):
        gymapp._garmin_do_sync(conn)
        if conn.execute(
            "SELECT body_battery_high FROM garmin_daily WHERE day = ?", (old_day,)
        ).fetchone()["body_battery_high"] is not None:
            break
    else:
        raise AssertionError("backfill never reached the incomplete day")

    row = conn.execute("SELECT * FROM garmin_daily WHERE day = ?", (old_day,)).fetchone()
    assert row["body_battery_high"] == 88  # the gap filled in
    assert row["sleep_seconds"] == 26000 and row["stress_avg"] == 31  # and nothing was lost


# --- Heart rate for logged exercises ---------------------------------------


def _hr_series(day, samples):
    """Garmin-shaped daily heart rate: [[epoch_ms, bpm], ...]."""
    from datetime import datetime

    out = []
    for hhmm, bpm in samples:
        at = datetime.fromisoformat(f"{day}T{hhmm}:00")
        out.append([int(at.timestamp() * 1000), bpm])
    return out


def _patch_hr(monkeypatch, series_by_day, device_upload=None):
    monkeypatch.setattr(gymapp.garmin_client, "is_connected", lambda: True)
    monkeypatch.setattr(gymapp.garmin_client, "get_client", lambda: _FakeClient())
    monkeypatch.setattr(gymapp.garmin_client, "fetch_day", lambda client, day: {})
    monkeypatch.setattr(gymapp.garmin_client, "fetch_activities", lambda client, s, e: [])
    monkeypatch.setattr(gymapp.garmin_client, "device_last_upload", lambda client: device_upload)

    def _series(client, day):
        raw = series_by_day.get(day, [])
        return [(row[0] / 1000.0, row[1]) for row in raw]

    monkeypatch.setattr(gymapp.garmin_client, "fetch_heart_rate_series", _series)


def _log_exercise(conn, ts, duration_sec=None, ts_exact=1):
    conn.execute("INSERT OR IGNORE INTO exercises (id, name) VALUES (1, 'Push-up')")
    cur = conn.execute(
        "INSERT INTO workout_logs (ts, exercise_id, sets, reps, duration_sec, source, ts_exact) "
        "VALUES (?, 1, 3, 15, ?, 'manual', ?)",
        (ts, duration_sec, ts_exact),
    )
    conn.commit()
    return cur.lastrowid


def test_heart_rate_is_taken_from_the_window_before_the_log(conn, monkeypatch):
    from datetime import date

    day = date.today().isoformat()
    # Logged at 18:42 with a 12-minute duration -> the window is 18:30-18:42.
    wid = _log_exercise(conn, f"{day}T18:42:00", duration_sec=12 * 60)
    _patch_hr(
        monkeypatch,
        {day: _hr_series(day, [("18:20", 68), ("18:32", 118), ("18:36", 141), ("18:40", 134), ("18:50", 96)])},
    )

    result = gymapp._garmin_do_sync(conn)
    assert result["heart_rates"] == 1

    row = conn.execute("SELECT * FROM workout_logs WHERE id = ?", (wid,)).fetchone()
    assert row["hr_max"] == 141  # the 18:20 and 18:50 samples are outside the window
    assert row["hr_min"] == 118
    assert row["hr_avg"] == round((118 + 141 + 134) / 3)
    assert row["hr_samples"] == 3


def test_heart_rate_window_falls_back_to_the_configured_default(conn, monkeypatch, set_options):
    from datetime import date

    set_options(garmin_hr_window_minutes=15)
    day = date.today().isoformat()
    wid = _log_exercise(conn, f"{day}T19:00:00")  # no duration on the entry
    _patch_hr(
        monkeypatch,
        {day: _hr_series(day, [("18:40", 60), ("18:50", 120), ("18:58", 130)])},
    )
    gymapp._garmin_do_sync(conn)

    row = conn.execute("SELECT * FROM workout_logs WHERE id = ?", (wid,)).fetchone()
    assert row["hr_samples"] == 2  # 18:45 onwards, so the 18:40 sample is out
    assert row["hr_avg"] == 125


def test_heart_rate_backfills_once_the_watch_syncs(conn, monkeypatch):
    from datetime import date, timedelta

    # Logged two days ago; the watch hadn't uploaded, so Garmin had nothing.
    day = (date.today() - timedelta(days=2)).isoformat()
    wid = _log_exercise(conn, f"{day}T07:30:00", duration_sec=10 * 60)
    _patch_hr(monkeypatch, {}, device_upload="2026-07-27T08:00:00")
    gymapp._garmin_do_sync(conn)
    assert conn.execute(
        "SELECT hr_avg FROM workout_logs WHERE id = ?", (wid,)
    ).fetchone()["hr_avg"] is None

    # The watch syncs; the samples for that morning show up and are picked up.
    _patch_hr(
        monkeypatch,
        {day: _hr_series(day, [("07:15", 62), ("07:20", 121), ("07:25", 145)])},
        device_upload="2026-07-29T20:00:00",
    )
    result = gymapp._garmin_do_sync(conn)

    assert result["heart_rates"] == 1
    row = conn.execute("SELECT * FROM workout_logs WHERE id = ?", (wid,)).fetchone()
    assert row["hr_avg"] == 133 and row["hr_max"] == 145


def test_heart_rate_gives_up_until_the_watch_uploads_again(conn, monkeypatch):
    from datetime import date

    day = date.today().isoformat()
    _log_exercise(conn, f"{day}T09:00:00")
    calls = []

    def _series(client, cdate):
        calls.append(cdate)
        return []

    _patch_hr(monkeypatch, {}, device_upload="2026-07-27T08:00:00")
    monkeypatch.setattr(gymapp.garmin_client, "fetch_heart_rate_series", _series)

    for _ in range(gymapp.GARMIN_HR_ATTEMPTS):
        gymapp._garmin_do_sync(conn)
    calls.clear()
    gymapp._garmin_do_sync(conn)
    assert calls == []  # exhausted, waiting on the watch

    monkeypatch.setattr(gymapp.garmin_client, "device_last_upload", lambda client: "2026-07-29T21:00:00")
    gymapp._garmin_do_sync(conn)
    assert calls  # a new upload reopens it


def test_a_single_stray_sample_is_not_a_heart_rate(conn, monkeypatch):
    from datetime import date

    day = date.today().isoformat()
    wid = _log_exercise(conn, f"{day}T20:00:00", duration_sec=10 * 60)
    _patch_hr(monkeypatch, {day: _hr_series(day, [("19:55", 130)])})
    gymapp._garmin_do_sync(conn)

    assert conn.execute(
        "SELECT hr_avg FROM workout_logs WHERE id = ?", (wid,)
    ).fetchone()["hr_avg"] is None


def test_ticking_a_challenge_item_records_the_real_time(client, conn):
    from datetime import datetime

    conn.execute("INSERT OR IGNORE INTO exercises (id, name) VALUES (1, 'Push-up')")
    conn.execute(
        "INSERT INTO challenge_items (id, sort_order, label, item_type, exercise_id, target_sets, target_reps) "
        "VALUES (99, 1, 'Push-up', 'exercise', 1, 3, 15)"
    )
    conn.commit()

    assert client.post("/api/challenge/toggle", json={"item_id": 99}).status_code == 200

    ts = conn.execute("SELECT ts FROM challenge_completions WHERE item_id = 99").fetchone()["ts"]
    logged = conn.execute(
        "SELECT ts FROM workout_logs WHERE challenge_item_id = 99"
    ).fetchone()["ts"]
    # Not the old hardcoded midday: the window for heart rate depends on this.
    assert not ts.endswith("T12:00:00")
    assert logged == ts
    assert abs((datetime.now() - datetime.fromisoformat(ts)).total_seconds()) < 60


def test_ticking_an_earlier_day_keeps_the_midday_placeholder(client, conn):
    conn.execute("INSERT OR IGNORE INTO exercises (id, name) VALUES (1, 'Push-up')")
    conn.execute(
        "INSERT INTO challenge_items (id, sort_order, label, item_type, exercise_id) "
        "VALUES (98, 1, 'Push-up', 'exercise', 1)"
    )
    conn.commit()

    client.post("/api/challenge/toggle", json={"item_id": 98, "day": "2026-07-01"})
    ts = conn.execute("SELECT ts FROM challenge_completions WHERE item_id = 98").fetchone()["ts"]
    assert ts == "2026-07-01T12:00:00"  # can't know when, so don't pretend


def test_heart_rate_is_skipped_when_the_time_is_only_a_placeholder(conn, monkeypatch):
    from datetime import date

    # An entry filed against a past day carries a midday placeholder, so there
    # is no real window — a heart rate here would be invented.
    day = date.today().isoformat()
    wid = _log_exercise(conn, f"{day}T12:00:00", duration_sec=30 * 60, ts_exact=0)
    _patch_hr(
        monkeypatch,
        {day: _hr_series(day, [("11:40", 120), ("11:50", 130), ("11:58", 140)])},
    )
    result = gymapp._garmin_do_sync(conn)

    assert result["heart_rates"] == 0
    assert conn.execute(
        "SELECT hr_avg FROM workout_logs WHERE id = ?", (wid,)
    ).fetchone()["hr_avg"] is None


def test_backfill_horizon_is_configurable(conn, monkeypatch, set_options):
    from datetime import date, timedelta

    # Reach back further than the 60-day default, to pull in history from
    # before the add-on was installed.
    set_options(garmin_backfill_days=200)
    today = date.today()
    old_day = (today - timedelta(days=150)).isoformat()
    _patch_data(monkeypatch, {"sleep_seconds": 25000}, [], days_with_data={old_day})

    for _ in range(60):  # it walks outwards a batch at a time
        gymapp._garmin_do_sync(conn)
        if conn.execute("SELECT COUNT(*) FROM garmin_daily WHERE day = ?", (old_day,)).fetchone()[0]:
            break
    else:
        raise AssertionError("the horizon never reached 150 days back")

    assert gymapp.get_garmin_config()["backfill_days"] == 200


def test_backfill_horizon_is_clamped(set_options):
    set_options(garmin_backfill_days=99999)
    assert gymapp.get_garmin_config()["backfill_days"] == 730
    set_options(garmin_backfill_days="nonsense")
    assert gymapp.get_garmin_config()["backfill_days"] == gymapp.GARMIN_BACKFILL_DAYS


def test_default_horizon_does_not_reach_beyond_sixty_days(conn, monkeypatch):
    from datetime import date, timedelta

    today = date.today()
    old_day = (today - timedelta(days=150)).isoformat()
    _patch_data(monkeypatch, {"sleep_seconds": 25000}, [], days_with_data={old_day})
    for _ in range(30):
        gymapp._garmin_do_sync(conn)
    assert conn.execute(
        "SELECT COUNT(*) FROM garmin_daily WHERE day = ?", (old_day,)
    ).fetchone()[0] == 0


# --- Sessions ---------------------------------------------------------------


def test_one_heart_rate_window_covers_the_whole_session(conn, monkeypatch):
    from datetime import date

    day = date.today().isoformat()
    # Three exercises logged over 20 minutes: one session, one window.
    a = _log_exercise(conn, f"{day}T18:45:00", duration_sec=15 * 60)
    b = _log_exercise(conn, f"{day}T18:55:00")
    c = _log_exercise(conn, f"{day}T19:05:00")
    _patch_hr(monkeypatch, {day: _hr_series(day, [
        ("18:20", 70),   # before the session
        ("18:35", 120), ("18:45", 141), ("18:55", 150), ("19:05", 132),
        ("19:30", 80),   # after it
    ])})

    gymapp._garmin_do_sync(conn)

    rows = {r["id"]: r for r in conn.execute(
        "SELECT id, hr_avg, hr_max, session_start, session_end FROM workout_logs"
    )}
    # Session runs 18:30 (15 min before the first) to 19:05 (the last log).
    assert rows[a]["session_start"] == f"{day}T18:30:00"
    assert rows[a]["session_end"] == f"{day}T19:05:00"
    # Every exercise in it reports the session's heart rate, not its own slice.
    assert rows[a]["hr_max"] == rows[b]["hr_max"] == rows[c]["hr_max"] == 150
    assert rows[a]["hr_avg"] == round((120 + 141 + 150 + 132) / 4)


def test_exercises_far_apart_are_separate_sessions(conn, monkeypatch):
    from datetime import date

    day = date.today().isoformat()
    morning = _log_exercise(conn, f"{day}T07:30:00", duration_sec=10 * 60)
    evening = _log_exercise(conn, f"{day}T19:30:00", duration_sec=10 * 60)
    _patch_hr(monkeypatch, {day: _hr_series(day, [
        ("07:22", 110), ("07:28", 118), ("19:22", 145), ("19:28", 152),
    ])})

    gymapp._garmin_do_sync(conn)

    rows = {r["id"]: r for r in conn.execute("SELECT id, hr_avg, hr_max FROM workout_logs")}
    assert rows[morning]["hr_max"] == 118
    assert rows[evening]["hr_max"] == 152  # not merged into one all-day window


def test_sessions_endpoint_groups_and_totals(client, conn, monkeypatch):
    from datetime import date

    day = date.today().isoformat()
    _log_exercise(conn, f"{day}T18:45:00", duration_sec=15 * 60)
    _log_exercise(conn, f"{day}T19:05:00")
    _patch_hr(monkeypatch, {day: _hr_series(day, [("18:35", 120), ("19:00", 140)])})
    gymapp._garmin_do_sync(conn)

    sessions = client.get("/api/sessions?days=7").get_json()
    assert len(sessions) == 1
    s = sessions[0]
    assert len(s["exercises"]) == 2
    assert s["minutes"] == 35  # 18:30 to 19:05, the time under load
    assert s["reps"] == 3 * 15 * 2
    assert s["hr_avg"] == 130 and s["hr_max"] == 140


def test_sessions_ignore_entries_with_a_placeholder_time(client, conn):
    from datetime import date

    day = date.today().isoformat()
    _log_exercise(conn, f"{day}T12:00:00", ts_exact=0)
    # Nothing to group: a midday placeholder says nothing about what was done
    # alongside what.
    assert client.get("/api/sessions").get_json() == []
