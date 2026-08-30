"""Meal adherence, and the distinction the whole feature rests on.

There are three states, not two: eaten, skipped, and *never recorded*. Most of
what follows exists to pin that third one down, because every plausible bug here
collapses it into one of the others — and the failure is silent. A missing row
read as a skip turns a fortnight's holiday into the worst adherence in the
history; read as compliance, it makes the numbers meaninglessly flattering.

The daily challenge next door stores a tick and treats absence as "not done",
which is correct there and wrong here. These tests are what stops someone
helpfully unifying the two later.
"""
import datetime

import pytest

import app as gymapp
import meals


TODAY = "2026-08-30"
THREE = ["Breakfast", "Lunch", "Dinner"]


def _statuses(rows):
    return {r["meal"]: r["status"] for r in rows}


# --- the third state ----------------------------------------------------------


def test_an_unrecorded_meal_is_none_not_a_skip(conn):
    """The single most important assertion in this file."""
    rows = meals.get_day(conn, TODAY, THREE)
    assert _statuses(rows) == {"Breakfast": None, "Lunch": None, "Dinner": None}
    assert all(r["status"] != meals.SKIPPED for r in rows)


def test_an_unrecorded_day_contributes_to_neither_side_of_the_rate(conn):
    """A week where the app was never opened must not read as a week of skipped
    meals — that is the failure this design exists to prevent."""
    summary = meals.range_summary(conn, "2026-08-01", "2026-08-07", THREE)
    assert summary["ate"] == 0
    assert summary["skipped"] == 0
    assert summary["recorded"] == 0
    assert summary["skip_rate"] is None, "a rate with no data must be None, not 0"


def test_the_skip_rate_is_out_of_what_was_recorded_not_what_was_possible(conn):
    """21 possible meals in a week; 4 recorded; 1 skipped. The rate is 1/4, not
    1/21. Reporting 1/21 would flatter the number by counting silence as
    success."""
    meals.log_meal(conn, "2026-08-24", "Breakfast", meals.ATE)
    meals.log_meal(conn, "2026-08-24", "Lunch", meals.SKIPPED)
    meals.log_meal(conn, "2026-08-25", "Breakfast", meals.ATE)
    meals.log_meal(conn, "2026-08-25", "Dinner", meals.ATE)
    conn.commit()

    summary = meals.range_summary(conn, "2026-08-24", "2026-08-30", THREE)
    assert summary["recorded"] == 4
    assert summary["skipped"] == 1
    assert summary["skip_rate"] == 0.25
    assert summary["possible"] == 21
    assert summary["coverage"] == round(4 / 21, 3)


def test_clearing_a_meal_returns_it_to_unknown_not_to_skipped(conn):
    """'I should not have recorded that' is a different statement from 'I did
    not eat', and the two must not be conflated by the delete path."""
    meals.log_meal(conn, TODAY, "Lunch", meals.ATE)
    conn.commit()
    assert _statuses(meals.get_day(conn, TODAY, THREE))["Lunch"] == meals.ATE

    meals.clear_meal(conn, TODAY, "Lunch")
    conn.commit()
    assert _statuses(meals.get_day(conn, TODAY, THREE))["Lunch"] is None
    assert meals.range_summary(conn, TODAY, TODAY, THREE)["skipped"] == 0


def test_only_days_with_an_explicit_skip_are_marked_on_the_chart(conn):
    """The weight chart draws these. A day nobody logged has nothing known
    about it, so it must produce no marker rather than an assumed one."""
    meals.log_meal(conn, "2026-08-28", "Lunch", meals.SKIPPED)
    meals.log_meal(conn, "2026-08-29", "Breakfast", meals.ATE)
    conn.commit()

    marked = meals.skipped_days(conn, "2026-08-01", "2026-08-30")
    assert [m["day"] for m in marked] == ["2026-08-28"]
    assert marked[0]["meals"] == ["Lunch"]


# --- logging ------------------------------------------------------------------


def test_a_meal_can_be_eaten_or_skipped(conn):
    meals.log_meal(conn, TODAY, "Breakfast", meals.ATE)
    meals.log_meal(conn, TODAY, "Lunch", meals.SKIPPED)
    conn.commit()
    assert _statuses(meals.get_day(conn, TODAY, THREE)) == {
        "Breakfast": meals.ATE, "Lunch": meals.SKIPPED, "Dinner": None,
    }


def test_relogging_replaces_rather_than_appends(conn):
    """A mis-tap is corrected by tapping the other button. Two rows for one
    lunch would make the day's counts add up to more than the day."""
    meals.log_meal(conn, TODAY, "Lunch", meals.SKIPPED)
    meals.log_meal(conn, TODAY, "Lunch", meals.ATE)
    conn.commit()

    rows = conn.execute("SELECT COUNT(*) AS n FROM meal_logs WHERE day = ?", (TODAY,)).fetchone()
    assert rows["n"] == 1
    assert _statuses(meals.get_day(conn, TODAY, THREE))["Lunch"] == meals.ATE


