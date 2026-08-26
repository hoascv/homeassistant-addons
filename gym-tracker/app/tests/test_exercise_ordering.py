"""Ordering of the exercise picker.

The list is what you scroll when logging, and an alphabet is the wrong order
for it: the three exercises somebody actually does should not sit below twenty
they have never touched.
"""
import pytest


def _log(client, exercise_id, count=1):
    for _ in range(count):
        res = client.post("/api/workouts", json={"exercise_id": exercise_id, "sets": 3, "reps": 10})
        assert res.status_code in (200, 201), res.get_json()


def _group_named(payload, equipment):
    for group in payload:
        if group["equipment"] == equipment:
            return [e["name"] for e in group["exercises"]]
    raise AssertionError(f"no {equipment!r} group in {[g['equipment'] for g in payload]}")


def _first_group_with_two(client):
    """A group holding at least two exercises, whatever the seeded library is."""
    payload = client.get("/api/exercises").get_json()
    for group in payload:
        if len(group["exercises"]) >= 2:
            return group
    pytest.skip("seeded library has no group with two exercises")


def test_exercises_expose_their_log_count(client):
    group = _first_group_with_two(client)
    exercise = group["exercises"][0]
    assert exercise["log_count"] == 0

    _log(client, exercise["id"], 2)
    refreshed = _group_named_entry(client, group["equipment"], exercise["name"])
    assert refreshed["log_count"] == 2


def _group_named_entry(client, equipment, name):
    payload = client.get("/api/exercises").get_json()
    for g in payload:
        if g["equipment"] == equipment:
            for e in g["exercises"]:
                if e["name"] == name:
                    return e
    raise AssertionError(f"{name!r} not found under {equipment!r}")


def test_the_most_logged_exercise_rises_to_the_top_of_its_group(client):
    group = _first_group_with_two(client)
    equipment = group["equipment"]
    before = _group_named(client.get("/api/exercises").get_json(), equipment)

    # Log the one that sorts last alphabetically.
    last_name = before[-1]
    last = next(e for e in group["exercises"] if e["name"] == last_name)
    _log(client, last["id"], 3)

    after = _group_named(client.get("/api/exercises").get_json(), equipment)
    assert after[0] == last_name
    assert after[0] != before[0] or len(before) == 1


def test_never_logged_exercises_keep_alphabetical_order(client):
    """Only what you have actually done moves. Everything else stays where it
    has always been, so the list does not become unrecognisable."""
    group = _first_group_with_two(client)
    equipment = group["equipment"]
    names = _group_named(client.get("/api/exercises").get_json(), equipment)
    assert names == sorted(names)  # nothing logged yet

    logged = next(e for e in group["exercises"] if e["name"] == names[-1])
    _log(client, logged["id"])

    after = _group_named(client.get("/api/exercises").get_json(), equipment)
    rest = [n for n in after if n != names[-1]]
    assert rest == sorted(rest)


def test_ordering_is_by_count_not_recency(client):
    """Most logged, not most recent — a one-off yesterday should not displace
    the thing done every week."""
    group = _first_group_with_two(client)
    a, b = group["exercises"][0], group["exercises"][1]
    _log(client, a["id"], 5)
    _log(client, b["id"], 1)  # logged more recently, but far less often

    after = _group_named(client.get("/api/exercises").get_json(), group["equipment"])
    assert after.index(a["name"]) < after.index(b["name"])


def test_equipment_grouping_is_unchanged(client):
    """The grouping people navigate by must not move — only the order inside."""
    before = [g["equipment"] for g in client.get("/api/exercises").get_json()]
    group = _first_group_with_two(client)
    _log(client, group["exercises"][-1]["id"], 4)
    after = [g["equipment"] for g in client.get("/api/exercises").get_json()]
    assert after == before
