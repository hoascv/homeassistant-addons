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


def test_body_fat_forecast_needs_two_readings(client, conn):
    # Body fat is logged less often than weight: enough weigh-ins to trend the
    # weight, only one body-fat reading -> no body-fat projection.
    for day, w in [("2026-07-17", 100.6), ("2026-07-31", 101.5)]:
        conn.execute("INSERT INTO weight_logs (ts, weight_kg) VALUES (?, ?)", (f"{day}T08:00:00", w))
    conn.execute(
        "INSERT INTO weight_logs (ts, weight_kg, body_fat_pct) VALUES (?, ?, ?)",
        ("2026-08-14T08:00:00", 102.4, 21.0),
    )
    conn.commit()
    fc = client.get("/api/weight").get_json()["forecast"]
    assert fc["available"] is True
    assert fc["bf_available"] is False


def test_body_fat_forecast_projects_to_goal_date(client, conn):
    # Steady 21 -> 19 % cut; the goal target is 15 % on 2026-12-28.
    for day, bf in [("2026-07-17", 21.0), ("2026-07-31", 20.0), ("2026-08-14", 19.0)]:
        conn.execute(
            "INSERT INTO weight_logs (ts, weight_kg, body_fat_pct) VALUES (?, ?, ?)",
            (f"{day}T08:00:00", 100.0, bf),
        )
    conn.commit()
    fc = client.get("/api/weight").get_json()["forecast"]
    assert fc["bf_available"] is True
    assert fc["bf_slope_per_week"] < 0
    # Trend endpoints share the weight forecast's target date, so both panels
    # of the chart line up on one x-axis.
    assert fc["bf_trend"][0]["ts"] == "2026-07-17"
    assert fc["bf_trend"][1]["ts"] == "2026-12-28"
    assert fc["bf_trend"][1]["body_fat_pct"] == fc["bf_projected_pct"]


def test_body_fat_forecast_survives_missing_weight_trend(client, conn):
    # Two body-fat readings on the same day as the only other weigh-in still
    # give a body-fat trend even where the weight trend is undefined.
    conn.execute(
        "INSERT INTO weight_logs (ts, weight_kg, body_fat_pct) VALUES (?, ?, ?)",
        ("2026-07-03T09:00:00", 99.7, 22.0),
    )
    conn.execute(
        "INSERT INTO weight_logs (ts, weight_kg, body_fat_pct) VALUES (?, ?, ?)",
        ("2026-08-14T08:00:00", 99.7, 20.0),
    )
    conn.commit()
    fc = client.get("/api/weight").get_json()["forecast"]
    assert fc["bf_available"] is True
    assert fc["bf_slope_per_week"] < 0


def test_forecast_off_track_when_moving_away(client, conn):
    # Losing weight while the goal is to bulk to 105 -> off track.
    for day, w in [("2026-07-17", 99.3), ("2026-07-31", 98.9), ("2026-08-14", 98.4)]:
        conn.execute("INSERT INTO weight_logs (ts, weight_kg) VALUES (?, ?)", (f"{day}T08:00:00", w))
    conn.commit()
    fc = client.get("/api/weight").get_json()["forecast"]
    assert fc["status"] == "off_track"
    assert fc["slope_per_week"] < 0
    assert fc["projected_date"] is None


# --- Timestamps: real moment vs day placeholder -----------------------------


def test_logging_today_records_the_time_not_midday(client, conn):
    from datetime import date, datetime

    client.post("/api/weight", json={"weight_kg": 100.4, "date": date.today().isoformat()})
    row = conn.execute("SELECT ts, ts_exact FROM weight_logs ORDER BY id DESC LIMIT 1").fetchone()

    assert not row["ts"].endswith("T12:00:00")
    assert row["ts_exact"] == 1
    assert abs((datetime.now() - datetime.fromisoformat(row["ts"])).total_seconds()) < 60


def test_backdated_entries_stay_marked_inexact(client, conn):
    client.post("/api/weight", json={"weight_kg": 100.4, "date": "2026-07-10"})
    row = conn.execute("SELECT ts, ts_exact FROM weight_logs ORDER BY id DESC LIMIT 1").fetchone()

    # No way to know when it happened, so the placeholder stays and says so.
    assert row["ts"] == "2026-07-10T12:00:00"
    assert row["ts_exact"] == 0


def test_migration_marks_old_midday_rows_inexact(db_path):
    import sqlite3

    import app as gymapp

    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE weight_logs DROP COLUMN ts_exact")
    conn.execute(
        "INSERT INTO weight_logs (ts, weight_kg) VALUES ('2026-07-02T12:00:00', 99.0)"
    )
    conn.execute(
        "INSERT INTO weight_logs (ts, weight_kg) VALUES ('2026-07-02T07:41:12', 99.1)"
    )
    conn.commit()

    gymapp._migrate_columns(conn)
    conn.commit()

    rows = {
        r[0]: r[1]
        for r in conn.execute("SELECT ts, ts_exact FROM weight_logs WHERE ts LIKE '2026-07-02%'")
    }
    conn.close()
    assert rows["2026-07-02T12:00:00"] == 0  # the old placeholder
    assert rows["2026-07-02T07:41:12"] == 1  # a real clock reading

    conn2 = sqlite3.connect(db_path)
    seeded = conn2.execute(
        "SELECT ts_exact FROM weight_logs WHERE notes = 'Starting weight'"
    ).fetchone()
    conn2.close()
    assert seeded[0] == 0  # the seeded starting weight is a stand-in, not a weigh-in