def test_a_note_is_optional_and_survives(conn):
    meals.log_meal(conn, TODAY, "Lunch", meals.SKIPPED, note="  long meeting  ")
    conn.commit()
    row = [r for r in meals.get_day(conn, TODAY, THREE) if r["meal"] == "Lunch"][0]
    assert row["note"] == "long meeting"


def test_a_blank_note_is_stored_as_nothing(conn):
    """So 'has a note' is a meaningful filter rather than matching empty strings."""
    meals.log_meal(conn, TODAY, "Lunch", meals.ATE, note="   ")
    conn.commit()
    assert meals.recent_notes(conn) == []


def test_an_unknown_status_is_refused(conn):
    with pytest.raises(ValueError, match="status"):
        meals.log_meal(conn, TODAY, "Lunch", "maybe")


def test_a_missing_meal_name_is_refused(conn):
    with pytest.raises(ValueError, match="meal"):
        meals.log_meal(conn, TODAY, "   ", meals.ATE)


def test_the_recorded_timestamp_is_separate_from_the_day(conn):
    """Logging last night's skipped dinner this morning is normal, so the day
    it belongs to and the moment it was recorded are different facts."""
    meals.log_meal(conn, "2026-08-29", "Dinner", meals.SKIPPED)
    conn.commit()
    row = conn.execute("SELECT day, ts FROM meal_logs").fetchone()
    assert row["day"] == "2026-08-29"
    assert row["ts"].startswith(datetime.date.today().isoformat())


# --- configured meals ---------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    (None, list(meals.DEFAULT_MEALS)),
    ("", list(meals.DEFAULT_MEALS)),
    ("   ", list(meals.DEFAULT_MEALS)),
    ("Breakfast, Lunch, Dinner, Supper", ["Breakfast", "Lunch", "Dinner", "Supper"]),
    ("Brunch\nTea", ["Brunch", "Tea"]),
    ("Lunch, lunch, LUNCH", ["Lunch"]),
    (",,,", list(meals.DEFAULT_MEALS)),
])
def test_meal_names_are_free_text_and_deduplicated(raw, expected):
    """Meal names are personal and cultural — four meals, or calling the
    evening one 'tea', should not be an argument with the app."""
    assert meals.configured_meals(raw) == expected


def test_history_survives_a_meal_being_retired(conn):
    """Renaming or dropping a meal must not orphan what was recorded about it —
    which is why the column is text rather than a foreign key."""
    meals.log_meal(conn, TODAY, "Supper", meals.SKIPPED)
    conn.commit()

    rows = meals.get_day(conn, TODAY, THREE)  # 'Supper' no longer configured
    supper = [r for r in rows if r["meal"] == "Supper"]
    assert supper and supper[0]["status"] == meals.SKIPPED
    assert supper[0]["retired"] is True


def test_a_retired_meal_does_not_count_against_the_day(conn):
    """It is history, not an outstanding thing to record today."""
    meals.log_meal(conn, TODAY, "Supper", meals.ATE)
    conn.commit()
    summary = meals.day_summary(conn, TODAY, THREE)
    assert summary["expected"] == 3
    assert summary["recorded"] == 0


# --- the day summary ----------------------------------------------------------


def test_the_day_summary_states_its_denominator(conn):
    """'2 of 3 recorded' is a fact; showing 2 alone implies the third was eaten."""
    meals.log_meal(conn, TODAY, "Breakfast", meals.ATE)
    meals.log_meal(conn, TODAY, "Lunch", meals.SKIPPED)
    conn.commit()
    summary = meals.day_summary(conn, TODAY, THREE)
    assert (summary["ate"], summary["skipped"]) == (1, 1)
    assert (summary["recorded"], summary["expected"]) == (2, 3)


# --- the streak ---------------------------------------------------------------


def test_a_streak_counts_days_with_every_meal_eaten(conn):
    for offset in range(3):
        day = (datetime.date.fromisoformat(TODAY) - datetime.timedelta(days=offset)).isoformat()
        for meal in THREE:
            meals.log_meal(conn, day, meal, meals.ATE)
    conn.commit()
    assert meals.current_streak(conn, THREE, today=TODAY) == 3


def test_an_unrecorded_day_ends_the_streak_rather_than_being_skipped_over(conn):
    """Otherwise the streak measures how often the app was opened. Nobody can
    say what happened on a day nobody recorded, so it cannot extend a run."""
    for meal in THREE:
        meals.log_meal(conn, TODAY, meal, meals.ATE)
    # 2026-08-29 deliberately left blank
    for meal in THREE:
        meals.log_meal(conn, "2026-08-28", meal, meals.ATE)
    conn.commit()
    assert meals.current_streak(conn, THREE, today=TODAY) == 1


def test_a_skipped_meal_ends_the_streak(conn):
    for meal in THREE:
        meals.log_meal(conn, TODAY, meal, meals.ATE)
    meals.log_meal(conn, "2026-08-29", "Lunch", meals.SKIPPED)
    conn.commit()
    assert meals.current_streak(conn, THREE, today=TODAY) == 1


