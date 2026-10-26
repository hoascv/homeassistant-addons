"""How long an exercise has been taking, session by session.

Derived from tick times — the stretch from the previous log to this one — so
the two cases that carry no measurement have to be reported as such rather than
quietly averaged in.
"""
from datetime import date, timedelta


def _log(conn, ts, exercise_id=1, name="Push-up"):
    conn.execute("INSERT OR IGNORE INTO exercises (id, name) VALUES (?, ?)",
                 (exercise_id, name))
    cur = conn.execute(
        "INSERT INTO workout_logs (ts, exercise_id, sets, reps, source, ts_exact) "
        "VALUES (?, ?, 3, 8, 'challenge', 1)", (ts, exercise_id))
    conn.commit()
    return cur.lastrowid


def _timing(client, exercise_id=2):
    return client.get(f"/api/exercises/{exercise_id}/timing").get_json()


def _session(conn, day, pairs):
    """pairs: [(hh:mm, exercise_id)] — one session on `day`."""
    for hhmm, ex in pairs:
        _log(conn, f"{day}T{hhmm}:00", ex, f"Exercise {ex}")


def test_the_gap_from_the_previous_log_is_the_measurement(client, conn):
    day = date.today().isoformat()
    _session(conn, day, [("18:00", 1), ("18:12", 2)])

    body = _timing(client)

    assert body["measured"] == 1
    assert body["sessions"][0]["minutes"] == 12.0
    assert body["last_minutes"] == 12.0


def test_the_first_exercise_of_a_session_has_no_measurement(client, conn):
    """Nothing precedes it, so there is no stretch to measure."""
    day = date.today().isoformat()
    _session(conn, day, [("18:00", 2), ("18:12", 1)])

    body = _timing(client)

    assert body["measured"] == 0
    assert body["sessions"][0]["minutes"] is None
    assert body["sessions"][0]["why"] == "first in the session"


def test_exercises_ticked_together_carry_no_time(client, conn):
    """The time between them belongs to neither."""
    day = date.today().isoformat()
    _session(conn, day, [("18:00", 1), ("18:12", 3), ("18:12", 2)])

    body = _timing(client)

    assert body["measured"] == 0
    assert body["sessions"][0]["why"] == "ticked together with the one before it"


def test_a_few_seconds_apart_is_also_ticked_together(client, conn):
    """Found on real data: two rows six seconds apart passed as a measurement
    and took a squat's trend from steady to +7 minutes a session. Someone
    tapping down a list is one act, not two exercises."""
    day = date.today().isoformat()
    conn.execute("INSERT OR IGNORE INTO exercises (id, name) VALUES (3, 'Exercise 3')")
    _log(conn, f"{day}T18:00:00", 1, "Exercise 1")
    _log(conn, f"{day}T18:12:00", 3, "Exercise 3")
    _log(conn, f"{day}T18:12:06", 2, "Exercise 2")   # six seconds later

    body = _timing(client)

    assert body["measured"] == 0
    assert body["sessions"][0]["why"] == "ticked together with the one before it"


def test_just_over_a_minute_is_a_real_measurement(client, conn):
    """The floor has to let a genuinely quick exercise through."""
    day = date.today().isoformat()
    _log(conn, f"{day}T18:00:00", 1, "Exercise 1")
    _log(conn, f"{day}T18:01:30", 2, "Exercise 2")

    assert _timing(client)["measured"] == 1


def test_each_session_is_reported_separately(client, conn):
    today = date.today()
    for offset, gap in ((6, 8), (4, 10), (2, 14)):
        day = (today - timedelta(days=offset)).isoformat()
        _session(conn, day, [("18:00", 1), (f"18:{gap:02d}", 2)])

    body = _timing(client)

    assert body["measured"] == 3
    assert [s["minutes"] for s in body["sessions"]] == [14.0, 10.0, 8.0]  # newest first
    assert body["mean_minutes"] == round((8 + 10 + 14) / 3, 1)


def test_the_trend_says_whether_it_is_taking_longer(client, conn):
    """The question being asked: is this exercise taking me longer than it was?"""
    today = date.today()
    for offset, gap in ((8, 6), (6, 9), (4, 12), (2, 15)):
        day = (today - timedelta(days=offset)).isoformat()
        _session(conn, day, [("18:00", 1), (f"18:{gap:02d}", 2)])

    body = _timing(client)

    assert body["trend_minutes_per_session"] == 3.0  # +3 minutes each time
    assert body["last_minutes"] == 15.0


def test_a_steady_exercise_has_a_flat_trend(client, conn):
    today = date.today()
    for offset in (8, 6, 4, 2):
        day = (today - timedelta(days=offset)).isoformat()
        _session(conn, day, [("18:00", 1), ("18:10", 2)])

    assert _timing(client)["trend_minutes_per_session"] == 0.0


def test_two_sessions_are_too_few_for_a_trend(client, conn):
    """A slope through two points is arithmetic, not evidence."""
    today = date.today()
    for offset, gap in ((4, 8), (2, 20)):
        day = (today - timedelta(days=offset)).isoformat()
        _session(conn, day, [("18:00", 1), (f"18:{gap:02d}", 2)])

    body = _timing(client)

    assert body["measured"] == 2
    assert body["trend_minutes_per_session"] is None


def test_a_long_gap_is_a_separate_session_not_a_long_exercise(client, conn):
    """Morning and evening are two sessions; the evening exercise is not
    reported as having taken eleven hours."""
    day = date.today().isoformat()
    _log(conn, f"{day}T07:30:00", 1, "Exercise 1")
    _log(conn, f"{day}T19:00:00", 2, "Exercise 2")

    body = _timing(client)

    assert body["measured"] == 0
    assert body["sessions"][0]["why"] == "first in the session"


def test_the_window_can_be_narrowed(client, conn):
    today = date.today()
    _session(conn, (today - timedelta(days=200)).isoformat(), [("18:00", 1), ("18:08", 2)])
    _session(conn, (today - timedelta(days=2)).isoformat(), [("18:00", 1), ("18:14", 2)])

    assert _timing(client)["measured"] == 1  # 120-day default excludes the old one
    wide = client.get("/api/exercises/2/timing?days=365").get_json()
    assert wide["measured"] == 2


def test_a_backfilled_entry_is_not_timed(client, conn):
    """A midday placeholder says nothing about when anything happened."""
    day = date.today().isoformat()
    conn.execute("INSERT OR IGNORE INTO exercises (id, name) VALUES (2, 'Exercise 2')")
    conn.execute(
        "INSERT INTO workout_logs (ts, exercise_id, sets, reps, source, ts_exact) "
        "VALUES (?, 2, 3, 8, 'challenge', 0)", (f"{day}T12:00:00",))
    conn.commit()

    assert _timing(client)["sessions"] == []


def test_no_such_exercise(client):
    assert client.get("/api/exercises/9999/timing").status_code == 404


def test_it_says_the_number_includes_rest(client, conn):
    """The UI has to be able to label it honestly, so the API states it."""
    day = date.today().isoformat()
    _session(conn, day, [("18:00", 1), ("18:12", 2)])

    assert _timing(client)["includes_rest"] is True
