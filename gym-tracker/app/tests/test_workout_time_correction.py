"""Correcting when a workout happened, and re-reading its heart rate.

The heart-rate window ends at the moment an exercise was logged, which is right
as long as you log as you finish. Tick the last exercise of a session an hour
after leaving the gym and the window covers the journey home: a confident number
about the wrong half-hour. Nothing but the person who was there can know, so
there has to be a way to say — and the reading has to be taken again afterwards.
"""
from datetime import date, datetime, timedelta

import app as gymapp

# Every scenario is anchored to **yesterday**, not today. A correction has to be
# in the past — a window cannot be read over minutes that have not happened —
# so a test using today's 22:12 passes in the evening and fails in the morning.
# Yesterday is in the past at every hour of the day.
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()


def _log(conn, ts, exercise_id=1, source="challenge", item_id=None, ts_exact=1, hr=None):
    conn.execute("INSERT OR IGNORE INTO exercises (id, name) VALUES (?, 'Push-up')",
                 (exercise_id,))
    cur = conn.execute(
        "INSERT INTO workout_logs (ts, exercise_id, sets, reps, source, challenge_item_id, "
        "ts_exact, hr_avg, hr_max, hr_samples, hr_scope, hr_attempts) "
        "VALUES (?, ?, 3, 8, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ts, exercise_id, source, item_id, ts_exact,
         hr, (hr + 10) if hr else None, 16 if hr else None,
         "exercise" if hr else None, 3 if hr else 0))
    conn.commit()
    return cur.lastrowid


def _row(conn, wid):
    return conn.execute("SELECT * FROM workout_logs WHERE id = ?", (wid,)).fetchone()


# --- the correction itself ---------------------------------------------------


def test_a_time_corrects_the_timestamp(client, conn):
    """The reported case: ticked at 23:48, actually done about 22:12."""
    wid = _log(conn, f"{YESTERDAY}T23:48:00", hr=82)

    res = client.put(f"/api/workouts/{wid}", json={"time": "22:12"})

    assert res.status_code == 200, res.get_json()
    assert _row(conn, wid)["ts"] == f"{YESTERDAY}T22:12:00"


def test_a_stated_time_is_exact(client, conn):
    """A backfilled entry carries a midday placeholder and is marked inexact.
    Somebody telling us when it was replaces that."""
    wid = _log(conn, f"{YESTERDAY}T12:00:00", ts_exact=0)

    client.put(f"/api/workouts/{wid}", json={"time": "19:30"})

    row = _row(conn, wid)
    assert row["ts"] == f"{YESTERDAY}T19:30:00"
    assert row["ts_exact"] == 1, "a stated time is still being treated as a placeholder"


def test_seconds_are_accepted_as_well_as_minutes(client, conn):
    wid = _log(conn, f"{YESTERDAY}T23:48:00")

    client.put(f"/api/workouts/{wid}", json={"time": "22:12:30"})

    assert _row(conn, wid)["ts"] == f"{YESTERDAY}T22:12:30"


def test_a_time_with_a_date_moves_both(client, conn):
    two_days = (date.today() - timedelta(days=2)).isoformat()
    wid = _log(conn, f"{YESTERDAY}T10:00:00")

    client.put(f"/api/workouts/{wid}", json={"date": two_days, "time": "21:00"})

    assert _row(conn, wid)["ts"] == f"{two_days}T21:00:00"


# --- what it does to the heart rate -----------------------------------------


def test_moving_the_time_gives_up_the_heart_rate(client, conn):
    """The reading measured a stretch of clock the entry no longer claims."""
    wid = _log(conn, f"{YESTERDAY}T23:48:00", hr=82)

    res = client.put(f"/api/workouts/{wid}", json={"time": "22:12"})

    row = _row(conn, wid)
    assert row["hr_avg"] is None, "a reading of the wrong half-hour was carried across"
    assert row["hr_max"] is None and row["hr_samples"] is None
    assert row["hr_scope"] is None
    assert row["session_start"] is None and row["session_end"] is None
    assert res.get_json()["heart_rate_will_be_reread"] is True


def test_the_attempt_count_is_reset_so_the_sync_asks_again(client, conn):
    """Three attempts and the sync stops asking. A corrected entry has never
    been asked about at its new time, so the count has to go back to zero or
    the reading never returns."""
    wid = _log(conn, f"{YESTERDAY}T23:48:00", hr=82)
    assert _row(conn, wid)["hr_attempts"] == 3

    client.put(f"/api/workouts/{wid}", json={"time": "22:12"})

    assert _row(conn, wid)["hr_attempts"] == 0


