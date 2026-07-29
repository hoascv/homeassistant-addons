from datetime import datetime, timedelta


def _day_last_month(day):
    """A timestamp on the given day of the *previous* calendar month —
    always fully in the past and inside a 3-month trends window, whatever
    day of the month the test happens to run on."""
    now = datetime.now()
    last_month_end = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    return last_month_end.replace(day=day, hour=9)


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


def test_eggs_per_day_is_none_for_months_with_no_collection(client):
    body = client.get("/api/trends?months=3").get_json()
    assert body["eggs_per_day"] == [None, None, None]
    assert body["eggs_per_day_days"] == [0, 0, 0]


def test_eggs_per_day_spreads_a_collection_over_the_days_it_covers(client):
    # 1 egg on the 10th (first ever — covers its own day only), then 12 on
    # the 14th, covering the 11th-14th at 3/day. 13 eggs over 5 days.
    client.post("/api/log", json={"type": "egg", "count": 1, "ts": _day_last_month(10).isoformat()})
    client.post("/api/log", json={"type": "egg", "count": 12, "ts": _day_last_month(14).isoformat()})

    body = client.get("/api/trends?months=3").get_json()
    assert body["eggs_per_day"][-2] == 2.6
    assert body["eggs_per_day_days"][-2] == 5


def test_eggs_per_day_is_unchanged_by_how_often_you_collect(client):
    for day in range(10, 15):
        client.post(
            "/api/log", json={"type": "egg", "count": 3, "ts": _day_last_month(day).isoformat()}
        )
    daily = client.get("/api/trends?months=3").get_json()["eggs_per_day"][-2]

    # Same 15 eggs over the same days, collected in two batches instead.
    for entry in client.get("/api/entries").get_json():
        client.delete(f"/api/entries/{entry['id']}")
    client.post("/api/log", json={"type": "egg", "count": 3, "ts": _day_last_month(10).isoformat()})
    client.post("/api/log", json={"type": "egg", "count": 12, "ts": _day_last_month(14).isoformat()})
    batched = client.get("/api/trends?months=3").get_json()["eggs_per_day"][-2]

    assert daily == batched == 3.0


def test_eggs_per_day_does_not_spread_across_a_long_tracking_gap(client):
    now = datetime.now()
    client.post(
        "/api/log", json={"type": "egg", "count": 5, "ts": (now - timedelta(days=90)).isoformat()}
    )
    client.post("/api/log", json={"type": "egg", "count": 5, "ts": now.isoformat()})

    body = client.get("/api/trends?months=6").get_json()
    # 1 day for the first-ever collection + 31 (the cap) for the second —
    # the untracked middle stays uncovered rather than reading as near-zero laying.
    assert sum(body["eggs_per_day_days"]) == 32


def test_eggs_per_day_covers_days_in_the_month_the_eggs_were_laid(client):
    """A collection on the 2nd of a month mostly covers the month before."""
    first_of_month = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month_end = first_of_month - timedelta(days=1)
    client.post(
        "/api/log",
        json={"type": "egg", "count": 1, "ts": (last_month_end - timedelta(days=3)).isoformat()},
    )
    client.post(
        "/api/log",
        json={"type": "egg", "count": 20, "ts": (first_of_month + timedelta(days=1, hours=9)).isoformat()},
    )

    body = client.get("/api/trends?months=2").get_json()
    # 20 eggs spread over 5 days (the 29th-2nd of the following month):
    # 3 of those days fall in last month, 2 in this one.
    assert body["eggs_per_day_days"] == [4, 2]
    assert body["collected"] == [1, 20]  # while the raw totals stay where they were logged


def test_eggs_per_day_reports_the_thin_coverage_threshold(client):
    """The rate for a thinly-covered month is still reported — the UI
    flags it rather than hiding it — so the threshold has to travel with
    it for the frontend to know which months those are."""
    client.post("/api/log", json={"type": "egg", "count": 4, "ts": _day_last_month(10).isoformat()})
    client.post("/api/log", json={"type": "egg", "count": 9, "ts": _day_last_month(13).isoformat()})

    body = client.get("/api/trends?months=3").get_json()
    assert body["eggs_per_day_min_days"] == 10
    assert body["eggs_per_day_days"][-2] == 4  # under the threshold
    assert body["eggs_per_day"][-2] == 3.25  # but still reported


def test_daily_eggs_ends_today_and_defaults_to_thirty_days(client):
    body = client.get("/api/trends/daily").get_json()
    assert len(body["days"]) == 30
    assert body["days"][-1] == datetime.now().date().isoformat()
    assert body["eggs_per_day"] == [None] * 30


def test_daily_eggs_spreads_a_collection_across_the_days_it_covers(client):
    now = datetime.now()
    client.post(
        "/api/log", json={"type": "egg", "count": 2, "ts": (now - timedelta(days=6)).isoformat()}
    )
    client.post(
        "/api/log", json={"type": "egg", "count": 8, "ts": (now - timedelta(days=2)).isoformat()}
    )

    values = client.get("/api/trends/daily?days=10").get_json()["eggs_per_day"]
    # Days -9..-7 predate the first collection, -6 is it (2 over its own
    # day), -5..-2 share the 8 at 2/day, and -1/today aren't collected yet.
    assert values == [None, None, None, 2.0, 2.0, 2.0, 2.0, 2.0, None, None]


def test_daily_eggs_days_param_is_clamped(client):
    assert len(client.get("/api/trends/daily?days=1").get_json()["days"]) == 7
    assert len(client.get("/api/trends/daily?days=999").get_json()["days"]) == 90
    assert len(client.get("/api/trends/daily?days=abc").get_json()["days"]) == 30


def test_trends_includes_a_per_day_forecast_for_each_forecast_month(client):
    body = client.get("/api/trends").get_json()
    assert len(body["forecast_eggs_per_day"]) == len(body["forecast_months"])
    assert all(v >= 0 for v in body["forecast_eggs_per_day"])
