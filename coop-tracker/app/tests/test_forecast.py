from datetime import datetime, timedelta

import pytest

import app as coopapp


_DEFAULT_BREED_EGGS = dict(coopapp.DEFAULT_BREEDS)


def _baseline_daily(isabrown=3, sussex=2):
    return isabrown * (_DEFAULT_BREED_EGGS["Isabrown"] / 365) + sussex * (
        _DEFAULT_BREED_EGGS["Sussex"] / 365
    )


def _season(when):
    return coopapp._seasonal_multiplier(when)


def _month_midpoint(year, month):
    start, end = coopapp._month_bounds(year, month)
    return start + (end - start) / 2


def test_forecast_pure_breed_standard_when_no_history(client):
    body = client.get("/api/trends?months=1").get_json()
    assert body["forecast_basis"] == "breed_standard"
    assert body["forecast_daily_rate"] == round(_baseline_daily() * _season(datetime.now()), 2)
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


def test_forecast_collected_applies_each_months_seasonal_factor(client):
    # With no history the ratio is 1.0, so each projected month must be
    # exactly flat baseline × that month's seasonal factor × its days.
    body = client.get("/api/trends?months=1").get_json()
    for ym, projected in zip(body["forecast_months"], body["forecast_collected"]):
        year, month = (int(p) for p in ym.split("-"))
        start, end = coopapp._month_bounds(year, month)
        days = (end - start).days
        expected_rate = _baseline_daily() * _season(_month_midpoint(year, month))
        assert projected == round(expected_rate * days)


def test_forecast_zero_flock_produces_zero_forecast(client, set_options):
    set_options(flock_isabrown_count=0, flock_sussex_count=0)
    body = client.get("/api/trends?months=1").get_json()
    assert body["forecast_daily_rate"] == 0.0
    assert all(v == 0 for v in body["forecast_collected"])


def test_forecast_reflects_custom_flock_composition(client, set_options):
    set_options(flock_isabrown_count=1, flock_sussex_count=0)
    body = client.get("/api/trends?months=1").get_json()
    expected = _baseline_daily(isabrown=1, sussex=0) * _season(datetime.now())
    assert body["forecast_daily_rate"] == round(expected, 2)


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
    expected_ceiling = round(
        _baseline_daily() * _season(datetime.now()) * coopapp.FORECAST_RATIO_BOUNDS[1], 2
    )
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
    expected_floor = round(
        _baseline_daily() * _season(datetime.now()) * coopapp.FORECAST_RATIO_BOUNDS[0], 2
    )
    assert body["forecast_daily_rate"] == expected_floor


def test_forecast_basis_switches_to_blended_after_any_egg_logged(client):
    client.post("/api/log", json={"type": "egg", "count": 1})
    body = client.get("/api/trends?months=1").get_json()
    assert body["forecast_basis"] == "blended"


def test_forecast_months_wrap_into_next_year(conn):
    # FORECAST_MONTHS looks 3 months ahead; from November that spans the
    # turn of the year, exercising the y/m rollover that a mid-year test
    # run would never hit via `now = datetime.now()`.
    result = coopapp._compute_forecast(conn, datetime(2026, 11, 15))
    assert result["forecast_months"] == ["2026-12", "2027-01", "2027-02"]


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
        expected_rate = baseline * _season(_month_midpoint(year, month))
        assert backtest == round(expected_rate * days)


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
    # window of steady history behind it — the backtest should land close
    # to what actually happened. Not exactly: the seasonal factor at the
    # month's midpoint vs. its start differs by up to ~6% near the
    # equinoxes, and this test runs at whatever real date the suite runs.
    backtest, actual = body["forecast_backtest"][-2], body["collected"][-2]
    assert abs(backtest - actual) <= 0.10 * actual


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


# --- Seasonal adjustment ---


def test_seasonal_multiplier_boundaries():
    assert coopapp._seasonal_multiplier(datetime(2026, 6, 21)) == pytest.approx(
        1 + coopapp.SEASONAL_AMPLITUDE, abs=0.005
    )
    assert coopapp._seasonal_multiplier(datetime(2026, 12, 20)) == pytest.approx(
        1 - coopapp.SEASONAL_AMPLITUDE, abs=0.005
    )
    assert coopapp._seasonal_multiplier(datetime(2026, 9, 21)) == pytest.approx(1.0, abs=0.02)
    assert coopapp._seasonal_multiplier(datetime(2026, 3, 21)) == pytest.approx(1.0, abs=0.02)


