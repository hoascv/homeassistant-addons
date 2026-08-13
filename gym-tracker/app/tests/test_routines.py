"""Routines: an exercise made of timed steps.

The rules that decide what a routine *is* live on the server, deliberately —
there is no JS test harness in this add-on, so anything that can be settled here
is the part that can be trusted.
"""


def _new_exercise(client, name="Tabata"):
    res = client.post("/api/exercises", json={"name": name, "equipment": "Bodyweight"})
    assert res.status_code == 201, res.get_json()
    return res.get_json()["id"]


def _first_exercise_id(client, exclude=()):
    for group in client.get("/api/exercises").get_json():
        for ex in group["exercises"]:
            if ex["id"] not in exclude:
                return ex["id"]
    raise AssertionError("no exercises seeded")


def _save(client, exercise_id, steps, rounds=1):
    return client.put(f"/api/exercises/{exercise_id}/routine",
                      json={"rounds": rounds, "steps": steps})


def _work(seconds=20, **over):
    step = {"kind": "work", "seconds": seconds, "label": "Jumping jacks"}
    step.update(over)
    return step


def _rest(seconds=10):
    return {"kind": "rest", "seconds": seconds}


# --- becoming a routine -------------------------------------------------------


def test_saving_steps_makes_it_a_routine_and_clearing_them_unmakes_it(client, db_path):
    """`is_routine` is derived from the steps, never declared, so the two can
    never disagree."""
    ex = _new_exercise(client)
    saved = _save(client, ex, [_work(), _rest()], rounds=8).get_json()
    assert saved["is_routine"] is True
    assert len(saved["steps"]) == 2

    cleared = _save(client, ex, [], rounds=1).get_json()
    assert cleared["is_routine"] is False
    assert cleared["steps"] == []


def test_a_routine_is_timed(client, db_path, conn):
    """Every branch on `measure` in this app is `duration ? seconds : reps`, so
    a routine has to be duration or its target and its logged rows would mean
    the wrong thing."""
    ex = _new_exercise(client)
    _save(client, ex, [_work()])
    assert conn.execute("SELECT measure FROM exercises WHERE id = ?", (ex,)).fetchone()[0] == "duration"


def test_a_routine_cannot_be_switched_back_to_reps(client, db_path):
    ex = _new_exercise(client)
    _save(client, ex, [_work()])
    refused = client.put(f"/api/exercises/{ex}", json={"measure": "reps"})
    assert refused.status_code == 400
    assert "routine is timed" in refused.get_json()["error"]


def test_an_exercise_with_no_steps_is_not_a_routine(client, db_path):
    ex = _new_exercise(client)
    assert client.get(f"/api/exercises/{ex}/routine").get_json()["is_routine"] is False


# --- the total ----------------------------------------------------------------


def test_the_total_is_the_rounds_times_the_sum_of_the_steps(client, db_path):
    """Tabata: 8 × (20s work + 10s rest) is four minutes exactly. Every step
    runs, including the last rest, so the arithmetic is checkable at a glance."""
    ex = _new_exercise(client)
    view = _save(client, ex, [_work(20), _rest(10)], rounds=8).get_json()
    assert view["round_seconds"] == 30
    assert view["total_seconds"] == 240


def test_the_challenge_label_says_the_length_and_the_rounds(client, db_path):
    ex = _new_exercise(client, "Tabata")
    _save(client, ex, [_work(20), _rest(10)], rounds=8)
    challenge_id = client.get("/api/challenges").get_json()[0]["id"]
    client.post("/api/challenge/items", json={
        "item_type": "exercise", "exercise_id": ex, "challenge_id": challenge_id,
    })

    item = next(
        i for c in client.get("/api/challenges").get_json() for i in c["items"]
        if i["exercise_id"] == ex
    )
    assert item["label"] == "Tabata · 4m · 8 rounds"
    assert item["is_routine"] is True
    assert item["routine_seconds"] == 240


# --- what a step may be -------------------------------------------------------


def test_a_rest_step_needs_neither_an_exercise_nor_a_name(client, db_path):
    ex = _new_exercise(client)
    view = _save(client, ex, [_rest(15)]).get_json()
    assert view["steps"][0]["name"] is None
    assert view["steps"][0]["kind"] == "rest"


def test_a_work_step_takes_its_name_from_the_exercise_it_references(client, db_path):
    ex = _new_exercise(client)
    referenced = _first_exercise_id(client, exclude={ex})
    name = next(
        e["name"] for g in client.get("/api/exercises").get_json()
        for e in g["exercises"] if e["id"] == referenced
    )
    view = _save(client, ex, [_work(label=None, step_exercise_id=referenced)]).get_json()
    assert view["steps"][0]["name"] == name


def test_a_referenced_step_follows_a_rename(client, db_path):
    """The live-name doctrine the challenge items already follow: renaming an
    exercise updates it everywhere rather than leaving a stale copy."""
    ex = _new_exercise(client)
    referenced = _first_exercise_id(client, exclude={ex})
    _save(client, ex, [_work(label=None, step_exercise_id=referenced)])

    client.put(f"/api/exercises/{referenced}", json={"name": "Star jumps"})
    view = client.get(f"/api/exercises/{ex}/routine").get_json()
    assert view["steps"][0]["name"] == "Star jumps"


def test_a_work_step_needs_exactly_one_of_a_reference_and_a_name(client, db_path):
    ex = _new_exercise(client)
    referenced = _first_exercise_id(client, exclude={ex})

    neither = _save(client, ex, [_work(label=None)])
    assert neither.status_code == 400
    assert "either an exercise or a name" in neither.get_json()["error"]

    both = _save(client, ex, [_work(step_exercise_id=referenced)])
    assert both.status_code == 400