def test_a_partly_recorded_day_ends_the_streak(conn):
    """Two of three eaten and one unknown is not a complete day."""
    meals.log_meal(conn, TODAY, "Breakfast", meals.ATE)
    meals.log_meal(conn, TODAY, "Lunch", meals.ATE)
    conn.commit()
    assert meals.current_streak(conn, THREE, today=TODAY) == 0


def test_no_configured_meals_is_a_streak_of_zero_not_an_infinite_loop(conn):
    assert meals.current_streak(conn, [], today=TODAY) == 0


# --- the HTTP surface ---------------------------------------------------------


def test_the_day_route_returns_every_configured_meal(client):
    body = client.get(f"/api/meals?day={TODAY}").get_json()
    assert [m["meal"] for m in body["meals"]] == list(meals.DEFAULT_MEALS)
    assert all(m["status"] is None for m in body["meals"])
    assert body["expected"] == 3


def test_logging_through_the_route_round_trips(client):
    posted = client.post("/api/meals", json={
        "day": TODAY, "meal": "Lunch", "status": "skipped", "note": "long meeting",
    })
    assert posted.status_code == 200
    assert _statuses(posted.get_json()["meals"])["Lunch"] == "skipped"

    fetched = client.get(f"/api/meals?day={TODAY}").get_json()
    assert _statuses(fetched["meals"])["Lunch"] == "skipped"


def test_the_route_refuses_a_bad_status(client):
    response = client.post("/api/meals", json={"day": TODAY, "meal": "Lunch", "status": "maybe"})
    assert response.status_code == 400
    assert "status" in response.get_json()["error"]


@pytest.mark.parametrize("day", ["not-a-date", "30-08-2026", "2026-13-40"])
def test_the_route_refuses_a_bad_day(client, day):
    assert client.get(f"/api/meals?day={day}").status_code == 400


def test_no_day_at_all_means_today(client):
    """An absent or empty day is not an error — it is the common case, since
    the page asks for today without saying so."""
    body = client.get("/api/meals?day=").get_json()
    assert body["day"] == datetime.date.today().isoformat()


def test_deleting_through_the_route_returns_the_meal_to_unknown(client):
    client.post("/api/meals", json={"day": TODAY, "meal": "Lunch", "status": "ate"})
    body = client.delete("/api/meals", json={"day": TODAY, "meal": "Lunch"}).get_json()
    assert _statuses(body["meals"])["Lunch"] is None


def test_the_summary_route_clamps_the_window(client):
    """`days` comes off the query string; an unbounded value would scan
    everything for nothing."""
    for days in ("100000", "-5", "notanumber"):
        assert client.get(f"/api/meals/summary?days={days}").status_code == 200


def test_the_summary_route_carries_the_chart_markers_and_notes(client):
    client.post("/api/meals", json={
        "day": TODAY, "meal": "Dinner", "status": "skipped", "note": "out late",
    })
    body = client.get(f"/api/meals/summary?end={TODAY}&days=7").get_json()
    assert body["skipped"] == 1
    assert body["skip_rate"] == 1.0, "one recorded meal, and it was skipped"
    assert [d["day"] for d in body["skipped_days"]] == [TODAY]
    assert body["notes"][0]["note"] == "out late"


def test_meals_are_ingress_only_like_the_rest_of_the_app(direct_client):
    assert direct_client.get("/api/meals").status_code in (401, 403)
    assert direct_client.post("/api/meals", json={}).status_code in (401, 403)


# --- the pipeline contract ----------------------------------------------------


def test_meal_logs_is_in_the_change_feed(conn):
    """Every other table the app owns is. A table outside it is invisible to
    the lakehouse and silently missing from backups."""
    assert "meal_logs" in gymapp.TRACKED_TABLES


def test_a_logged_meal_produces_a_change_log_row(conn):
    """The triggers are installed per tracked table at start-up; a table added
    to the dict but created after the triggers run would have none."""
    before = conn.execute("SELECT COUNT(*) AS n FROM change_log").fetchone()["n"]
    meals.log_meal(conn, TODAY, "Lunch", meals.ATE)
    conn.commit()
    rows = conn.execute(
        "SELECT table_name, op FROM change_log WHERE table_name = 'meal_logs'"
    ).fetchall()
    assert rows, "no change_log row — the trigger is missing"
    assert conn.execute("SELECT COUNT(*) AS n FROM change_log").fetchone()["n"] > before


def test_meals_appear_in_stats_and_the_export_snapshot(client):
    """/api/backup here is the binary database, unlike Knowledge's JSON one;
    /api/export is the snapshot the pipeline bootstraps from."""
    client.post("/api/meals", json={"day": TODAY, "meal": "Lunch", "status": "ate"})

    stats = client.get("/api/stats").get_json()
    assert stats["counts"]["meal_logs"] == 1

    export = client.get("/api/export").get_json()
    assert export["tables"]["meal_logs"][0]["meal"] == "Lunch"
    assert export["keys"]["meal_logs"] == "id"
