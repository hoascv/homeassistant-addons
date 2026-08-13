"""What a guided run of a routine records.

The stakes here are the reason this logic sits on the server: finishing has to
tick and log exactly what tapping the item does, and stopping early has to leave
the effort standing without claiming the day was done.
"""


def _routine(client, name="Tabata", rounds=8):
    ex = client.post("/api/exercises", json={"name": name, "equipment": "Bodyweight"}).get_json()["id"]
    client.put(f"/api/exercises/{ex}/routine", json={
        "rounds": rounds,
        "steps": [{"kind": "work", "seconds": 20, "label": "Squats"},
                  {"kind": "rest", "seconds": 10}],
    })
    return ex


def _attach(client, exercise_id):
    challenge_id = client.get("/api/challenges").get_json()[0]["id"]
    res = client.post("/api/challenge/items", json={
        "item_type": "exercise", "exercise_id": exercise_id, "challenge_id": challenge_id,
    })
    assert res.status_code == 201, res.get_json()
    return res.get_json()["id"]


def _session(client, exercise_id, **body):
    payload = {"elapsed_seconds": 240, "completed": True}
    payload.update(body)
    return client.post(f"/api/exercises/{exercise_id}/routine/session", json=payload)


def _workouts(conn):
    return conn.execute("SELECT * FROM workout_logs ORDER BY id").fetchall()


# --- finishing ----------------------------------------------------------------


def test_finishing_ticks_the_item_and_logs_the_workout(client, db_path, conn):
    ex = _routine(client)
    item_id = _attach(client, ex)

    res = _session(client, ex, item_id=item_id, elapsed_seconds=243)
    assert res.status_code == 200
    assert res.get_json()["done"] is True

    rows = _workouts(conn)
    assert len(rows) == 1
    assert rows[0]["source"] == "challenge"
    assert rows[0]["challenge_item_id"] == item_id
    assert conn.execute("SELECT COUNT(*) FROM challenge_completions").fetchone()[0] == 1


def test_the_logged_seconds_are_what_was_measured_not_what_was_planned(client, db_path, conn):
    """A routine that took 243 seconds logs 243, not the 240 it was designed to
    take — otherwise the log records the plan rather than the workout."""
    ex = _routine(client)
    _session(client, ex, item_id=_attach(client, ex), elapsed_seconds=243)
    assert _workouts(conn)[0]["duration_sec"] == 243


def test_finishing_twice_in_a_day_neither_doubles_the_log_nor_trips_the_unique(client, db_path, conn):
    """A retried POST — a flaky connection at the end of a workout — must not
    leave two rows or fail on the (item, day) uniqueness."""
    ex = _routine(client)
    item_id = _attach(client, ex)

    assert _session(client, ex, item_id=item_id).status_code == 200
    assert _session(client, ex, item_id=item_id, elapsed_seconds=250).status_code == 200

    rows = _workouts(conn)
    assert len(rows) == 1, "a retry doubled the workout"
    assert rows[0]["duration_sec"] == 250, "the retry should replace, not be ignored"
    assert conn.execute("SELECT COUNT(*) FROM challenge_completions").fetchone()[0] == 1


