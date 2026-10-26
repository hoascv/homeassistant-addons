"""Recording what was actually lifted, not what was planned.

Before 1.58.0 a tick copied the item's target sets and reps and never touched
weight at all — so a barbell programme written 3×8 logged 3×8 every session
whatever happened, and the load, the one variable that actually moves, was
absent from 326 of 329 entries.
"""
from datetime import date


def _exercise_item(client, conn, target_sets=3, target_reps=8):
    ex = conn.execute("SELECT id FROM exercises WHERE archived = 0 LIMIT 1").fetchone()["id"]
    r = client.post("/api/challenge/items", json={
        "item_type": "exercise", "exercise_id": ex,
        "target_sets": target_sets, "target_reps": target_reps,
    })
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"], ex


def _logged(conn, item_id):
    return conn.execute(
        "SELECT sets, reps, weight_kg FROM workout_logs "
        "WHERE source = 'challenge' AND challenge_item_id = ?", (item_id,)
    ).fetchone()


def _card_item(client, item_id):
    items = client.get("/api/challenge").get_json()["items"]
    return next(i for i in items if i["id"] == item_id)


# --- the fast path is unchanged ---------------------------------------------


def test_a_plain_tick_still_records_the_target(client, conn):
    """One tap has to keep working exactly as it did: the target stands in."""
    item_id, _ = _exercise_item(client, conn)
    client.post("/api/challenge/toggle", json={"item_id": item_id})

    row = _logged(conn, item_id)
    assert (row["sets"], row["reps"]) == (3, 8)
    assert row["weight_kg"] is None  # a target carries no load, so none is invented


# --- ticking with the real numbers ------------------------------------------


def test_a_tick_can_carry_what_was_actually_done(client, conn):
    item_id, _ = _exercise_item(client, conn)
    client.post("/api/challenge/toggle",
                json={"item_id": item_id, "sets": 3, "reps": 6, "weight_kg": 82.5})

    row = _logged(conn, item_id)
    assert (row["sets"], row["reps"], row["weight_kg"]) == (3, 6, 82.5)


def test_a_short_session_is_not_logged_as_the_target(client, conn):
    """The reported shape: 3x8 planned, 3x6 done, logged as 3x8."""
    item_id, _ = _exercise_item(client, conn)
    client.post("/api/challenge/toggle", json={"item_id": item_id, "reps": 6})

    row = _logged(conn, item_id)
    assert row["reps"] == 6
    assert row["sets"] == 3, "sets were not sent, so the target still stands in"


def test_the_weight_reaches_the_workout_history(client, conn):
    """The load is only useful if the rest of the app can see it."""
    item_id, _ = _exercise_item(client, conn)
    client.post("/api/challenge/toggle",
                json={"item_id": item_id, "weight_kg": 90})

    hist = client.get("/api/workouts").get_json()
    entry = next(w for w in hist if w["source"] == "challenge")
    assert entry["weight_kg"] == 90


# --- amending after the tick ------------------------------------------------


def test_numbers_can_be_added_after_ticking(client, conn):
    """The tick goes green first and the numbers get typed while the next set is
    being loaded, so the amend path is the one that matters most."""
    item_id, _ = _exercise_item(client, conn)
    client.post("/api/challenge/toggle", json={"item_id": item_id})

    res = client.put("/api/challenge/logged",
                     json={"item_id": item_id, "sets": 3, "reps": 7, "weight_kg": 85})

    assert res.status_code == 200, res.get_json()
    row = _logged(conn, item_id)
    assert (row["sets"], row["reps"], row["weight_kg"]) == (3, 7, 85.0)


def test_amending_does_not_create_a_second_workout(client, conn):
    item_id, _ = _exercise_item(client, conn)
    client.post("/api/challenge/toggle", json={"item_id": item_id})
    client.put("/api/challenge/logged", json={"item_id": item_id, "weight_kg": 85})
    client.put("/api/challenge/logged", json={"item_id": item_id, "weight_kg": 87.5})

    rows = conn.execute(
        "SELECT COUNT(*) n FROM workout_logs WHERE challenge_item_id = ?", (item_id,)
    ).fetchone()
    assert rows["n"] == 1
    assert _logged(conn, item_id)["weight_kg"] == 87.5


