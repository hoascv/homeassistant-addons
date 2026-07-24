from datetime import date, timedelta


def test_challenge_items_seeded(client):
    data = client.get("/api/challenge").get_json()
    items = data["items"]
    assert [i["item_type"] for i in items] == ["supplement", "exercise", "exercise"]
    assert [i["name"] for i in items] == ["Creatine", "Push-up", "Squat"]
    assert items[0]["label"] == "Creatine · 5 g"
    assert items[1]["label"] == "Push-up · 40 reps"
    assert data["streak"] == 0
    assert data["complete_today"] is False
    assert len(data["last_7_days"]) == 7


def test_toggle_marks_done(client):
    item_id = client.get("/api/challenge").get_json()["items"][0]["id"]
    res = client.post("/api/challenge/toggle", json={"item_id": item_id}).get_json()
    assert res["done"] is True
    data = client.get("/api/challenge").get_json()
    assert data["items"][0]["done_today"] is True
    # toggling again clears it
    res = client.post("/api/challenge/toggle", json={"item_id": item_id}).get_json()
    assert res["done"] is False


def test_complete_today_when_all_ticked(client):
    for item in client.get("/api/challenge").get_json()["items"]:
        client.post("/api/challenge/toggle", json={"item_id": item["id"]})
    data = client.get("/api/challenge").get_json()
    assert data["complete_today"] is True
    assert data["streak"] == 1


def test_streak_counts_consecutive_full_days(client, conn):
    items = [i["id"] for i in client.get("/api/challenge").get_json()["items"]]
    today = date.today()
    # Complete the 3 days *before* today fully; leave today untouched.
    for off in range(1, 4):
        day = (today - timedelta(days=off)).isoformat()
        for i in items:
            conn.execute("INSERT INTO challenge_completions (item_id, day) VALUES (?, ?)", (i, day))
    conn.commit()
    # An unfinished today should not break the 3-day streak behind it.
    assert client.get("/api/challenge").get_json()["streak"] == 3


def test_streak_breaks_on_partial_day(client, conn):
    items = [i["id"] for i in client.get("/api/challenge").get_json()["items"]]
    today = date.today()
    # yesterday fully complete, day-before only partially -> streak stops at 1
    for i in items:
        conn.execute(
            "INSERT INTO challenge_completions (item_id, day) VALUES (?, ?)",
            (i, (today - timedelta(days=1)).isoformat()),
        )
    conn.execute(
        "INSERT INTO challenge_completions (item_id, day) VALUES (?, ?)",
        (items[0], (today - timedelta(days=2)).isoformat()),
    )
    conn.commit()
    assert client.get("/api/challenge").get_json()["streak"] == 1


def _first_exercise_id(client):
    return client.get("/api/exercises").get_json()[0]["exercises"][0]["id"]


def _first_supplement_id(client):
    return client.get("/api/supplements").get_json()[0]["id"]


def test_add_exercise_challenge_item(client):
    ex_id = _first_exercise_id(client)
    res = client.post(
        "/api/challenge/items",
        json={"item_type": "exercise", "exercise_id": ex_id, "target_reps": 25},
    )
    assert res.status_code == 201
    new_id = res.get_json()["id"]
    item = next(i for i in client.get("/api/challenge/items").get_json() if i["id"] == new_id)
    assert item["item_type"] == "exercise"
    assert item["target_reps"] == 25
    assert item["label"].endswith("· 25 reps")

    # edit the rep target
    assert client.put(f"/api/challenge/items/{new_id}", json={"target_reps": 30}).status_code == 200
    item = next(i for i in client.get("/api/challenge/items").get_json() if i["id"] == new_id)
    assert item["target_reps"] == 30

    # archive
    assert client.delete(f"/api/challenge/items/{new_id}").status_code == 200
    assert new_id not in [i["id"] for i in client.get("/api/challenge/items").get_json()]