def test_a_step_cannot_reference_another_routine(client, db_path):
    """Nesting would be an unbounded timeline and an unbounded recursion in the
    total."""
    inner = _new_exercise(client, "Inner")
    _save(client, inner, [_work()])
    outer = _new_exercise(client, "Outer")

    refused = _save(client, outer, [_work(label=None, step_exercise_id=inner)])
    assert refused.status_code == 400
    assert "another routine" in refused.get_json()["error"]


def test_a_step_referencing_nothing_real_is_refused(client, db_path):
    ex = _new_exercise(client)
    refused = _save(client, ex, [_work(label=None, step_exercise_id=99999)])
    assert refused.status_code == 400
    assert "no such exercise" in refused.get_json()["error"]


# --- bounds -------------------------------------------------------------------


def test_rounds_and_seconds_are_bounded_at_both_ends(client, db_path):
    ex = _new_exercise(client)
    assert _save(client, ex, [_work()], rounds=0).status_code == 400
    assert _save(client, ex, [_work()], rounds=100).status_code == 400
    assert _save(client, ex, [_work()], rounds="lots").status_code == 400
    assert _save(client, ex, [_work(0)]).status_code == 400
    assert _save(client, ex, [_work(3601)]).status_code == 400
    assert _save(client, ex, [{"kind": "work", "seconds": "abc", "label": "x"}]).status_code == 400


def test_a_bad_kind_is_refused(client, db_path):
    ex = _new_exercise(client)
    refused = _save(client, ex, [{"kind": "sprint", "seconds": 10, "label": "x"}])
    assert refused.status_code == 400
    assert "work" in refused.get_json()["error"]


def test_too_many_steps_is_refused(client, db_path):
    ex = _new_exercise(client)
    refused = _save(client, ex, [_work(10) for _ in range(51)])
    assert refused.status_code == 400
    assert "at most 50" in refused.get_json()["error"]


def test_a_routine_for_an_exercise_that_does_not_exist_is_a_404(client, db_path):
    assert client.get("/api/exercises/9999/routine").status_code == 404
    assert _save(client, 9999, [_work()]).status_code == 404


# --- editing ------------------------------------------------------------------


def test_re_saving_updates_inserts_and_deletes_without_churning_the_feed(client, db_path, conn):
    """The reason the save diffs by id rather than rewriting the list: a rewrite
    would emit two change rows per step on every edit and churn ids the
    lakehouse has already merged."""
    ex = _new_exercise(client)
    first = _save(client, ex, [_work(20), _rest(10), _work(30)]).get_json()
    ids = [s["id"] for s in first["steps"]]
    before = conn.execute(
        "SELECT COUNT(*) FROM change_log WHERE table_name = 'routine_steps'"
    ).fetchone()[0]

    # Keep the first, change the second, drop the third, add a fourth.
    second = _save(client, ex, [
        {"id": ids[0], "kind": "work", "seconds": 20, "label": "Jumping jacks"},
        {"id": ids[1], "kind": "rest", "seconds": 15},
        _work(45, label="Burpees"),
    ]).get_json()

    kept = [s["id"] for s in second["steps"]]
    assert kept[0] == ids[0], "an untouched step lost its id"
    assert kept[1] == ids[1]
    assert ids[2] not in kept, "the dropped step survived"
    assert second["steps"][1]["seconds"] == 15

    after = conn.execute(
        "SELECT COUNT(*) FROM change_log WHERE table_name = 'routine_steps'"
    ).fetchone()[0]
    # Two updates, one delete, one insert — not six rows for a rewrite.
    assert after - before <= 4


def test_the_order_sent_is_the_order_stored(client, db_path):
    ex = _new_exercise(client)
    view = _save(client, ex, [_work(20, label="A"), _work(30, label="B"), _work(40, label="C")]).get_json()
    assert [s["name"] for s in view["steps"]] == ["A", "B", "C"]
    assert [s["sort_order"] for s in view["steps"]] == [0, 1, 2]

    reordered = _save(client, ex, [
        {"id": view["steps"][2]["id"], "kind": "work", "seconds": 40, "label": "C"},
        {"id": view["steps"][0]["id"], "kind": "work", "seconds": 20, "label": "A"},
        {"id": view["steps"][1]["id"], "kind": "work", "seconds": 30, "label": "B"},
    ]).get_json()
    assert [s["name"] for s in reordered["steps"]] == ["C", "A", "B"]


# --- the rest of the app ------------------------------------------------------


def test_an_exercise_used_only_by_a_routine_step_is_archived_not_deleted(client, db_path, conn):
    """Otherwise it is hard-deleted and the step is left pointing at nothing."""
    ex = _new_exercise(client, "Holder")
    referenced = _new_exercise(client, "Only in a routine")
    _save(client, ex, [_work(label=None, step_exercise_id=referenced)])

    assert client.delete(f"/api/exercises/{referenced}").get_json()["status"] == "archived"
    row = conn.execute("SELECT archived FROM exercises WHERE id = ?", (referenced,)).fetchone()
    assert row["archived"] == 1
    # And the step still names it, because the join does not filter archived.
    assert client.get(f"/api/exercises/{ex}/routine").get_json()["steps"][0]["name"] == "Only in a routine"


def test_routine_steps_reach_the_feed_and_the_stats(client, db_path):
    import app as gymapp

    assert "routine_steps" in gymapp.TRACKED_TABLES
    ex = _new_exercise(client)
    _save(client, ex, [_work(), _rest()])

    assert client.get("/api/stats").get_json()["counts"]["routine_steps"] == 2
    assert "routine_steps" in client.get("/api/export").get_json()["tables"]
