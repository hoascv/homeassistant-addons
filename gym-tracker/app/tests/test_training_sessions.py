"""Training sessions: a start you press, a duration that is measured.

Before this, a session's length was the first exercise's tick minus the
configured heart-rate window — a guessed thirty minutes before the first thing
logged, which also meant a warm-up done beforehand fell outside it entirely.
"""
from datetime import date, datetime, timedelta

import app as gymapp


def _item(client, conn, target_reps=8):
    ex = conn.execute("SELECT id FROM exercises WHERE archived = 0 LIMIT 1").fetchone()["id"]
    r = client.post("/api/challenge/items", json={
        "item_type": "exercise", "exercise_id": ex, "target_reps": target_reps})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _state(client):
    return client.get("/api/training").get_json()


# --- starting and finishing --------------------------------------------------


def test_nothing_is_running_to_begin_with(client):
    body = _state(client)
    assert body["current"] is None
    assert body["recent"] == []
    assert body["mean_seconds"] is None


def test_starting_opens_a_session(client):
    res = client.post("/api/training/start")

    assert res.status_code == 201, res.get_json()
    cur = res.get_json()["current"]
    assert cur["running"] is True
    assert cur["seconds"] >= 0
    assert _state(client)["current"]["id"] == cur["id"]


def test_finishing_closes_it_and_keeps_the_duration(client, conn):
    client.post("/api/training/start")
    # Backdate the start so the duration is a real number rather than 0.
    conn.execute("UPDATE training_sessions SET started_at = ?",
                 ((datetime.now() - timedelta(minutes=42)).isoformat(timespec="seconds"),))
    conn.commit()

    res = client.post("/api/training/finish")

    assert res.status_code == 200, res.get_json()
    done = res.get_json()["session"]
    assert done["running"] is False
    assert done["ended_by"] == "finished"
    assert 41 * 60 <= done["seconds"] <= 43 * 60
    assert _state(client)["current"] is None


def test_finishing_when_nothing_runs_is_not_an_error(client):
    """A session can already have been closed by ticking the last item, so the
    button must not fail for pressing it afterwards."""
    res = client.post("/api/training/finish")

    assert res.status_code == 200
    assert res.get_json()["status"] == "nothing running"


def test_starting_again_closes_the_one_left_open(client, conn):
    """An open session from yesterday is a forgotten one. Making you tidy it up
    before you can train would be the wrong way round."""
    first = client.post("/api/training/start").get_json()["current"]["id"]
    conn.execute("UPDATE training_sessions SET started_at = ?",
                 ((datetime.now() - timedelta(days=1)).isoformat(timespec="seconds"),))
    conn.commit()

    res = client.post("/api/training/start")

    assert res.get_json()["superseded"] == first
    rows = {r["id"]: r for r in conn.execute("SELECT * FROM training_sessions")}
    assert rows[first]["ended_by"] == "superseded"
    assert rows[first]["ended_at"] is not None
    assert _state(client)["current"]["id"] != first


def test_only_one_session_is_ever_open(client, conn):
    for _ in range(3):
        client.post("/api/training/start")

    open_rows = conn.execute(
        "SELECT COUNT(*) AS n FROM training_sessions WHERE ended_at IS NULL").fetchone()
    assert open_rows["n"] == 1


# --- what it counts ----------------------------------------------------------


def test_it_counts_the_exercises_logged_inside_it(client, conn):
    item = _item(client, conn)
    client.post("/api/training/start")

    client.post("/api/challenge/toggle", json={"item_id": item})

    assert _state(client)["current"]["exercises_logged"] == 1


