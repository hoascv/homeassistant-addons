from datetime import datetime


def test_trends_default_returns_six_months(client):
    body = client.get("/api/trends").get_json()
    assert len(body["months"]) == 6
    assert len(body["collected"]) == 6
    assert body["months"][-1] == datetime.now().strftime("%Y-%m")


def test_trends_zero_fills_months_with_no_activity(client):
    body = client.get("/api/trends?months=3").get_json()
    assert body["collected"] == [0, 0, 0]
    assert body["sold"] == [0, 0, 0]
    assert body["used"] == [0, 0, 0]


def test_trends_aggregates_by_type_for_current_month(client):
    now = datetime.now()
    this_month = now.strftime("%Y-%m")
    client.post("/api/log", json={"type": "egg", "count": 8, "ts": now.isoformat()})
    client.post("/api/log", json={"type": "sale", "count": 3, "price": 9, "ts": now.isoformat()})
    client.post("/api/log", json={"type": "used", "count": 2, "ts": now.isoformat()})

    body = client.get("/api/trends?months=1").get_json()
    assert body["months"] == [this_month]
    assert body["collected"] == [8]
    assert body["sold"] == [3]
    assert body["used"] == [2]


def test_trends_months_param_clamped_to_valid_range(client):
    low = client.get("/api/trends?months=0").get_json()
    high = client.get("/api/trends?months=999").get_json()
    assert len(low["months"]) == 1
    assert len(high["months"]) == 24


def test_trends_non_numeric_months_param_defaults_to_six(client):
    body = client.get("/api/trends?months=abc").get_json()
    assert len(body["months"]) == 6


def test_trends_entries_outside_window_are_excluded(client):
    client.post("/api/log", json={"type": "egg", "count": 99, "ts": "2000-01-01T10:00:00"})
    body = client.get("/api/trends?months=3").get_json()
    assert sum(body["collected"]) == 0
