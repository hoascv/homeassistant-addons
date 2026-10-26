"""The defensive paths: bad input from a caller, bad rows in the database.

Worth testing rather than writing off as unreachable. This database is restored
from backups, mirrored into a lakehouse and read over a published port by a
pipeline, so a malformed timestamp or a non-numeric query parameter is a thing
that happens — and the difference between degrading and returning a 500 is the
difference between a card that says "no pace yet" and an app that looks broken.
"""
from datetime import date


def _log(conn, ts, exercise_id=1, ts_exact=1, name="Push-up"):
    conn.execute("INSERT OR IGNORE INTO exercises (id, name) VALUES (?, ?)",
                 (exercise_id, name))
    cur = conn.execute(
        "INSERT INTO workout_logs (ts, exercise_id, sets, reps, source, ts_exact) "
        "VALUES (?, ?, 3, 8, 'challenge', ?)", (ts, exercise_id, ts_exact))
    conn.commit()
    return cur.lastrowid


# --- a timestamp nothing can parse ------------------------------------------


def test_the_timing_endpoint_survives_an_unreadable_timestamp(client, conn):
    """A row whose ts matches the day but is not a time.

    It is dropped by _session_groups before a session is built, so the timing
    endpoint never sees it — it answers "nothing to time" rather than raising.
    Written down because the obvious reading is that the endpoint handles this
    itself; it does not, and it does not need to.
    """
    day = date.today().isoformat()
    _log(conn, f"{day}T08:00:00", 1, name="Exercise 1")
    _log(conn, f"{day}Tnot-a-time", 2, name="Exercise 2")

    res = client.get("/api/exercises/2/timing")

    assert res.status_code == 200
    body = res.get_json()
    assert body["measured"] == 0
    assert body["sessions"] == [], "an unreadable row reached the timing loop"


def test_a_readable_exercise_is_still_timed_alongside_a_broken_row(client, conn):
    """The broken row must not cost its neighbours their measurement."""
    day = date.today().isoformat()
    _log(conn, f"{day}T18:00:00", 1, name="Exercise 1")
    _log(conn, f"{day}Tbroken", 3, name="Exercise 3")
    _log(conn, f"{day}T18:12:00", 2, name="Exercise 2")

    body = client.get("/api/exercises/2/timing").get_json()

    assert body["measured"] == 1
    assert body["sessions"][0]["minutes"] == 12.0


def test_the_sessions_endpoint_survives_an_unreadable_timestamp(client, conn):
    day = date.today().isoformat()
    _log(conn, f"{day}T08:00:00")
    _log(conn, f"{day}Tbroken", 2, name="Exercise 2")

    res = client.get("/api/sessions")

    assert res.status_code == 200
    # The session it could read is still reported; the unreadable row cannot
    # extend an extent, so minutes is a number or None, never a crash.
    for s in res.get_json():
        assert s["minutes"] is None or isinstance(s["minutes"], int)


def test_a_session_of_only_unreadable_rows_yields_no_windows():
    """_exercise_windows has nothing to work with and must return empty rather
    than index into a list it never built."""
    import app as gymapp

    rows = [{"id": 1, "ts": "nonsense", "duration_sec": None},
            {"id": 2, "ts": "also nonsense", "duration_sec": None}]
    assert gymapp._exercise_windows(rows, 30) == {}


def test_a_heart_rate_sync_skips_rows_it_cannot_time(conn, monkeypatch):
    """The sync must not abandon a day because one row in it is malformed."""
    import app as gymapp
    from test_garmin import _patch_hr, _hr_series   # the established fakes

    day = date.today().isoformat()
    good = _log(conn, f"{day}T18:45:00", 1, name="Exercise 1")
    _log(conn, f"{day}Tbroken", 2, name="Exercise 2")
    _patch_hr(monkeypatch, {day: _hr_series(day, [("18:20", 120), ("18:40", 140)])})

    gymapp._garmin_do_sync(conn)   # must not raise

    row = conn.execute("SELECT hr_avg FROM workout_logs WHERE id = ?", (good,)).fetchone()
    assert row["hr_avg"] is not None, "a readable row was skipped because of a broken sibling"