def test_an_emptied_field_clears_the_value(client, conn):
    """Sending null is how the UI says "not recorded" after a mistyped number."""
    item_id, _ = _exercise_item(client, conn)
    client.post("/api/challenge/toggle", json={"item_id": item_id, "weight_kg": 85})

    client.put("/api/challenge/logged", json={"item_id": item_id, "weight_kg": None})

    assert _logged(conn, item_id)["weight_kg"] is None


def test_numbers_without_a_tick_are_refused(client, conn):
    """Numbers alone would be a session that never happened."""
    item_id, _ = _exercise_item(client, conn)

    res = client.put("/api/challenge/logged", json={"item_id": item_id, "weight_kg": 85})

    assert res.status_code == 404
    assert "tick the item first" in res.get_json()["error"]


def test_a_supplement_has_nothing_to_weigh(client, conn):
    sup = conn.execute("SELECT id FROM supplements LIMIT 1").fetchone()["id"]
    r = client.post("/api/challenge/items",
                    json={"item_type": "supplement", "supplement_id": sup})
    item_id = r.get_json()["id"]
    client.post("/api/challenge/toggle", json={"item_id": item_id})

    res = client.put("/api/challenge/logged", json={"item_id": item_id, "weight_kg": 5})

    assert res.status_code == 400
    assert "only an exercise item" in res.get_json()["error"]


# --- validation --------------------------------------------------------------


def test_a_non_numeric_weight_is_refused(client, conn):
    item_id, _ = _exercise_item(client, conn)
    res = client.post("/api/challenge/toggle",
                      json={"item_id": item_id, "weight_kg": "heavy"})
    assert res.status_code == 400
    assert "weight_kg must be a number" in res.get_json()["error"]
    # and the tick did not half-happen
    assert _logged(conn, item_id) is None


def test_an_absurd_weight_is_refused(client, conn):
    item_id, _ = _exercise_item(client, conn)
    res = client.post("/api/challenge/toggle",
                      json={"item_id": item_id, "weight_kg": 5000})
    assert res.status_code == 400
    assert "between 0 and 1000" in res.get_json()["error"]


def test_a_negative_rep_count_is_refused(client, conn):
    item_id, _ = _exercise_item(client, conn)
    res = client.post("/api/challenge/toggle", json={"item_id": item_id, "reps": -5})
    assert res.status_code == 400


# --- the prefill -------------------------------------------------------------


def test_the_card_suggests_the_last_weight_used(client, conn):
    """So a session is confirm-or-nudge rather than retyping the same number."""
    item_id, _ = _exercise_item(client, conn)
    assert _card_item(client, item_id)["last_weight_kg"] is None

    client.post("/api/challenge/toggle", json={"item_id": item_id, "weight_kg": 82.5})

    assert _card_item(client, item_id)["last_weight_kg"] == 82.5


def test_the_suggestion_follows_the_exercise_not_the_item(client, conn):
    """A weight logged by hand, or on another challenge's item, is still the
    last thing that exercise was loaded with."""
    item_id, ex = _exercise_item(client, conn)
    r = client.post("/api/workouts",
                    json={"exercise_id": ex, "sets": 3, "reps": 5, "weight_kg": 100})
    assert r.status_code in (200, 201), r.get_json()

    assert _card_item(client, item_id)["last_weight_kg"] == 100


def test_the_card_reports_what_today_recorded(client, conn):
    """The fields show the stored numbers rather than re-suggesting the target
    over the top of them."""
    item_id, _ = _exercise_item(client, conn)
    assert _card_item(client, item_id)["logged"] is None

    client.post("/api/challenge/toggle",
                json={"item_id": item_id, "sets": 3, "reps": 6, "weight_kg": 82.5})

    logged = _card_item(client, item_id)["logged"]
    assert logged == {"sets": 3, "reps": 6, "weight_kg": 82.5}