def test_un_ticking_after_a_session_removes_the_workout_it_wrote(client, db_path, conn):
    ex = _routine(client)
    item_id = _attach(client, ex)
    _session(client, ex, item_id=item_id)

    client.post("/api/challenge/toggle", json={"item_id": item_id})
    assert _workouts(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM challenge_completions").fetchone()[0] == 0


def test_finishing_without_an_item_logs_a_workout_and_ticks_nothing(client, db_path, conn):
    """Played from the library rather than from a challenge — there is no tick
    to make, but the work still happened."""
    ex = _routine(client)
    res = _session(client, ex, elapsed_seconds=240)

    assert res.get_json()["done"] is False
    rows = _workouts(conn)
    assert len(rows) == 1
    assert rows[0]["source"] == "manual"
    assert rows[0]["challenge_item_id"] is None
    assert conn.execute("SELECT COUNT(*) FROM challenge_completions").fetchone()[0] == 0


# --- stopping early -----------------------------------------------------------


def test_stopping_halfway_logs_the_seconds_and_does_not_tick(client, db_path, conn):
    ex = _routine(client)
    item_id = _attach(client, ex)

    res = _session(client, ex, item_id=item_id, elapsed_seconds=95,
                   completed=False, rounds_done=3)
    assert res.get_json()["done"] is False

    rows = _workouts(conn)
    assert len(rows) == 1
    assert rows[0]["duration_sec"] == 95
    assert "round 3" in rows[0]["notes"]
    assert conn.execute("SELECT COUNT(*) FROM challenge_completions").fetchone()[0] == 0


def test_a_partial_survives_a_later_tick_and_un_tick(client, db_path, conn):
    """The reason a partial is logged as manual: marked 'challenge' it would be
    deleted by an un-tick, so work genuinely done would vanish because of
    something done afterwards."""
    ex = _routine(client)
    item_id = _attach(client, ex)
    _session(client, ex, item_id=item_id, elapsed_seconds=95, completed=False, rounds_done=3)

    client.post("/api/challenge/toggle", json={"item_id": item_id})   # tick
    client.post("/api/challenge/toggle", json={"item_id": item_id})   # un-tick

    rows = _workouts(conn)
    assert len(rows) == 1, "the un-tick ate the partial"
    assert rows[0]["duration_sec"] == 95


def test_a_partial_then_a_finish_keeps_both(client, db_path, conn):
    """Two separate facts: an abandoned attempt, and a completed one."""
    ex = _routine(client)
    item_id = _attach(client, ex)
    _session(client, ex, item_id=item_id, elapsed_seconds=95, completed=False, rounds_done=3)
    _session(client, ex, item_id=item_id, elapsed_seconds=240)

    rows = _workouts(conn)
    assert len(rows) == 2
    assert {r["source"] for r in rows} == {"manual", "challenge"}


# --- refusals -----------------------------------------------------------------


def test_an_exercise_that_is_not_a_routine_is_refused(client, db_path):
    ex = client.post("/api/exercises", json={"name": "Plain", "equipment": "Bodyweight"}).get_json()["id"]
    refused = _session(client, ex)
    assert refused.status_code == 400
    assert "not a routine" in refused.get_json()["error"]


def test_an_item_for_a_different_exercise_is_refused(client, db_path, conn):
    """What stops a confused client ticking the wrong thing off."""
    ex = _routine(client, "Tabata")
    other = _routine(client, "Other routine")
    item_id = _attach(client, other)

    refused = _session(client, ex, item_id=item_id)
    assert refused.status_code == 400
    assert "not for this routine" in refused.get_json()["error"]
    assert _workouts(conn) == []


def test_a_missing_or_nonsense_elapsed_is_refused(client, db_path):
    ex = _routine(client)
    assert client.post(f"/api/exercises/{ex}/routine/session", json={}).status_code == 400
    assert _session(client, ex, elapsed_seconds="ages").status_code == 400
    assert _session(client, ex, elapsed_seconds=-1).status_code == 400
    assert _session(client, ex, elapsed_seconds=90000).status_code == 400


def test_a_session_for_an_unknown_exercise_or_item_is_a_404(client, db_path):
    ex = _routine(client)
    assert client.post("/api/exercises/9999/routine/session",
                       json={"elapsed_seconds": 10, "completed": True}).status_code == 404
    assert _session(client, ex, item_id=9999).status_code == 404


def test_the_streak_agrees_with_the_toggle_route(client, db_path):
    """Both answers come from the same helper, and the client reconciles down
    the same path — so they must not be able to disagree."""
    ex = _routine(client)
    item_id = _attach(client, ex)
    from_session = _session(client, ex, item_id=item_id).get_json()["streak"]

    client.post("/api/challenge/toggle", json={"item_id": item_id})   # un-tick
    from_toggle = client.post("/api/challenge/toggle", json={"item_id": item_id}).get_json()["streak"]
    assert from_session == from_toggle