def test_seasonal_multiplier_annual_mean_is_one():
    values = [
        coopapp._seasonal_multiplier(datetime(2026, 1, 1) + timedelta(days=i))
        for i in range(365)
    ]
    assert sum(values) / len(values) == pytest.approx(1.0, abs=0.01)


def test_current_rate_equals_observed_actual_at_fixed_date(conn, options_path):
    # The cancellation invariant: at `now` itself the seasonal terms cancel,
    # so with steady history the blended rate is exactly the observed
    # trailing rate — regardless of the season `now` falls in.
    for fixed_now in (datetime(2026, 6, 15, 12, 0), datetime(2026, 12, 15, 12, 0)):
        conn.execute("DELETE FROM logs")
        for i in range(coopapp.FORECAST_TRAILING_DAYS):
            ts = (fixed_now - timedelta(days=i, hours=1)).isoformat()
            conn.execute(
                "INSERT INTO logs (type, ts, count) VALUES ('egg', ?, 2)", (ts,)
            )
        conn.commit()
        assert coopapp._forecast_daily_rate(conn, fixed_now) == pytest.approx(2.0)


def test_forecast_projects_recovery_across_winter_boundary(conn, options_path):
    # December `now` with seasonally-normal actuals: the spring months'
    # projected daily rates must climb back above the current winter rate.
    fixed_now = datetime(2026, 12, 1, 12, 0)
    for i in range(coopapp.FORECAST_TRAILING_DAYS):
        ts = (fixed_now - timedelta(days=i, hours=1)).isoformat()
        conn.execute("INSERT INTO logs (type, ts, count) VALUES ('egg', ?, 2)", (ts,))
    conn.commit()

    result = coopapp._compute_forecast(conn, fixed_now)
    assert result["forecast_daily_rate"] == pytest.approx(2.0)
    # last forecast month is March 2027 — well up the spring slope
    year, month = (int(p) for p in result["forecast_months"][-1].split("-"))
    start, end = coopapp._month_bounds(year, month)
    projected_daily = result["forecast_collected"][-1] / (end - start).days
    assert projected_daily > 2.0


def test_backtest_applies_seasonal_factor_retroactively(conn, options_path):
    # No history at all: a December backtest month must come out lower than
    # a June one, because the seasonal factor is applied at each historical
    # month's midpoint just like a forward projection.
    june = coopapp._forecast_daily_rate(
        conn, datetime(2026, 6, 1), when=datetime(2026, 6, 16)
    )
    december = coopapp._forecast_daily_rate(
        conn, datetime(2026, 12, 1), when=datetime(2026, 12, 16)
    )
    assert december < june


# --- Uncertainty band (forecast_margin) ---


def test_forecast_margin_none_with_no_completed_history():
    assert coopapp._compute_forecast_margin([10], [10]) is None
    assert coopapp._compute_forecast_margin([], []) is None


def test_forecast_margin_zero_for_perfect_backtest():
    assert coopapp._compute_forecast_margin([10, 10], [10, 10]) == 0


def test_forecast_margin_excludes_current_month():
    # a huge mismatch in the last (current, still-partial) month must not
    # count — same exclusion _compute_backtest's docstring documents
    assert coopapp._compute_forecast_margin([10, 999], [10, 0]) == 0


def test_forecast_margin_is_mean_absolute_error():
    # completed months: |10-8|=2, |20-25|=5 -> mean 3.5 -> rounds to 4;
    # the third (current) month's huge mismatch is excluded
    assert coopapp._compute_forecast_margin([10, 20, 5], [8, 25, 999]) == 4


def test_trends_endpoint_includes_forecast_margin(client):
    client.post("/api/log", json={"type": "egg", "count": 3, "ts": "2026-01-15T10:00:00"})
    body = client.get("/api/trends?months=3").get_json()
    assert "forecast_margin" in body
    assert body["forecast_margin"] is None or isinstance(body["forecast_margin"], int)