def test_work_logged_before_the_start_is_not_counted(client, conn):
    """The session is a window. Something logged an hour earlier is not in it."""
    item = _item(client, conn)
    client.post("/api/challenge/toggle", json={"item_id": item})
    conn.execute("UPDATE workout_logs SET ts = ?",
                 ((datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds"),))
    conn.commit()

    client.post("/api/training/start")

    assert _state(client)["current"]["exercises_logged"] == 0


def test_the_average_covers_finished_sessions_only(client, conn):
    """A running session has no duration yet; averaging it in would drag the
    figure toward zero every time you opened the app mid-workout."""
    for mins in (30, 40):
        client.post("/api/training/start")
        conn.execute("UPDATE training_sessions SET started_at = ? WHERE ended_at IS NULL",
                     ((datetime.now() - timedelta(minutes=mins)).isoformat(timespec="seconds"),))
        conn.commit()
        client.post("/api/training/finish")
    client.post("/api/training/start")          # still running

    body = _state(client)

    assert body["current"]["running"] is True
    assert 34 * 60 <= body["mean_seconds"] <= 36 * 60   # the mean of 30 and 40


# --- finishing by itself -----------------------------------------------------


def _all_item_ids(client):
    """Every item due today, across every challenge — the seeded challenge
    included. Auto-finish means *everything* due, so a test that ticks only its
    own items is testing nothing."""
    return [i["id"] for c in client.get("/api/challenges").get_json()
            for i in c.get("items", [])]


def test_ticking_the_last_item_due_finishes_the_session(client, conn):
    """The common case needs no second press."""
    _item(client, conn)
    ids = _all_item_ids(client)
    assert len(ids) > 1
    client.post("/api/training/start")

    for item in ids[:-1]:
        client.post("/api/challenge/toggle", json={"item_id": item})
    assert _state(client)["current"] is not None, "closed with work still outstanding"

    client.post("/api/challenge/toggle", json={"item_id": ids[-1]})

    body = _state(client)
    assert body["current"] is None, "everything due was ticked and the session stayed open"
    assert body["recent"][0]["ended_by"] == "auto"


def test_a_session_started_on_another_day_is_not_auto_finished(client, conn):
    """Today's tick says nothing about a session left open yesterday — only an
    explicit start supersedes that."""
    ids = _all_item_ids(client)
    client.post("/api/training/start")
    conn.execute("UPDATE training_sessions SET started_at = ?",
                 ((datetime.now() - timedelta(days=2)).isoformat(timespec="seconds"),))
    conn.commit()

    for item in ids:
        client.post("/api/challenge/toggle", json={"item_id": item})

    assert _state(client)["current"] is not None


def test_un_ticking_does_not_reopen_a_finished_session(client, conn):
    ids = _all_item_ids(client)
    client.post("/api/training/start")
    for item in ids:
        client.post("/api/challenge/toggle", json={"item_id": item})
    assert _state(client)["current"] is None

    client.post("/api/challenge/toggle", json={"item_id": ids[0]})   # un-tick

    assert _state(client)["current"] is None, "un-ticking resurrected the session"


# --- the heart-rate window ---------------------------------------------------


def test_a_declared_start_frames_the_first_exercise(client, conn, monkeypatch):
    """The point of pressing start: the first exercise's window begins where
    training actually began, not a guessed thirty minutes earlier — so a
    warm-up done before the first tick is inside it."""
    from test_garmin import _patch_hr, _hr_series

    day = date.today().isoformat()
    conn.execute("INSERT INTO training_sessions (started_at) VALUES (?)", (f"{day}T18:40:00",))
    conn.execute("INSERT OR IGNORE INTO exercises (id, name) VALUES (1, 'Push-up')")
    conn.execute(
        "INSERT INTO workout_logs (ts, exercise_id, sets, reps, source, ts_exact) "
        "VALUES (?, 1, 3, 8, 'challenge', 1)", (f"{day}T18:55:00",))
    conn.commit()
    _patch_hr(monkeypatch, {day: _hr_series(day, [
        ("18:20", 62),                      # before training started
        ("18:45", 120), ("18:50", 131), ("18:55", 128),
    ])})

    gymapp._garmin_do_sync(conn)

    row = conn.execute("SELECT hr_avg, session_start FROM workout_logs").fetchone()
    assert row["session_start"] == f"{day}T18:40:00", "the declared start was ignored"
    assert row["hr_avg"] == round((120 + 131 + 128) / 3), (
        "the quiet period before training leaked into the window"
    )


def test_a_start_long_before_the_first_tick_is_not_used(client, conn, monkeypatch):
    """A session opened in the morning and trained in the evening must not
    stretch one window across the whole day."""
    from test_garmin import _patch_hr, _hr_series

    day = date.today().isoformat()
    conn.execute("INSERT INTO training_sessions (started_at) VALUES (?)", (f"{day}T08:00:00",))
    conn.execute("INSERT OR IGNORE INTO exercises (id, name) VALUES (1, 'Push-up')")
    conn.execute(
        "INSERT INTO workout_logs (ts, exercise_id, sets, reps, source, ts_exact) "
        "VALUES (?, 1, 3, 8, 'challenge', 1)", (f"{day}T19:00:00",))
    conn.commit()
    _patch_hr(monkeypatch, {day: _hr_series(day, [
        ("08:05", 58), ("12:00", 70), ("18:40", 125), ("19:00", 133),
    ])})

    gymapp._garmin_do_sync(conn)

    row = conn.execute("SELECT hr_avg, session_start FROM workout_logs").fetchone()
    assert row["session_start"] == f"{day}T18:30:00", (
        "an eleven-hour window was accepted from a stale start"
    )
    assert row["hr_avg"] == round((125 + 133) / 2)
