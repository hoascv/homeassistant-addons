"""A schedule anchored on when you last did it, not on the calendar.

The problem it exists for: a rotation of gym sessions done whenever you reach
the gym cannot be described by fixed weekdays. Pinned to Monday, a session
trained on Saturday lands on a day the challenge does not consider due — so the
tick counts as neither a hit nor a miss, and the card reports 0% adherence for
a session that was trained four times.

`rolling` asks instead: has it been N days since this was last completed?
"""
from datetime import date, timedelta

import app as gymapp


def _challenge(client, conn, interval, start=None, name="Session A"):
    start = start or (date.today() - timedelta(days=30)).isoformat()
    r = client.post("/api/challenges", json={
        "name": name, "start_date": start,
        "schedule_kind": "rolling", "schedule_interval": interval})
    assert r.status_code == 201, r.get_json()
    cid = r.get_json()["id"]
    ex = conn.execute("SELECT id FROM exercises WHERE archived = 0 LIMIT 1").fetchone()["id"]
    item = client.post("/api/challenge/items", json={
        "item_type": "exercise", "exercise_id": ex, "target_reps": 8,
        "challenge_id": cid}).get_json()["id"]
    return cid, item


def _tick(client, item, day):
    r = client.post("/api/challenge/toggle", json={"item_id": item, "day": day})
    assert r.status_code == 200, r.get_json()


def _card(client, cid):
    return next(c for c in client.get("/api/challenges").get_json() if c["id"] == cid)


def _days(conn, cid):
    ch = next(c for c in gymapp._challenges(conn) if c["id"] == cid)
    return {d["day"]: d for d in gymapp._challenge_days(conn, ch)}


# --- the schedule itself -----------------------------------------------------


def test_it_is_due_from_the_start_until_it_is_first_done(client, conn):
    """Never done means overdue, not waiting for a date to come round."""
    cid, _ = _challenge(client, conn, interval=3)

    assert _card(client, cid)["due_today"] is True


def test_doing_it_today_leaves_today_due_and_moves_the_next_one(client, conn):
    """Today stays a day it was due — it was, and you did it. Completing
    something must not retroactively turn today into a rest day, or the card
    would collapse the list you have just ticked. What moves is the *next* due
    day, which is counted from today."""
    cid, item = _challenge(client, conn, interval=3)
    _tick(client, item, date.today().isoformat())

    card = _card(client, cid)
    assert card["due_today"] is True
    assert card["complete_today"] is True
    assert card["next_due"] == (date.today() + timedelta(days=3)).isoformat()


def test_it_comes_due_again_after_the_interval(client, conn):
    cid, item = _challenge(client, conn, interval=3)
    _tick(client, item, (date.today() - timedelta(days=3)).isoformat())

    assert _card(client, cid)["due_today"] is True, "3 days after, it should be due again"


def test_it_is_not_due_before_the_interval_has_passed(client, conn):
    cid, item = _challenge(client, conn, interval=3)
    _tick(client, item, (date.today() - timedelta(days=2)).isoformat())

    assert _card(client, cid)["due_today"] is False


def test_it_stays_due_while_overdue(client, conn):
    """Being a week late does not make it stop asking."""
    cid, item = _challenge(client, conn, interval=3)
    _tick(client, item, (date.today() - timedelta(days=10)).isoformat())

    assert _card(client, cid)["due_today"] is True


# --- the thing it was built for ---------------------------------------------


def test_training_on_any_day_counts(client, conn):
    """The whole point. Under `weekdays`, a session trained off-schedule
    counted as neither hit nor miss; here every completion lands on a day the
    challenge agrees was due."""
    cid, item = _challenge(client, conn, interval=3)
    # Four sessions at irregular gaps, the way a gym rotation actually happens.
    done = [date.today() - timedelta(days=n) for n in (21, 14, 6, 1)]
    for d in done:
        _tick(client, item, d.isoformat())

    days = _days(conn, cid)
    for d in done:
        row = days[d.isoformat()]
        assert row["scheduled"] is True, f"{d} was trained but not counted as due"
        assert row["complete"] is True, f"{d} was trained but not counted as complete"


def test_an_early_session_still_resets_the_clock(client, conn):
    """Doing it the day after you last did it is not due — but it counts, and
    the next due day is measured from it."""
    cid, item = _challenge(client, conn, interval=7)
    _tick(client, item, (date.today() - timedelta(days=8)).isoformat())
    early = date.today() - timedelta(days=7)
    _tick(client, item, early.isoformat())

    # Seven days after the early one, not after the first.
    assert _card(client, cid)["due_today"] is True
    days = _days(conn, cid)
    assert days[early.isoformat()]["scheduled"] is False, "it was not yet due"
    assert days[early.isoformat()]["done"] == days[early.isoformat()]["total"]


def test_the_next_due_day_is_counted_from_the_last_completion(client, conn):
    cid, item = _challenge(client, conn, interval=4)
    _tick(client, item, date.today().isoformat())

    expected = (date.today() + timedelta(days=4)).isoformat()
    assert _card(client, cid)["next_due"] == expected