def test_the_corrected_entry_reads_the_window_it_now_names(client, conn, monkeypatch):
    """End to end: correct the time, sync, and the reading is of the session."""
    from test_garmin import _patch_hr, _hr_series

    day = YESTERDAY
    # The session, then the straggler ticked much later from home.
    _log(conn, f"{day}T21:50:00", exercise_id=1)
    late = _log(conn, f"{day}T23:48:00", exercise_id=2, hr=82)
    conn.execute("INSERT OR IGNORE INTO exercises (id, name) VALUES (2, 'Calf raise')")
    conn.commit()
    _patch_hr(monkeypatch, {day: _hr_series(day, [
        ("21:40", 124), ("21:50", 131), ("22:05", 128), ("22:12", 126),
        ("23:30", 60), ("23:45", 58),      # at home, which is what 82 came from
    ])})

    client.put(f"/api/workouts/{late}", json={"time": "22:12"})
    gymapp._garmin_do_sync(conn)

    row = _row(conn, late)
    assert row["hr_avg"] is not None, "the reading never came back"
    assert row["hr_avg"] > 100, (
        f"still reading the quiet period at home ({row['hr_avg']} bpm)"
    )


def test_an_edit_that_does_not_move_the_time_keeps_the_heart_rate(client, conn):
    """Editing a note must not throw a reading away."""
    wid = _log(conn, f"{YESTERDAY}T21:50:00", hr=128)

    res = client.put(f"/api/workouts/{wid}", json={"notes": "felt heavy"})

    assert _row(conn, wid)["hr_avg"] == 128
    assert res.get_json()["heart_rate_will_be_reread"] is False


# --- what it does to the challenge link -------------------------------------


def test_correcting_only_the_time_stays_with_the_challenge(client, conn):
    """It is the same exercise on the same day — not a claim you did something
    else, so the tick still owns it."""
    wid = _log(conn, f"{YESTERDAY}T23:48:00", source="challenge", item_id=77)

    res = client.put(f"/api/workouts/{wid}", json={"time": "22:12"})

    row = _row(conn, wid)
    assert row["source"] == "challenge"
    assert row["challenge_item_id"] == 77
    assert res.get_json()["detached_from_challenge"] is False


def test_moving_it_to_another_day_still_detaches(client, conn):
    """Unchanged behaviour: the row stands for "this item, ticked on this day"."""
    two_days = (date.today() - timedelta(days=2)).isoformat()
    wid = _log(conn, f"{YESTERDAY}T21:50:00", source="challenge", item_id=77)

    res = client.put(f"/api/workouts/{wid}", json={"date": two_days, "time": "21:00"})

    assert _row(conn, wid)["source"] == "manual"
    assert res.get_json()["detached_from_challenge"] is True


# --- refusals ---------------------------------------------------------------


def test_a_malformed_time_is_refused(client, conn):
    wid = _log(conn, f"{YESTERDAY}T21:50:00", hr=128)

    res = client.put(f"/api/workouts/{wid}", json={"time": "about ten"})

    assert res.status_code == 400
    assert "time must be HH:MM" in res.get_json()["error"]
    assert _row(conn, wid)["hr_avg"] == 128, "a refused edit still threw the reading away"


def test_a_time_in_the_future_is_refused(client, conn):
    """A window cannot be read over minutes that have not happened.

    Stated as tomorrow rather than "now plus two hours", which stops being in
    the future when the suite runs late at night.
    """
    wid = _log(conn, f"{YESTERDAY}T09:00:00")
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    res = client.put(f"/api/workouts/{wid}", json={"date": tomorrow, "time": "12:00"})

    assert res.status_code == 400
    assert "future" in res.get_json()["error"]


def test_an_impossible_clock_time_is_refused(client, conn):
    wid = _log(conn, f"{YESTERDAY}T09:00:00")

    assert client.put(f"/api/workouts/{wid}", json={"time": "25:00"}).status_code == 400


def test_omitting_the_time_leaves_the_timestamp_alone(client, conn):
    """The regression this field could most easily cause: every edit that does
    not mention a time must keep the one already recorded."""
    wid = _log(conn, f"{YESTERDAY}T07:41:00")

    client.put(f"/api/workouts/{wid}", json={"sets": 4})

    assert _row(conn, wid)["ts"] == f"{YESTERDAY}T07:41:00"
    assert _row(conn, wid)["sets"] == 4