def test_add_supplement_challenge_item(client):
    sup_id = _first_supplement_id(client)
    res = client.post(
        "/api/challenge/items",
        json={"item_type": "supplement", "supplement_id": sup_id, "dose": "10 g"},
    )
    assert res.status_code == 201
    new_id = res.get_json()["id"]
    item = next(i for i in client.get("/api/challenge/items").get_json() if i["id"] == new_id)
    assert item["item_type"] == "supplement"
    assert item["dose"] == "10 g"


def test_challenge_item_type_validation(client):
    # free text / no type is rejected — items must reference the library
    assert client.post("/api/challenge/items", json={"label": "10 min stretch"}).status_code == 400
    assert client.post("/api/challenge/items", json={"item_type": "exercise"}).status_code == 400
    assert client.post("/api/challenge/items", json={"item_type": "exercise", "exercise_id": 999}).status_code == 400
    assert client.post("/api/challenge/items", json={"item_type": "supplement", "supplement_id": 999}).status_code == 400


def test_ticking_exercise_item_logs_and_unlogs_workout(client):
    ex_id = _first_exercise_id(client)
    item_id = client.post(
        "/api/challenge/items",
        json={"item_type": "exercise", "exercise_id": ex_id, "target_sets": 3, "target_reps": 40},
    ).get_json()["id"]

    # tick it on -> a workout appears, sourced from the challenge
    client.post("/api/challenge/toggle", json={"item_id": item_id, "day": "2026-07-20"})
    workouts = client.get("/api/workouts?date=2026-07-20").get_json()
    assert len(workouts) == 1
    assert workouts[0]["exercise_id"] == ex_id
    assert workouts[0]["sets"] == 3 and workouts[0]["reps"] == 40
    assert workouts[0]["source"] == "challenge"

    # tick it off -> the auto-logged workout is removed again
    client.post("/api/challenge/toggle", json={"item_id": item_id, "day": "2026-07-20"})
    assert client.get("/api/workouts?date=2026-07-20").get_json() == []


def test_ticking_supplement_item_logs_no_workout(client):
    sup_id = _first_supplement_id(client)
    item_id = client.post(
        "/api/challenge/items", json={"item_type": "supplement", "supplement_id": sup_id}
    ).get_json()["id"]
    client.post("/api/challenge/toggle", json={"item_id": item_id, "day": "2026-07-20"})
    assert client.get("/api/workouts?date=2026-07-20").get_json() == []


def test_challenge_history_matrix(client):
    data = client.get("/api/challenge/history?days=10").get_json()
    assert len(data["days"]) == 10
    assert len(data["items"]) == 3
    # newest first
    assert data["days"][0]["day"] > data["days"][1]["day"]
    # backfill a past day via toggle, then it shows in history
    item_id = data["items"][0]["id"]
    client.post("/api/challenge/toggle", json={"item_id": item_id, "day": data["days"][3]["day"]})
    refreshed = client.get("/api/challenge/history?days=10").get_json()
    assert item_id in refreshed["days"][3]["done"]


def test_archived_item_not_required_for_streak(client, conn):
    """Archiving an item removes it from the 'all items done' bar, so a day
    where the remaining items are done still counts."""
    data = client.get("/api/challenge").get_json()
    items = data["items"]
    # Complete only the first two items today, then archive the third.
    for item in items[:2]:
        client.post("/api/challenge/toggle", json={"item_id": item["id"]})
    assert client.get("/api/challenge").get_json()["complete_today"] is False
    client.delete(f"/api/challenge/items/{items[2]['id']}")
    assert client.get("/api/challenge").get_json()["complete_today"] is True


def test_challenge_validation(client):
    assert client.post("/api/challenge/toggle", json={}).status_code == 400
    assert client.post("/api/challenge/toggle", json={"item_id": 999}).status_code == 404
    assert client.post("/api/challenge/items", json={"label": "  "}).status_code == 400
    assert client.put("/api/challenge/items/999", json={"label": "x"}).status_code == 404
    assert client.delete("/api/challenge/items/999").status_code == 404
