def test_presets_seeded_and_grouped(client):
    groups = client.get("/api/exercises").get_json()
    equipment = [g["equipment"] for g in groups]
    assert equipment[:3] == ["Bodyweight", "Pull-up bar", "Dumbbells"]
    names = {ex["name"] for g in groups for ex in g["exercises"]}
    assert {"Push-up", "Pull-up", "Dumbbell curl"} <= names


def test_add_custom_exercise(client):
    res = client.post("/api/exercises", json={"name": "Kettlebell swing", "equipment": "Kettlebell"})
    assert res.status_code == 201
    groups = {g["equipment"]: g for g in client.get("/api/exercises").get_json()}
    assert "Kettlebell" in groups
    assert groups["Kettlebell"]["exercises"][0]["name"] == "Kettlebell swing"


def test_add_exercise_requires_name(client):
    assert client.post("/api/exercises", json={"name": "  "}).status_code == 400


def test_update_exercise(client):
    ex_id = client.post("/api/exercises", json={"name": "Band pull-apart", "equipment": "Bands"}).get_json()["id"]
    assert client.put(
        f"/api/exercises/{ex_id}", json={"name": "Band row", "equipment": "Bands"}
    ).status_code == 200
    names = {ex["name"] for g in client.get("/api/exercises").get_json() for ex in g["exercises"]}
    assert "Band row" in names and "Band pull-apart" not in names
    assert client.put("/api/exercises/999", json={"name": "x"}).status_code == 404


def test_unused_exercise_hard_deleted(client):
    ex_id = client.post("/api/exercises", json={"name": "Temp"}).get_json()["id"]
    assert client.delete(f"/api/exercises/{ex_id}").get_json()["status"] == "deleted"


def test_used_exercise_archived_not_deleted(client):
    groups = client.get("/api/exercises").get_json()
    ex_id = groups[0]["exercises"][0]["id"]
    client.post("/api/workouts", json={"exercise_id": ex_id, "sets": 3, "reps": 40})
    assert client.delete(f"/api/exercises/{ex_id}").get_json()["status"] == "archived"
    # gone from the active library, but its workout history remains
    active_ids = [ex["id"] for g in client.get("/api/exercises").get_json() for ex in g["exercises"]]
    assert ex_id not in active_ids
    history = client.get("/api/workouts").get_json()
    assert any(w["exercise_id"] == ex_id for w in history)


def test_workout_crud(client):
    groups = client.get("/api/exercises").get_json()
    ex_id = groups[0]["exercises"][0]["id"]
    res = client.post(
        "/api/workouts",
        json={"exercise_id": ex_id, "sets": 3, "reps": 12, "weight_kg": 10, "date": "2026-07-20"},
    )
    assert res.status_code == 201
    wid = res.get_json()["id"]

    rows = client.get("/api/workouts").get_json()
    assert rows[0]["id"] == wid
    assert rows[0]["exercise_name"]
    assert rows[0]["sets"] == 3 and rows[0]["reps"] == 12 and rows[0]["weight_kg"] == 10

    # filter by exercise + date
    assert len(client.get(f"/api/workouts?exercise_id={ex_id}").get_json()) == 1
    assert len(client.get("/api/workouts?date=2026-07-20").get_json()) == 1
    assert len(client.get("/api/workouts?date=2026-07-19").get_json()) == 0

    assert client.delete(f"/api/workouts/{wid}").status_code == 204
    assert client.get("/api/workouts").get_json() == []


def test_workout_edit(client):
    groups = client.get("/api/exercises").get_json()
    ex_id = groups[0]["exercises"][0]["id"]
    wid = client.post(
        "/api/workouts", json={"exercise_id": ex_id, "sets": 3, "reps": 10, "date": "2026-07-20"}
    ).get_json()["id"]

    res = client.put(
        f"/api/workouts/{wid}",
        json={"sets": 4, "reps": 12, "weight_kg": 12.5, "date": "2026-07-21", "notes": "felt strong"},
    )
    assert res.status_code == 200
    w = client.get("/api/workouts").get_json()[0]
    assert w["sets"] == 4 and w["reps"] == 12 and w["weight_kg"] == 12.5
    assert w["ts"].startswith("2026-07-21")
    assert w["notes"] == "felt strong"
    assert client.put("/api/workouts/999", json={"sets": 1}).status_code == 404


def test_exercise_referenced_by_challenge_is_archived(client):
    groups = client.get("/api/exercises").get_json()
    ex_id = groups[0]["exercises"][0]["id"]
    client.post("/api/challenge/items", json={"item_type": "exercise", "exercise_id": ex_id, "target_reps": 20})
    # even with no workout history, a challenge reference protects it from hard delete
    assert client.delete(f"/api/exercises/{ex_id}").get_json()["status"] == "archived"


def test_workout_validation(client):
    assert client.post("/api/workouts", json={}).status_code == 400
    assert client.post("/api/workouts", json={"exercise_id": 999}).status_code == 400
    groups = client.get("/api/exercises").get_json()
    ex_id = groups[0]["exercises"][0]["id"]
    assert client.post("/api/workouts", json={"exercise_id": ex_id, "reps": "lots"}).status_code == 400
    assert client.delete("/api/workouts/999").status_code == 404
