from datetime import datetime, timedelta


def test_summary_empty_db(client):
    body = client.get("/api/summary").get_json()
    assert body["eggs_today"] == 0
    assert body["eggs_available"] == 0
    assert body["revenue_total"] == 0
    assert body["net_total"] == 0


def test_summary_eggs_today_and_week(client):
    now = datetime.now()
    client.post("/api/log", json={"type": "egg", "count": 4, "ts": now.isoformat()})
    client.post(
        "/api/log",
        json={"type": "egg", "count": 2, "ts": (now - timedelta(days=1)).isoformat()},
    )
    body = client.get("/api/summary").get_json()
    assert body["eggs_today"] == 4
    assert body["eggs_week"] >= 4


def test_summary_eggs_available_subtracts_sold_and_used(client):
    client.post("/api/log", json={"type": "egg", "count": 10})
    client.post("/api/log", json={"type": "sale", "count": 3, "price": 9})
    client.post("/api/log", json={"type": "used", "count": 2})
    body = client.get("/api/summary").get_json()
    assert body["eggs_available"] == 5


def test_summary_month_param_scopes_finances(client):
    client.post(
        "/api/log", json={"type": "sale", "count": 1, "price": 5, "ts": "2026-01-15T10:00:00"}
    )
    client.post(
        "/api/log", json={"type": "sale", "count": 1, "price": 7, "ts": "2026-02-15T10:00:00"}
    )
    jan = client.get("/api/summary?month=2026-01").get_json()
    feb = client.get("/api/summary?month=2026-02").get_json()
    assert jan["revenue_month"] == 5
    assert jan["month"] == "2026-01"
    assert feb["revenue_month"] == 7


def test_summary_bad_month_param_falls_back_to_current(client):
    body = client.get("/api/summary?month=not-a-month").get_json()
    assert body["month"] == datetime.now().strftime("%Y-%m")


def test_summary_net_is_revenue_minus_cost(client):
    client.post(
        "/api/log", json={"type": "sale", "count": 1, "price": 10, "ts": "2026-03-01T10:00:00"}
    )
    client.post(
        "/api/log", json={"type": "expense", "cost": 4, "ts": "2026-03-02T10:00:00"}
    )
    body = client.get("/api/summary?month=2026-03").get_json()
    assert body["revenue_month"] == 10
    assert body["cost_month"] == 4
    assert body["net_month"] == 6


def test_summary_all_time_totals_span_every_month(client):
    client.post(
        "/api/log", json={"type": "sale", "count": 1, "price": 5, "ts": "2020-01-15T10:00:00"}
    )
    client.post(
        "/api/log", json={"type": "sale", "count": 1, "price": 7, "ts": "2026-06-15T10:00:00"}
    )
    client.post(
        "/api/log", json={"type": "expense", "cost": 4, "ts": "2021-03-01T10:00:00"}
    )
    body = client.get("/api/summary?month=2026-06").get_json()
    assert body["revenue_total"] == 12
    assert body["cost_total"] == 4
    assert body["net_total"] == 8
    assert body["revenue_month"] == 7  # scoped to June 2026 only