# --- query parameters a caller got wrong ------------------------------------


def test_a_non_numeric_days_window_falls_back(client, conn):
    """Rather than 400 on a window: the caller wanted recent data, and a
    default answers that. Both endpoints take the same parameter."""
    day = date.today().isoformat()
    _log(conn, f"{day}T18:00:00", 1, name="Exercise 1")
    _log(conn, f"{day}T18:12:00", 2, name="Exercise 2")

    for path in ("/api/sessions?days=lots", "/api/exercises/2/timing?days=lots"):
        res = client.get(path)
        assert res.status_code == 200, f"{path} → {res.status_code}"

    # And the default window really is in force, not an empty result.
    assert client.get("/api/exercises/2/timing?days=lots").get_json()["measured"] == 1


def test_an_out_of_range_days_window_is_clamped(client, conn):
    day = date.today().isoformat()
    _log(conn, f"{day}T18:00:00", 1, name="Exercise 1")
    _log(conn, f"{day}T18:12:00", 2, name="Exercise 2")

    for path in ("/api/exercises/2/timing?days=0", "/api/exercises/2/timing?days=99999",
                 "/api/sessions?days=-5", "/api/sessions?days=99999"):
        assert client.get(path).status_code == 200, path


# --- the amend endpoint's refusals ------------------------------------------


def _exercise_item(client, conn):
    ex = conn.execute("SELECT id FROM exercises WHERE archived = 0 LIMIT 1").fetchone()["id"]
    r = client.post("/api/challenge/items", json={
        "item_type": "exercise", "exercise_id": ex, "target_sets": 3, "target_reps": 8})
    return r.get_json()["id"]


def test_amending_without_an_item_id_is_refused(client):
    res = client.put("/api/challenge/logged", json={"weight_kg": 80})
    assert res.status_code == 400
    assert "item_id is required" in res.get_json()["error"]


def test_amending_a_non_numeric_item_id_is_refused(client):
    res = client.put("/api/challenge/logged", json={"item_id": "the squat one"})
    assert res.status_code == 400


def test_amending_an_unknown_item_is_refused(client):
    res = client.put("/api/challenge/logged", json={"item_id": 99999, "weight_kg": 80})
    assert res.status_code == 404
    assert "no such challenge item" in res.get_json()["error"]


def test_amending_an_archived_item_is_refused(client, conn):
    """Removing an item archives it; numbers must not reach it afterwards."""
    item_id = _exercise_item(client, conn)
    client.post("/api/challenge/toggle", json={"item_id": item_id})
    client.delete(f"/api/challenge/items/{item_id}")

    res = client.put("/api/challenge/logged", json={"item_id": item_id, "weight_kg": 80})

    assert res.status_code == 404


def test_amending_with_a_malformed_day_is_refused(client, conn):
    item_id = _exercise_item(client, conn)
    res = client.put("/api/challenge/logged",
                     json={"item_id": item_id, "day": "last tuesday", "weight_kg": 80})
    assert res.status_code == 400
    assert "day must be YYYY-MM-DD" in res.get_json()["error"]


def test_amending_a_tick_whose_workout_was_deleted(client, conn):
    """The tick stands but its workout is gone — deleted by hand, or lost in a
    partial restore. The endpoint says so rather than reporting success for an
    update that changed nothing."""
    item_id = _exercise_item(client, conn)
    client.post("/api/challenge/toggle", json={"item_id": item_id})
    conn.execute("DELETE FROM workout_logs WHERE challenge_item_id = ?", (item_id,))
    conn.commit()

    res = client.put("/api/challenge/logged", json={"item_id": item_id, "weight_kg": 80})

    assert res.status_code == 404
    assert "no workout behind it" in res.get_json()["error"]


def test_amending_a_bad_number_is_refused_by_the_amend_path_too(client, conn):
    """The tick path validates; so must this one, with the same message."""
    item_id = _exercise_item(client, conn)
    client.post("/api/challenge/toggle", json={"item_id": item_id})

    res = client.put("/api/challenge/logged", json={"item_id": item_id, "reps": "loads"})

    assert res.status_code == 400
    assert "reps must be a number" in res.get_json()["error"]
