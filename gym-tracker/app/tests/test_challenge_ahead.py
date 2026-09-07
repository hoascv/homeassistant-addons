"""Keeping a day in advance.

The rule the whole feature rests on: nobody trains in the future. Ticking a day
ahead credits that day but files the session — and so the heart rate read
against it — on the day the work actually happened.
"""
from datetime import date, timedelta


def _items(client):
    return client.get("/api/challenge").get_json()["items"]


def _exercise_item(client):
    return next(i for i in _items(client) if i["item_type"] == "exercise")


def _tick(client, item_id, day):
    return client.post("/api/challenge/toggle", json={"item_id": item_id, "day": day})


def test_ticking_a_day_ahead_logs_the_workout_today(client, conn):
    item = _exercise_item(client)
    ahead = (date.today() + timedelta(days=2)).isoformat()
    assert _tick(client, item["id"], ahead).get_json()["done"] is True

    row = conn.execute(
        "SELECT day, done_on, ts FROM challenge_completions WHERE item_id = ?", (item["id"],)
    ).fetchone()
    assert row["day"] == ahead
    assert row["done_on"] == date.today().isoformat()

    workouts = conn.execute(
        "SELECT ts, ts_exact FROM workout_logs WHERE source = 'challenge' AND challenge_item_id = ?",
        (item["id"],),
    ).fetchall()
    # One session, dated today, with a real time on it — a midday placeholder
    # would never be given a heart rate by the Garmin sync.
    assert len(workouts) == 1
    assert workouts[0]["ts"].startswith(date.today().isoformat())
    assert workouts[0]["ts_exact"] == 1


def test_a_day_kept_ahead_is_inert_until_it_arrives(client):
    for item in _items(client):
        _tick(client, item["id"], (date.today() + timedelta(days=1)).isoformat())
    data = client.get("/api/challenge").get_json()
    assert data["complete_today"] is False
    assert data["streak"] == 0
    # Tomorrow is not in the seven dots, and no figure counts it yet.
    assert all(d["complete"] is False for d in data["last_7_days"])


def test_the_card_names_the_days_already_done(client):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    for item in _items(client):
        _tick(client, item["id"], tomorrow)
    assert client.get("/api/challenge").get_json()["done_ahead"] == [tomorrow]


def test_a_partly_ticked_day_ahead_is_not_announced_as_done(client):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    _tick(client, _items(client)[0]["id"], tomorrow)
    assert client.get("/api/challenge").get_json()["done_ahead"] == []


def test_un_ticking_a_day_ahead_removes_the_session_it_logged(client, conn):
    item = _exercise_item(client)
    ahead = (date.today() + timedelta(days=3)).isoformat()
    _tick(client, item["id"], ahead)
    assert _tick(client, item["id"], ahead).get_json()["done"] is False
    # Neither the tick nor the workout it wrote today may be left behind.
    assert conn.execute("SELECT COUNT(*) FROM challenge_completions").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM workout_logs WHERE source = 'challenge'"
    ).fetchone()[0] == 0


def test_history_reports_when_a_future_day_was_done(client):
    item = _exercise_item(client)
    ahead = (date.today() + timedelta(days=2)).isoformat()
    _tick(client, item["id"], ahead)
    data = client.get(
        f"/api/challenge/history?from={date.today().isoformat()}&to={ahead}"
    ).get_json()
    row = next(d for d in data["days"] if d["day"] == ahead)
    assert row["done"] == [item["id"]]
    assert row["done_on"] == {str(item["id"]): date.today().isoformat()}
    # An ordinary day carries nothing extra.
    assert next(d for d in data["days"] if d["day"] == date.today().isoformat())["done_on"] == {}


def test_backfilling_a_past_day_still_means_it_happened_then(client, conn):
    item = _exercise_item(client)
    back = (date.today() - timedelta(days=2)).isoformat()
    _tick(client, item["id"], back)
    row = conn.execute(
        "SELECT day, done_on FROM challenge_completions WHERE item_id = ?", (item["id"],)
    ).fetchone()
    assert (row["day"], row["done_on"]) == (back, None)
    workout = conn.execute(
        "SELECT ts, ts_exact FROM workout_logs WHERE challenge_item_id = ?", (item["id"],)
    ).fetchone()
    assert workout["ts"] == f"{back}T12:00:00"
    assert workout["ts_exact"] == 0


def test_a_mistyped_year_is_refused(client):
    item = _exercise_item(client)
    res = _tick(client, item["id"], "2126-09-08")
    assert res.status_code == 400
    assert "ahead" in res.get_json()["error"]


def test_the_day_arrives_and_counts(client, conn):
    """The same rows, read a day later: what was inert becomes the record."""
    items = _items(client)
    tomorrow = date.today() + timedelta(days=1)
    for item in items:
        _tick(client, item["id"], tomorrow.isoformat())

    ch = dict(conn.execute("SELECT * FROM challenges LIMIT 1").fetchone())
    days = {d["day"]: d for d in gymapp_days(conn, ch, tomorrow)}
    assert days[tomorrow.isoformat()]["complete"] is True


def gymapp_days(conn, ch, today):
    """`_challenge_days` as it will read once `today` is the day in question."""
    import app as gymapp

    real = gymapp.date

    class _FrozenDate(real):
        @classmethod
        def today(cls):
            return today

    gymapp.date = _FrozenDate
    try:
        return gymapp._challenge_days(conn, ch)
    finally:
        gymapp.date = real


def test_a_replayed_tick_keeps_the_session_where_it_was_done(client, conn):
    """A second write for the same day must not move the workout.

    `done_on` on the row that already exists is the truth: if a replay filed the
    session under the day it counts for, the un-tick would look for it there and
    leave the real one behind.
    """
    import app as gymapp

    item = _exercise_item(client)
    ahead = (date.today() + timedelta(days=2)).isoformat()
    _tick(client, item["id"], ahead)

    row = conn.execute("SELECT * FROM challenge_items WHERE id = ?", (item["id"],)).fetchone()
    gymapp._record_completion(conn, row, ahead)  # no done_on: would default to `ahead`
    conn.commit()

    workouts = conn.execute(
        "SELECT ts FROM workout_logs WHERE source = 'challenge' AND challenge_item_id = ?",
        (item["id"],),
    ).fetchall()
    assert len(workouts) == 1
    assert workouts[0]["ts"].startswith(date.today().isoformat())

    _tick(client, item["id"], ahead)  # un-tick
    assert conn.execute(
        "SELECT COUNT(*) FROM workout_logs WHERE source = 'challenge'"
    ).fetchone()[0] == 0