def test_adherence_reflects_the_sessions_actually_done(client, conn):
    """Under weekdays this challenge read 0%. Here the hits are counted.

    Note what adherence means for a rolling schedule: the fraction of days you
    were *up to date*, not the fraction of sessions done. Being a week late
    makes a week of due days, because you were overdue on every one of them.
    Kept to the cadence, it reads high.
    """
    cid, item = _challenge(client, conn, interval=3,
                           start=(date.today() - timedelta(days=12)).isoformat())
    for n in (12, 9, 6, 3):
        _tick(client, item, (date.today() - timedelta(days=n)).isoformat())

    days = _days(conn, cid)
    due = [d for d in days.values() if d["scheduled"]]
    hit = [d for d in due if d["complete"]]
    assert len(hit) == 4, f"{len(hit)} of 4 sessions counted, not 4"
    assert len(hit) / len(due) >= 0.8, (
        f"kept a 3-day cadence for 12 days and scored {len(hit)}/{len(due)}"
    )


def test_a_long_gap_shows_as_a_long_gap(client, conn):
    """The other side of that: overdue days each count, so a fortnight away
    reads as a fortnight of misses rather than one."""
    cid, item = _challenge(client, conn, interval=3,
                           start=(date.today() - timedelta(days=20)).isoformat())
    _tick(client, item, (date.today() - timedelta(days=20)).isoformat())
    _tick(client, item, date.today().isoformat())

    days = _days(conn, cid)
    due = [d for d in days.values() if d["scheduled"]]
    hit = [d for d in due if d["complete"]]
    assert len(hit) == 2
    assert len(due) > 10, "a 20-day gap on a 3-day cadence should read as badly overdue"


# --- the streak --------------------------------------------------------------


def test_a_kept_cadence_is_a_streak(client, conn):
    cid, item = _challenge(client, conn, interval=3)
    for n in (9, 6, 3, 0):
        _tick(client, item, (date.today() - timedelta(days=n)).isoformat())

    assert _card(client, cid)["streak"] >= 4, _card(client, cid)["streak"]


def test_a_missed_window_breaks_the_streak(client, conn):
    cid, item = _challenge(client, conn, interval=3)
    _tick(client, item, (date.today() - timedelta(days=20)).isoformat())
    _tick(client, item, date.today().isoformat())

    # The long gap in between was overdue throughout, so the run is only today's.
    assert _card(client, cid)["streak"] == 1


# --- validation --------------------------------------------------------------


def test_an_interval_of_one_is_allowed(client, conn):
    """Unlike `interval`, where 1 is just daily spelled oddly, a rolling 1 means
    "again the day after you did it" — which is a different thing."""
    r = client.post("/api/challenges", json={
        "name": "Daily-ish", "start_date": date.today().isoformat(),
        "schedule_kind": "rolling", "schedule_interval": 1})
    assert r.status_code == 201, r.get_json()


def test_a_missing_interval_is_refused(client):
    r = client.post("/api/challenges", json={
        "name": "No cadence", "start_date": date.today().isoformat(),
        "schedule_kind": "rolling"})
    assert r.status_code == 400
    assert "schedule_interval" in r.get_json()["error"]


def test_an_absurd_interval_is_refused(client):
    r = client.post("/api/challenges", json={
        "name": "Yearly", "start_date": date.today().isoformat(),
        "schedule_kind": "rolling", "schedule_interval": 400})
    assert r.status_code == 400


def test_the_other_schedules_are_unchanged(client, conn):
    """Regression: `interval` still counts from the start date, and still
    refuses 1."""
    today = date.today()
    r = client.post("/api/challenges", json={
        "name": "Every other day", "start_date": (today - timedelta(days=4)).isoformat(),
        "schedule_kind": "interval", "schedule_interval": 2})
    assert r.status_code == 201
    assert _card(client, r.get_json()["id"])["due_today"] is True   # 4 days on from the start

    bad = client.post("/api/challenges", json={
        "name": "Interval one", "start_date": today.isoformat(),
        "schedule_kind": "interval", "schedule_interval": 1})
    assert bad.status_code == 400


# --- rows the API would not produce, but a restore or a pipeline might ------


def _raw_challenge(conn, **cols):
    """A challenge written straight to the table, bypassing validation."""
    keys = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    cur = conn.execute(
        f"INSERT INTO challenges (name, start_date, sort_order, archived, {keys}) "
        f"VALUES ('Legacy', ?, 0, 0, {marks})",
        ((date.today() - timedelta(days=10)).isoformat(), *cols.values()))
    conn.commit()
    ch = dict(conn.execute("SELECT * FROM challenges WHERE id = ?", (cur.lastrowid,)).fetchone())
    return ch


def test_a_rolling_row_with_no_interval_falls_back_to_due(conn):
    """A quietly-never-due challenge is worse than one that asks too often, so
    anything unreadable errs toward due."""
    ch = _raw_challenge(conn, schedule_kind="rolling", schedule_interval=None)

    assert gymapp._challenge_scheduled_on(ch, date.today(), None) is True


def test_a_rolling_row_with_a_nonsense_interval_falls_back_to_due(conn):
    ch = _raw_challenge(conn, schedule_kind="rolling", schedule_interval="soon")

    assert gymapp._challenge_scheduled_on(ch, date.today(), None) is True
    assert gymapp._challenge_scheduled_on(ch, date.today(), date.today()) is True


def test_a_rolling_row_with_a_zero_interval_is_due_daily(conn):
    """Zero would divide the schedule by nothing; it reads as "always"."""
    ch = _raw_challenge(conn, schedule_kind="rolling", schedule_interval=0)

    assert gymapp._challenge_scheduled_on(ch, date.today(), date.today()) is True


def test_a_rolling_row_with_an_unreadable_start_date_is_due(conn):
    """Only consulted when the challenge has never been completed."""
    ch = _raw_challenge(conn, schedule_kind="rolling", schedule_interval=3)
    ch["start_date"] = "whenever"

    assert gymapp._challenge_scheduled_on(ch, date.today(), None) is True
