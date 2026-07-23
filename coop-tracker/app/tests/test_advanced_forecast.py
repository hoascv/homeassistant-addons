from datetime import datetime, timedelta

import pytest

import app as coopapp


def _seed_egg_logs(conn, now, months, jitter=lambda i: 0):
    for i in range(months):
        ts = (now.replace(day=15) - timedelta(days=30 * i)).isoformat()
        count = max(0, 100 + jitter(i))
        conn.execute("INSERT INTO logs (type, ts, count) VALUES ('egg', ?, ?)", (ts, count))
    conn.commit()


# --- Gating logic (no statsmodels required — tests the gate itself) ---


def test_advanced_forecast_disabled_by_default(client, conn):
    now = datetime.now()
    _seed_egg_logs(conn, now, 24)
    body = client.get("/api/trends/advanced").get_json()
    assert body["advanced_enabled"] is False
    assert body["model"] is None
    assert body["advanced_forecast"] == []


def test_advanced_forecast_reports_libs_available_flag(client):
    body = client.get("/api/trends/advanced").get_json()
    assert body["advanced_libs_available"] == coopapp.STATSMODELS_AVAILABLE


def test_advanced_forecast_insufficient_history(client, conn, set_options):
    set_options(advanced_forecast_enabled=True)
    _seed_egg_logs(conn, datetime.now(), 3)
    body = client.get("/api/trends/advanced").get_json()
    assert body["history_months"] < body["min_months_required"]
    assert body["model"] is None


def test_egg_history_span_months_counts_from_first_log(conn):
    now = datetime(2026, 7, 20)
    assert coopapp._egg_history_span_months(conn, now) == 0

    # (year, month - 9) -> inclusive span of exactly 10 calendar months
    conn.execute(
        "INSERT INTO logs (type, ts, count) VALUES ('egg', ?, 1)",
        (datetime(2025, 10, 20).isoformat(),),
    )
    conn.commit()
    assert coopapp._egg_history_span_months(conn, now) == 10

    # a log 3 calendar years back -> well past the 24-month cap
    conn.execute(
        "INSERT INTO logs (type, ts, count) VALUES ('egg', ?, 1)",
        (datetime(2023, 7, 20).isoformat(),),
    )
    conn.commit()
    assert coopapp._egg_history_span_months(conn, now) == 24


# --- Real model fit (skipped if statsmodels isn't installed) ---


@pytest.mark.skipif(not coopapp.STATSMODELS_AVAILABLE, reason="statsmodels not installed")
class TestAdvancedForecastFit:
    def test_trend_only_below_seasonal_threshold(self, client, conn, set_options):
        set_options(advanced_forecast_enabled=True)
        now = datetime.now()
        _seed_egg_logs(conn, now, 8, jitter=lambda i: (i * 7) % 13 - 6)
        body = client.get("/api/trends/advanced").get_json()
        assert body["model"] == "holt_winters_trend"
        assert len(body["advanced_forecast"]) == coopapp.FORECAST_MONTHS
        assert body["advanced_error"] is None

    def test_seasonal_with_full_history(self, client, conn, set_options):
        set_options(advanced_forecast_enabled=True)
        now = datetime.now()
        _seed_egg_logs(conn, now, 24, jitter=lambda i: (i % 12) * 5 - 25)
        body = client.get("/api/trends/advanced").get_json()
        assert body["model"] == "holt_winters_seasonal"
        assert len(body["advanced_forecast"]) == coopapp.FORECAST_MONTHS

    def test_ci_bounds_ordered(self, client, conn, set_options):
        set_options(advanced_forecast_enabled=True)
        now = datetime.now()
        _seed_egg_logs(conn, now, 24, jitter=lambda i: ((i * 37) % 21) - 10)
        body = client.get("/api/trends/advanced").get_json()
        for lower, mid, upper in zip(
            body["advanced_ci_lower"], body["advanced_forecast"], body["advanced_ci_upper"]
        ):
            assert lower <= mid <= upper

    def test_ci_lower_never_negative(self, client, conn, set_options):
        set_options(advanced_forecast_enabled=True)
        now = datetime.now()
        # a declining-toward-zero series, prone to a negative lower bound
        # without the max(0, ...) clamp
        for i in range(24):
            ts = (now.replace(day=15) - timedelta(days=30 * i)).isoformat()
            count = max(0, 5 - i)
            conn.execute(
                "INSERT INTO logs (type, ts, count) VALUES ('egg', ?, ?)", (ts, count)
            )
        conn.commit()
        body = client.get("/api/trends/advanced").get_json()
        assert all(v >= 0 for v in body["advanced_ci_lower"])
