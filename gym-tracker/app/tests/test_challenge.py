from datetime import date, timedelta


def test_challenge_items_seeded(client):
    data = client.get("/api/challenge").get_json()
    labels = [i["label"] for i in data["items"]]
    assert labels == ["Creatine 5 g", "40 push-ups", "40 squats"]
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


def test_add_edit_archive_challenge_item(client):
    new_id = client.post("/api/challenge/items", json={"label": "10 min stretch"}).get_json()["id"]
    labels = [i["label"] for i in client.get("/api/challenge/items").get_json()]
    assert "10 min stretch" in labels

    assert client.put(f"/api/challenge/items/{new_id}", json={"label": "15 min stretch"}).status_code == 200
    labels = [i["label"] for i in client.get("/api/challenge/items").get_json()]
    assert "15 min stretch" in labels and "10 min stretch" not in labels

    assert client.delete(f"/api/challenge/items/{new_id}").status_code == 200
    labels = [i["label"] for i in client.get("/api/challenge/items").get_json()]
    assert "15 min stretch" not in labels


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
