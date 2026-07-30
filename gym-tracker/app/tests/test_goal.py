def test_goal_seeded(client):
    goal = client.get("/api/goal").get_json()
    assert goal["target_weight_kg"] == 105.0
    assert goal["target_body_fat_pct"] == 15.0
    assert goal["target_date"] == "2026-12-28"
    assert goal["start_weight_kg"] == 99.7


def test_update_goal(client):
    res = client.put(
        "/api/goal",
        json={"target_weight_kg": 102, "target_body_fat_pct": 12, "target_date": "2027-03-01"},
    )
    assert res.status_code == 200
    goal = client.get("/api/goal").get_json()
    assert goal["target_weight_kg"] == 102
    assert goal["target_body_fat_pct"] == 12
    assert goal["target_date"] == "2027-03-01"
    # start values are preserved when not supplied
    assert goal["start_weight_kg"] == 99.7


def test_update_goal_validation(client):
    assert client.put("/api/goal", json={"target_weight_kg": 100}).status_code == 400
    assert client.put(
        "/api/goal",
        json={"target_weight_kg": 100, "target_body_fat_pct": 15, "target_date": "bad"},
    ).status_code == 400


# --- Goal history ----------------------------------------------------------


def test_goal_history_starts_from_the_seeded_goal(client):
    history = client.get("/api/goal/history").get_json()
    assert len(history) == 1
    assert history[0]["source"] == "seed"
    assert history[0]["target_weight_kg"] == 105.0


def test_editing_the_goal_appends_a_version(client):
    client.put("/api/goal", json={
        "target_weight_kg": 102, "target_body_fat_pct": 14, "target_date": "2027-01-31",
    })
    history = client.get("/api/goal/history").get_json()

    assert len(history) == 2
    assert history[-1]["source"] == "edit"
    assert history[-1]["target_weight_kg"] == 102
    assert history[-1]["target_date"] == "2027-01-31"
    # The old target is still there: an edit no longer erases what it replaced.
    assert history[0]["target_weight_kg"] == 105.0


def test_resaving_the_same_goal_is_not_a_change(client):
    goal = client.get("/api/goal").get_json()
    client.put("/api/goal", json={
        "target_weight_kg": goal["target_weight_kg"],
        "target_body_fat_pct": goal["target_body_fat_pct"],
        "target_date": goal["target_date"],
    })
    assert len(client.get("/api/goal/history").get_json()) == 1
