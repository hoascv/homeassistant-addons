def test_starting_weight_seeded(client):
    data = client.get("/api/weight").get_json()
    assert data["current_weight_kg"] == 99.7
    assert len(data["logs"]) == 1
    assert data["logs"][0]["ts"].startswith("2026-07-03")


def test_add_weight_and_progress(client):
    client.post("/api/weight", json={"weight_kg": 100.2, "body_fat_pct": 20, "date": "2026-07-20"})
    data = client.get("/api/weight").get_json()
    assert data["current_weight_kg"] == 100.2
    assert data["current_body_fat_pct"] == 20
    # lean mass = 100.2 * (1 - 0.20)
    assert data["lean_mass_kg"] == 80.2
    # progress from 99.7 -> 105, currently 100.2
    assert data["weight_to_target_kg"] == 4.8
    assert 0 < data["weight_progress_pct"] < 100
    assert data["days_remaining"] is not None


def test_add_weight_without_body_fat(client):
    client.post("/api/weight", json={"weight_kg": 101})
    data = client.get("/api/weight").get_json()
    assert data["current_weight_kg"] == 101
    assert data["current_body_fat_pct"] is None
    assert data["lean_mass_kg"] is None


def test_add_weight_validation(client):
    assert client.post("/api/weight", json={}).status_code == 400
    assert client.post("/api/weight", json={"weight_kg": "heavy"}).status_code == 400
    assert client.post("/api/weight", json={"weight_kg": 1000}).status_code == 400
    assert client.post("/api/weight", json={"weight_kg": 90, "body_fat_pct": 150}).status_code == 400
    assert client.post("/api/weight", json={"weight_kg": 90, "date": "nope"}).status_code == 400


def test_edit_and_delete_weight(client):
    wid = client.post("/api/weight", json={"weight_kg": 100}).get_json()["id"]
    assert client.put(f"/api/weight/{wid}", json={"weight_kg": 100.5, "body_fat_pct": 21}).status_code == 200
    logs = {l["id"]: l for l in client.get("/api/weight").get_json()["logs"]}
    assert logs[wid]["weight_kg"] == 100.5
    assert logs[wid]["body_fat_pct"] == 21

    assert client.delete(f"/api/weight/{wid}").status_code == 204
    ids = [l["id"] for l in client.get("/api/weight").get_json()["logs"]]
    assert wid not in ids


def test_weight_device_stored_and_editable(client):
    wid = client.post(
        "/api/weight", json={"weight_kg": 100, "body_fat_pct": 22, "device": "Home scale"}
    ).get_json()["id"]
    logs = {l["id"]: l for l in client.get("/api/weight").get_json()["logs"]}
    assert logs[wid]["device"] == "Home scale"

    # Blank device stores NULL; editing can set/clear it.
    wid2 = client.post("/api/weight", json={"weight_kg": 101, "device": "  "}).get_json()["id"]
    logs = {l["id"]: l for l in client.get("/api/weight").get_json()["logs"]}
    assert logs[wid2]["device"] is None

    client.put(f"/api/weight/{wid}", json={"weight_kg": 100, "device": "DEXA clinic"})
    logs = {l["id"]: l for l in client.get("/api/weight").get_json()["logs"]}
    assert logs[wid]["device"] == "DEXA clinic"


def test_weight_404s(client):
    assert client.put("/api/weight/999", json={"weight_kg": 100}).status_code == 404
    assert client.delete("/api/weight/999").status_code == 404


def test_forecast_needs_two_points(client):
    # Only the seeded starting weight exists -> not enough to trend.
    fc = client.get("/api/weight").get_json()["forecast"]
    assert fc["available"] is False
    assert fc["status"] == "insufficient"


def test_forecast_on_track_for_steady_gain(client, conn):
    # Seed weight is 99.7 on 2026-07-03; add a steady climb toward 105.
    for day, w in [("2026-07-17", 100.6), ("2026-07-31", 101.5), ("2026-08-14", 102.4)]:
        conn.execute("INSERT INTO weight_logs (ts, weight_kg) VALUES (?, ?)", (f"{day}T08:00:00", w))
    conn.commit()
    fc = client.get("/api/weight").get_json()["forecast"]
    assert fc["available"] is True
    assert fc["slope_per_week"] > 0
    assert fc["status"] in ("on_track", "ahead")
    # trend endpoints span the first log to the target date
    assert fc["trend"][0]["ts"] == "2026-07-03"
    assert fc["trend"][1]["ts"] == "2026-12-28"


def test_forecast_off_track_when_moving_away(client, conn):
    # Losing weight while the goal is to bulk to 105 -> off track.
    for day, w in [("2026-07-17", 99.3), ("2026-07-31", 98.9), ("2026-08-14", 98.4)]:
        conn.execute("INSERT INTO weight_logs (ts, weight_kg) VALUES (?, ?)", (f"{day}T08:00:00", w))
    conn.commit()
    fc = client.get("/api/weight").get_json()["forecast"]
    assert fc["status"] == "off_track"
    assert fc["slope_per_week"] < 0
    assert fc["projected_date"] is None
