from datetime import date, timedelta

import app as gymapp


def test_templates_are_listed_with_their_shape(client):
    data = client.get("/api/challenge-templates").get_json()
    tpl = next(t for t in data["templates"] if t["id"] == "kegel-advanced")
    assert tpl["days"] == 30
    assert [i["name"] for i in tpl["items"]] == [
        "Kegel warm-up",
        "Kegel endurance hold",
        "Kegel cool-down",
    ]
    warmup, endurance, cooldown = tpl["items"]
    assert (warmup["rounds"], warmup["seconds"]) == (10, 20)      # 10 × (1s + 1s)
    assert (endurance["rounds"], endurance["seconds"]) == (10, 200)  # 10 × (10s + 10s)
    assert endurance["sets"] == 2
    assert (cooldown["rounds"], cooldown["seconds"]) == (20, 40)  # 20 × (1s + 1s)
    # the endurance block counts twice, once per set
    assert tpl["total_seconds"] == 20 + 200 * 2 + 40


def test_starting_a_template_creates_the_challenge_and_its_items(client):
    res = client.post("/api/challenges/from-template", json={"template": "kegel-advanced"})
    assert res.status_code == 201
    challenge_id = res.get_json()["id"]

    challenges = client.get("/api/challenges").get_json()
    ch = next(c for c in challenges if c["id"] == challenge_id)
    assert ch["name"] == "Advanced Kegel"
    assert ch["start_date"] == date.today().isoformat()
    assert ch["end_date"] == (date.today() + timedelta(days=29)).isoformat()

    # The label is rebuilt from the live routine, so it states the real work:
    # duration, rounds, and — for the endurance block — that it runs twice.
    labels = [i["label"] for i in ch["items"]]
    assert labels == [
        "Kegel warm-up · 20s · 10 rounds",
        "Kegel endurance hold · 2 sets · 3m 20s · 10 rounds",
        "Kegel cool-down · 40s · 20 rounds",
    ]


def test_started_items_are_playable_routines(client, conn):
    res = client.post("/api/challenges/from-template", json={"template": "kegel-advanced"})
    challenge_id = res.get_json()["id"]
    ch = next(
        c for c in client.get("/api/challenges").get_json()
        if c["id"] == challenge_id
    )
    # is_routine is what puts the ▶ button on the item, so the player can run it.
    assert all(i["is_routine"] for i in ch["items"])

    endurance = ch["items"][1]
    routine = client.get(f"/api/exercises/{endurance['exercise_id']}/routine").get_json()
    assert routine["rounds"] == 10
    assert [(s["kind"], s["seconds"]) for s in routine["steps"]] == [("work", 10), ("rest", 10)]
    assert routine["steps"][0]["name"] == "Contract"


def test_step_exercises_are_created_once_and_reused(client, conn):
    client.post("/api/challenges/from-template", json={"template": "kegel-advanced"})
    client.post("/api/challenges/from-template", json={"template": "kegel-advanced"})
    for name in ("Contract", "Relax", "Kegel warm-up", "Kegel endurance hold"):
        n = conn.execute(
            "SELECT COUNT(*) FROM exercises WHERE archived = 0 AND LOWER(name) = LOWER(?)",
            (name,),
        ).fetchone()[0]
        assert n == 1, f"{name} was created {n} times"


def test_an_edited_routine_is_not_overwritten_by_a_second_start(client, conn):
    client.post("/api/challenges/from-template", json={"template": "kegel-advanced"})
    exercise_id = conn.execute(
        "SELECT id FROM exercises WHERE name = 'Kegel endurance hold'"
    ).fetchone()["id"]
    client.put(
        f"/api/exercises/{exercise_id}/routine",
        json={"rounds": 4, "steps": [{"kind": "work", "seconds": 15, "label": "Hold"}]},
    )
    client.post("/api/challenges/from-template", json={"template": "kegel-advanced"})
    routine = client.get(f"/api/exercises/{exercise_id}/routine").get_json()
    assert routine["rounds"] == 4
    assert len(routine["steps"]) == 1


def test_start_date_and_name_can_be_overridden(client):
    res = client.post(
        "/api/challenges/from-template",
        json={"template": "kegel-advanced", "start_date": "2026-09-01", "name": "Pelvic floor"},
    )
    challenge_id = res.get_json()["id"]
    ch = next(
        c for c in client.get("/api/challenges").get_json()
        if c["id"] == challenge_id
    )
    assert ch["name"] == "Pelvic floor"
    assert ch["start_date"] == "2026-09-01"
    assert ch["end_date"] == "2026-09-30"


def test_unknown_template_is_refused(client):
    res = client.post("/api/challenges/from-template", json={"template": "nope"})
    assert res.status_code == 400
    assert "no such template" in res.get_json()["error"]


def test_every_template_is_internally_consistent():
    """An item naming a routine the template doesn't define would only blow up
    when somebody pressed Start."""
    seen = set()
    for tpl in gymapp.CHALLENGE_TEMPLATES:
        assert tpl["id"] not in seen, f"duplicate template id {tpl['id']}"
        seen.add(tpl["id"])
        routines = {r["name"] for r in tpl["routines"]}
        for item in tpl["items"]:
            assert item["routine"] in routines, f"{tpl['id']}: no routine {item['routine']}"
        for routine in tpl["routines"]:
            assert 1 <= routine["rounds"] <= gymapp.MAX_ROUTINE_ROUNDS
            assert routine["steps"], f"{routine['name']} has no steps"
            for step in routine["steps"]:
                assert step["kind"] in gymapp.ROUTINE_STEP_KINDS
                assert 1 <= step["seconds"] <= gymapp.MAX_STEP_SECONDS
                # a work step needs something to show; a rest never does
                assert bool(step.get("exercise")) == (step["kind"] == "work")
