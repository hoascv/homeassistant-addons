"""Editing a workout, including the ones a challenge created.

The exercise was not editable until 1.32.1 — the form offered the choice, sent
it, and the handler dropped it, so the save appeared to work and changed
nothing. These pin that it now applies, and what it means for a row the
challenge owns.
"""
from datetime import date, timedelta


def _exercise_ids(client):
    groups = client.get("/api/exercises").get_json()
    return [ex["id"] for group in groups for ex in group["exercises"]]


def _workout(client, workout_id):
    return next(
        w for w in client.get("/api/workouts").get_json() if w["id"] == workout_id
    )


# --- the reported bug ---------------------------------------------------------


def test_the_exercise_can_be_changed(client):
    """The bug as reported: pick a different exercise, save, nothing happens."""
    first, second = _exercise_ids(client)[:2]
    workout_id = client.post(
        "/api/workouts", json={"exercise_id": first, "sets": 3, "reps": 10}
    ).get_json()["id"]

    res = client.put(
        f"/api/workouts/{workout_id}",
        json={"exercise_id": second, "sets": 3, "reps": 10},
    )
    assert res.status_code == 200
    assert _workout(client, workout_id)["exercise_id"] == second


def test_the_exercise_name_follows(client):
    """What the user actually sees in the history list."""
    first, second = _exercise_ids(client)[:2]
    workout_id = client.post(
        "/api/workouts", json={"exercise_id": first, "sets": 1}
    ).get_json()["id"]
    before = _workout(client, workout_id)["exercise_name"]

    client.put(f"/api/workouts/{workout_id}", json={"exercise_id": second})
    assert _workout(client, workout_id)["exercise_name"] != before


def test_omitting_the_exercise_leaves_it_alone(client):
    """A caller editing only the reps must not have the exercise cleared."""
    first = _exercise_ids(client)[0]
    workout_id = client.post(
        "/api/workouts", json={"exercise_id": first, "sets": 3, "reps": 10}
    ).get_json()["id"]

    client.put(f"/api/workouts/{workout_id}", json={"sets": 4, "reps": 12})
    row = _workout(client, workout_id)
    assert row["exercise_id"] == first
    assert row["sets"] == 4


def test_an_unknown_exercise_is_refused(client):
    first = _exercise_ids(client)[0]
    workout_id = client.post(
        "/api/workouts", json={"exercise_id": first, "sets": 1}
    ).get_json()["id"]

    res = client.put(f"/api/workouts/{workout_id}", json={"exercise_id": 999999})
    assert res.status_code == 400
    assert "no such exercise" in res.get_json()["error"]
    assert _workout(client, workout_id)["exercise_id"] == first


def test_a_non_numeric_exercise_is_refused(client):
    first = _exercise_ids(client)[0]
    workout_id = client.post(
        "/api/workouts", json={"exercise_id": first, "sets": 1}
    ).get_json()["id"]

    res = client.put(f"/api/workouts/{workout_id}", json={"exercise_id": "chest day"})
    assert res.status_code == 400


# --- rows the challenge created -----------------------------------------------


def _challenge_workout(client):
    """Tick an exercise item, which writes a workout with source='challenge'."""
    items = client.get("/api/challenge").get_json()["items"]
    item = next(i for i in items if i["item_type"] == "exercise")
    client.post("/api/challenge/toggle", json={"item_id": item["id"]})
    workout = next(
        w for w in client.get("/api/workouts").get_json() if w["source"] == "challenge"
    )
    return item, workout


def test_a_challenge_workout_can_have_its_exercise_changed(client):
    """The case that prompted this: the challenge logged it, and it was not
    what you actually did."""
    item, workout = _challenge_workout(client)
    other = next(i for i in _exercise_ids(client) if i != workout["exercise_id"])

    res = client.put(f"/api/workouts/{workout['id']}", json={"exercise_id": other})
    assert res.status_code == 200
    assert _workout(client, workout["id"])["exercise_id"] == other


def test_changing_the_exercise_detaches_it_from_the_challenge(client):
    """It no longer stands for that item, and saying it does would make
    un-ticking delete a workout the user deliberately edited."""
    item, workout = _challenge_workout(client)
    other = next(i for i in _exercise_ids(client) if i != workout["exercise_id"])

    res = client.put(f"/api/workouts/{workout['id']}", json={"exercise_id": other})
    assert res.get_json()["detached_from_challenge"] is True
    assert _workout(client, workout["id"])["source"] == "manual"


def test_an_edited_workout_survives_un_ticking_the_challenge(client):
    """The consequence that makes detaching the right answer rather than the
    tidy one. Un-ticking deletes the challenge's row by item and date; an
    edited row must not be collateral."""
    item, workout = _challenge_workout(client)
    other = next(i for i in _exercise_ids(client) if i != workout["exercise_id"])
    client.put(f"/api/workouts/{workout['id']}", json={"exercise_id": other})

    client.post("/api/challenge/toggle", json={"item_id": item["id"]})

    surviving = [w["id"] for w in client.get("/api/workouts").get_json()]
    assert workout["id"] in surviving, "the edited workout was deleted with the tick"


def test_moving_a_challenge_workout_to_another_day_also_detaches(client):
    """Same failure mode by a different route: un-ticking matches on the date
    too, so a row moved to another day would be orphaned but still claimed."""
    item, workout = _challenge_workout(client)
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    res = client.put(f"/api/workouts/{workout['id']}", json={"date": yesterday})
    assert res.get_json()["detached_from_challenge"] is True
    assert _workout(client, workout["id"])["source"] == "manual"


def test_editing_only_the_reps_keeps_it_attached(client):
    """Correcting a number is not a claim that it was a different exercise, so
    the row still belongs to the challenge and still disappears on un-tick."""
    item, workout = _challenge_workout(client)

    res = client.put(f"/api/workouts/{workout['id']}", json={"reps": 99})
    assert res.get_json()["detached_from_challenge"] is False
    assert _workout(client, workout["id"])["source"] == "challenge"

    client.post("/api/challenge/toggle", json={"item_id": item["id"]})
    assert workout["id"] not in [w["id"] for w in client.get("/api/workouts").get_json()]


def test_a_manual_workout_is_never_reported_as_detached(client):
    first, second = _exercise_ids(client)[:2]
    workout_id = client.post(
        "/api/workouts", json={"exercise_id": first, "sets": 1}
    ).get_json()["id"]

    res = client.put(f"/api/workouts/{workout_id}", json={"exercise_id": second})
    assert res.get_json()["detached_from_challenge"] is False
