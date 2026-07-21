from datetime import datetime, timedelta

import app as coopapp


_DEFAULT_BREED_EGGS = dict(coopapp.DEFAULT_BREEDS)


def _baseline_daily(isabrown=3, sussex=2):
    return isabrown * (_DEFAULT_BREED_EGGS["Isabrown"] / 365) + sussex * (
        _DEFAULT_BREED_EGGS["Sussex"] / 365
    )


def test_forecast_pure_breed_standard_when_no_history(client):
    body = client.get("/api/trends?months=1").get_json()
    assert body["forecast_basis"] == "breed_standard"
    assert body["forecast_daily_rate"] == round(_baseline_daily(), 2)
    assert len(body["forecast_months"]) == coopapp.FORECAST_MONTHS
    assert len(body["forecast_collected"]) == coopapp.FORECAST_MONTHS


def test_forecast_months_follow_the_current_month(client):
    body = client.get("/api/trends?months=1").get_json()
    now = datetime.now()
    year, month = now.year, now.month
    expected = []
    for i in range(1, coopapp.FORECAST_MONTHS + 1):
        m = month + i
        y = year
        while m > 12:
            m -= 12
            y += 1
        expected.append(f"{y:04d}-{m:02d}")
    assert body["forecast_months"] == expected


def test_forecast_collected_matches_daily_rate_times_days_in_month(client):
    body = client.get("/api/trends?months=1").get_json()
    daily_rate = body["forecast_daily_rate"]
    for ym, projected in zip(body["forecast_months"], body["forecast_collected"]):
        year, month = (int(p) for p in ym.split("-"))
        start, end = coopapp._month_bounds(year, month)
        days = (end - start).days
        assert projected == round(daily_rate * days)


def test_forecast_zero_flock_produces_zero_forecast(client, set_options):
    set_options(flock_isabrown_count=0, flock_sussex_count=0)
    body = client.get("/api/trends?months=1").get_json()
    assert body["forecast_daily_rate"] == 0.0
    assert all(v == 0 for v in body["forecast_collected"])


def test_forecast_reflects_custom_flock_composition(client, set_options):
    set_options(flock_isabrown_count=1, flock_sussex_count=0)
    body = client.get("/api/trends?months=1").get_json()
    assert body["forecast_daily_rate"] == round(_baseline_daily(isabrown=1, sussex=0), 2)


def test_forecast_blends_with_actual_recent_rate(client):
    now = datetime.now()
    for i in range(coopapp.FORECAST_TRAILING_DAYS):
        ts = (now - timedelta(days=i, hours=1)).isoformat()
        client.post("/api/log", json={"type": "egg", "count": 2, "ts": ts})

    body = client.get("/api/trends?months=1").get_json()
    assert body["forecast_basis"] == "blended"
    assert body["forecast_daily_rate"] == 2.0  # actual rate, well within clamp bounds


def test_forecast_ratio_clamped_on_high_end(client):
    now = datetime.now()
    for i in range(coopapp.FORECAST_TRAILING_DAYS):
        ts = (now - timedelta(days=i, hours=1)).isoformat()
        client.post("/api/log", json={"type": "egg", "count": 50, "ts": ts})

    body = client.get("/api/trends?months=1").get_json()
    expected_ceiling = round(_baseline_daily() * coopapp.FORECAST_RATIO_BOUNDS[1], 2)
    assert body["forecast_daily_rate"] == expected_ceiling


def test_forecast_ratio_clamped_on_low_end(client):
    now = datetime.now()
    # A single egg well inside the trailing window: real history, but a
    # near-zero actual rate — should floor at the clamp, not collapse to 0.
    client.post(
        "/api/log",
        json={"type": "egg", "count": 1, "ts": (now - timedelta(days=25)).isoformat()},
    )
    body = client.get("/api/trends?months=1").get_json()
    expected_floor = round(_baseline_daily() * coopapp.FORECAST_RATIO_BOUNDS[0], 2)
    assert body["forecast_daily_rate"] == expected_floor


def test_forecast_basis_switches_to_blended_after_any_egg_logged(client):
    client.post("/api/log", json={"type": "egg", "count": 1})
    body = client.get("/api/trends?months=1").get_json()
    assert body["forecast_basis"] == "blended"


def test_backtest_matches_length_of_history_months(client):
    body = client.get("/api/trends?months=6").get_json()
    assert len(body["forecast_backtest"]) == len(body["months"]) == 6


def test_backtest_is_pure_breed_standard_with_no_history(client):
    body = client.get("/api/trends?months=3").get_json()
    baseline = _baseline_daily()
    for ym, backtest in zip(body["months"], body["forecast_backtest"]):
        year, month = (int(p) for p in ym.split("-"))
        start, end = coopapp._month_bounds(year, month)
        days = (end - start).days
        assert backtest == round(baseline * days)


def test_backtest_converges_to_actual_with_enough_steady_history(client):
    now = datetime.now()
    # 90 days of a perfectly steady rate — more than the 30-day trailing
    # window the forecast looks back over.
    for i in range(90):
        ts = (now - timedelta(days=i, hours=2)).isoformat()
        client.post("/api/log", json={"type": "egg", "count": 5, "ts": ts})

    body = client.get("/api/trends?months=3").get_json()
    # The most recent *fully elapsed* historical month (index -2, since -1
    # is the current, still-partial month) already had a full trailing
    # window of steady history behind it — the backtest should land exactly
    # on what actually happened.
    assert body["forecast_backtest"][-2] == body["collected"][-2]


def test_backtest_error_shrinks_as_more_history_accumulates(client):
    now = datetime.now()
    for i in range(90):
        ts = (now - timedelta(days=i, hours=2)).isoformat()
        client.post("/api/log", json={"type": "egg", "count": 5, "ts": ts})

    body = client.get("/api/trends?months=3").get_json()
    # Exclude the current (still-partial) month — comparing a full-month
    # forecast against a partial month's actual isn't a fair test.
    backtest = body["forecast_backtest"][:-1]
    actual = body["collected"][:-1]
    errors = [abs(b - a) for b, a in zip(backtest, actual)]
    assert errors[0] >= errors[-1]  # oldest month had the least prior data