def test_un_ticking_clears_what_it_logged(client, conn):
    item_id, _ = _exercise_item(client, conn)
    client.post("/api/challenge/toggle", json={"item_id": item_id, "weight_kg": 85})
    client.post("/api/challenge/toggle", json={"item_id": item_id})

    assert _logged(conn, item_id) is None
    assert _card_item(client, item_id)["logged"] is None
    # but the suggestion survives: it is the last weight the exercise saw
    assert _card_item(client, item_id)["last_weight_kg"] is None


def test_a_day_kept_in_advance_still_finds_its_numbers(client, conn):
    """A tick for tomorrow files its workout under today, so the card has to
    look for the numbers where they actually are."""
    from datetime import timedelta

    item_id, _ = _exercise_item(client, conn)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    r = client.post("/api/challenge/toggle",
                    json={"item_id": item_id, "day": tomorrow, "weight_kg": 85})
    assert r.status_code == 200, r.get_json()

    res = client.put("/api/challenge/logged",
                     json={"item_id": item_id, "day": tomorrow, "weight_kg": 87.5})
    assert res.status_code == 200, res.get_json()
    assert _logged(conn, item_id)["weight_kg"] == 87.5


# --- one field at a time ----------------------------------------------------
# The fields each save on their own change event, so every amend sends exactly
# one key. Anything the handler reads as "absent means reset" is data loss on an
# unrelated edit — the same shape as the sets/reps bug fixed in 1.32.2.


def test_typing_a_weight_does_not_reset_the_reps(client, conn):
    item_id, _ = _exercise_item(client, conn, target_reps=8)
    client.post("/api/challenge/toggle", json={"item_id": item_id, "reps": 6})

    client.put("/api/challenge/logged", json={"item_id": item_id, "weight_kg": 82.5})

    row = _logged(conn, item_id)
    assert row["reps"] == 6, "the reps fell back to the target instead of staying put"
    assert row["weight_kg"] == 82.5


def test_typing_reps_does_not_wipe_the_weight(client, conn):
    item_id, _ = _exercise_item(client, conn)
    client.post("/api/challenge/toggle", json={"item_id": item_id, "weight_kg": 82.5})

    client.put("/api/challenge/logged", json={"item_id": item_id, "reps": 6})

    row = _logged(conn, item_id)
    assert row["weight_kg"] == 82.5, "the weight was cleared by an edit that never mentioned it"
    assert row["reps"] == 6


def test_clearing_the_weight_leaves_the_reps_alone(client, conn):
    """The exact sequence that caught this: 3x6 at 82.5, then the weight
    emptied, which used to put the reps back to the target."""
    item_id, _ = _exercise_item(client, conn, target_reps=8)
    client.post("/api/challenge/toggle", json={"item_id": item_id})
    client.put("/api/challenge/logged",
               json={"item_id": item_id, "sets": 3, "reps": 6, "weight_kg": 82.5})

    client.put("/api/challenge/logged", json={"item_id": item_id, "weight_kg": None})

    row = _logged(conn, item_id)
    assert (row["sets"], row["reps"]) == (3, 6)
    assert row["weight_kg"] is None


def test_each_field_edited_in_turn_accumulates(client, conn):
    """Which is what actually happens: tap, type the weight, tab, type the reps."""
    item_id, _ = _exercise_item(client, conn)
    client.post("/api/challenge/toggle", json={"item_id": item_id})
    client.put("/api/challenge/logged", json={"item_id": item_id, "weight_kg": 95})
    client.put("/api/challenge/logged", json={"item_id": item_id, "reps": 5})
    client.put("/api/challenge/logged", json={"item_id": item_id, "sets": 4})

    row = _logged(conn, item_id)
    assert (row["sets"], row["reps"], row["weight_kg"]) == (4, 5, 95.0)
