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
