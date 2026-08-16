def test_profile_defaults_to_empty(client):
    profile = client.get("/api/profile").get_json()
    assert profile["sex"] is None
    assert profile["age"] is None
    assert profile["activity_level"] is None
    assert profile["activity_level_set_at"] is None


def test_update_profile(client):
    res = client.put(
        "/api/profile",
        json={"sex": "male", "age": 34, "activity_level": 3, "activity_level_set_at": "2026-08-10"},
    )
    assert res.status_code == 200
    profile = client.get("/api/profile").get_json()
    assert profile["sex"] == "male"
    assert profile["age"] == 34
    assert profile["activity_level"] == 3
    assert profile["activity_level_set_at"] == "2026-08-10"


def test_update_profile_validation(client):
    assert client.put("/api/profile", json={"sex": "other"}).status_code == 400
    assert client.put("/api/profile", json={"age": 200}).status_code == 400
    assert client.put("/api/profile", json={"activity_level": 9}).status_code == 400
    assert client.put("/api/profile", json={"activity_level_set_at": "not-a-date"}).status_code == 400


def test_profile_fields_are_independently_clearable(client):
    client.put("/api/profile", json={"sex": "female", "age": 40, "activity_level": 2})
    client.put("/api/profile", json={})
    profile = client.get("/api/profile").get_json()
    assert profile["sex"] is None
    assert profile["age"] is None
    assert profile["activity_level"] is None


# --- Calibration -------------------------------------------------------------


def test_calibration_empty_by_default(client):
    summary = client.get("/api/bf-calibration").get_json()
    assert summary["count"] == 0
    assert summary["offset_pct"] is None
    assert summary["spread_pct"] is None


def test_add_calibration_readings_and_compute_offset(client):
    client.post("/api/bf-calibration", json={"old_bf_pct": 20.0, "new_bf_pct": 22.0})
    client.post("/api/bf-calibration", json={"old_bf_pct": 21.0, "new_bf_pct": 22.6})
    summary = client.get("/api/bf-calibration").get_json()
    assert summary["count"] == 2
    # deltas: 2.0 and 1.6 -> mean 1.8, spread 0.4
    assert summary["offset_pct"] == 1.8
    assert summary["spread_pct"] == 0.4
    assert len(summary["readings"]) == 2


def test_calibration_validation(client):
    assert client.post("/api/bf-calibration", json={"old_bf_pct": 20}).status_code == 400
    assert client.post(
        "/api/bf-calibration", json={"old_bf_pct": 200, "new_bf_pct": 20}
    ).status_code == 400


def test_delete_calibration_reading(client):
    client.post("/api/bf-calibration", json={"old_bf_pct": 20.0, "new_bf_pct": 22.0})
    reading_id = client.get("/api/bf-calibration").get_json()["readings"][0]["id"]
    res = client.delete(f"/api/bf-calibration/{reading_id}")
    assert res.status_code == 200
    assert res.get_json()["count"] == 0
    assert client.delete(f"/api/bf-calibration/{reading_id}").status_code == 404


# --- Bulk correction -----------------------------------------------------


def _seed_weight_history(client):
    # Two historical entries (before the switch) and one recent entry (after).
    client.post("/api/weight", json={"weight_kg": 90, "body_fat_pct": 20.0, "date": "2026-01-01"})
    client.post("/api/weight", json={"weight_kg": 89, "body_fat_pct": 19.0, "date": "2026-02-01"})
    client.post("/api/weight", json={"weight_kg": 88, "body_fat_pct": 18.0, "date": "2026-08-15"})


def test_apply_correction_requires_calibration_or_override(client):
    _seed_weight_history(client)
    res = client.post("/api/bf-correction/apply", json={"cutoff_date": "2026-08-01"})
    assert res.status_code == 400


def test_apply_correction_shifts_only_historical_rows(client):
    _seed_weight_history(client)
    client.post("/api/bf-calibration", json={"old_bf_pct": 20.0, "new_bf_pct": 22.0})  # offset +2.0

    res = client.post("/api/bf-correction/apply", json={"cutoff_date": "2026-08-01"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["rows_affected"] == 2

    logs = client.get("/api/weight").get_json()["logs"]
    by_date = {l["ts"][:10]: l for l in logs}
    assert by_date["2026-01-01"]["body_fat_pct"] == 22.0
    assert by_date["2026-01-01"]["body_fat_pct_raw"] == 20.0
    assert by_date["2026-01-01"]["bf_correction_id"] is not None
    assert by_date["2026-02-01"]["body_fat_pct"] == 21.0
    # the recent entry, after the cutoff, is untouched
    assert by_date["2026-08-15"]["body_fat_pct"] == 18.0
    assert by_date["2026-08-15"]["body_fat_pct_raw"] is None


def test_applying_correction_twice_does_not_double_apply(client):
    _seed_weight_history(client)
    client.post("/api/bf-calibration", json={"old_bf_pct": 20.0, "new_bf_pct": 22.0})
    client.post("/api/bf-correction/apply", json={"cutoff_date": "2026-08-01"})
    res = client.post("/api/bf-correction/apply", json={"cutoff_date": "2026-08-01"})
    assert res.get_json()["rows_affected"] == 0

    logs = client.get("/api/weight").get_json()["logs"]
    jan = next(l for l in logs if l["ts"].startswith("2026-01-01"))
    assert jan["body_fat_pct"] == 22.0  # unchanged by the second apply


def test_manual_offset_override(client):
    _seed_weight_history(client)
    res = client.post(
        "/api/bf-correction/apply", json={"cutoff_date": "2026-08-01", "offset_pct": -1.5}
    )
    assert res.status_code == 200
    logs = client.get("/api/weight").get_json()["logs"]
    jan = next(l for l in logs if l["ts"].startswith("2026-01-01"))
    assert jan["body_fat_pct"] == 18.5


def test_revert_correction(client):
    _seed_weight_history(client)
    client.post("/api/bf-calibration", json={"old_bf_pct": 20.0, "new_bf_pct": 22.0})
    apply_res = client.post("/api/bf-correction/apply", json={"cutoff_date": "2026-08-01"}).get_json()
    event_id = apply_res["event"]["id"]

    res = client.post(f"/api/bf-correction/{event_id}/revert")
    assert res.status_code == 200
    assert res.get_json()["rows_reverted"] == 2

    logs = client.get("/api/weight").get_json()["logs"]
    jan = next(l for l in logs if l["ts"].startswith("2026-01-01"))
    assert jan["body_fat_pct"] == 20.0
    assert jan["body_fat_pct_raw"] is None
    assert jan["bf_correction_id"] is None

    events = client.get("/api/bf-correction/events").get_json()
    assert events[0]["reverted_at"] is not None


def test_revert_unknown_event_404s(client):
    assert client.post("/api/bf-correction/999/revert").status_code == 404


def test_apply_correction_validation(client):
    assert client.post("/api/bf-correction/apply", json={"cutoff_date": "bad"}).status_code == 400
