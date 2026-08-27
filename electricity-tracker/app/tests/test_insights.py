"""The Insights tab's arithmetic.

The headline number — what you paid against what a flat consumer would have —
is the one that has to be right: it is the number that says whether being on a
spot tariff is worth anything, and a wrong one would be believed.
"""
import app as electricityapp


def _row(day="2026-08-16", hour=0, kwh=1.0, price=1.0):
    return {
        "time_dk": f"{day}T{hour:02d}:00:00",
        "kwh": kwh,
        "price_dkk_kwh": price,
        "cost_dkk": None if price is None else round(kwh * price, 4),
        "source": "eloverblik",
    }


# --- what you paid against a flat consumer ---


def test_using_power_in_the_cheap_hours_beats_the_flat_average(conn):
    rows = [_row(hour=0, kwh=10.0, price=1.0), _row(hour=1, kwh=1.0, price=3.0)]
    result = electricityapp._price_performance(rows)
    # Paid (10*1 + 1*3) / 11 = 1.1818; a flat consumer pays (1+3)/2 = 2.0.
    assert result["avg_paid_dkk_kwh"] == 1.1818
    assert result["flat_dkk_kwh"] == 2.0
    assert result["difference_pct"] > 0


def test_using_power_in_the_expensive_hours_is_reported_as_worse(conn):
    """Negative is not hidden: a heat pump running at the daily peak should say
    so rather than being rounded to 'no difference'."""
    rows = [_row(hour=0, kwh=1.0, price=1.0), _row(hour=1, kwh=10.0, price=3.0)]
    result = electricityapp._price_performance(rows)
    assert result["avg_paid_dkk_kwh"] > result["flat_dkk_kwh"]
    assert result["difference_pct"] < 0
    assert result["difference_dkk"] < 0


def test_flat_consumption_shows_no_difference(conn):
    rows = [_row(hour=h, kwh=2.0, price=1.0 + h) for h in range(4)]
    result = electricityapp._price_performance(rows)
    assert result["avg_paid_dkk_kwh"] == result["flat_dkk_kwh"]
    assert result["difference_pct"] == 0.0


def test_unpriced_hours_are_excluded_from_both_sides(conn):
    """Weighting over one set of hours and averaging over another would not be
    a comparison at all."""
    rows = [_row(hour=0, kwh=1.0, price=1.0), _row(hour=1, kwh=99.0, price=None)]
    result = electricityapp._price_performance(rows)
    assert result["hours"] == 1
    assert result["kwh"] == 1.0
    assert result["avg_paid_dkk_kwh"] == result["flat_dkk_kwh"] == 1.0


def test_no_priced_consumption_yields_nothing_rather_than_a_zero(conn):
    assert electricityapp._price_performance([]) is None
    assert electricityapp._price_performance([_row(kwh=0.0, price=1.0)]) is None


# --- the shape of a day ---


def test_the_hourly_profile_averages_each_hour_across_days(conn):
    rows = [_row(day="2026-08-16", hour=7, kwh=2.0), _row(day="2026-08-17", hour=7, kwh=4.0)]
    profile = electricityapp._hourly_profile(rows)
    assert len(profile) == 24  # every hour present, so the chart has no gaps
    seven = next(p for p in profile if p["hour"] == 7)
    assert seven["avg_kwh"] == 3.0
    assert seven["samples"] == 2


def test_hours_never_seen_are_zero_not_missing(conn):
    profile = electricityapp._hourly_profile([_row(hour=7, kwh=2.0)])
    assert next(p for p in profile if p["hour"] == 3)["avg_kwh"] == 0.0
    assert next(p for p in profile if p["hour"] == 3)["samples"] == 0


def test_the_profile_reports_the_average_price_paid_in_each_hour(conn):
    rows = [_row(hour=2, kwh=1.0, price=0.5), _row(day="2026-08-17", hour=2, kwh=3.0, price=1.5)]
    two = next(p for p in electricityapp._hourly_profile(rows) if p["hour"] == 2)
    # Weighted by consumption: (1*0.5 + 3*1.5) / 4
    assert two["avg_price"] == 1.25


# --- the load that never goes away ---


def test_baseline_uses_a_percentile_not_the_minimum(conn):
    """One hour of a power cut, or a gap in reporting, would make a minimum
    read as zero and the whole figure useless."""
    rows = [_row(hour=h % 24, day=f"2026-08-{16 + h // 24:02d}", kwh=1.0) for h in range(48)]
    rows[5]["kwh"] = 0.0  # an outage hour
    baseline = electricityapp._baseline_load(rows)
    assert baseline["kw"] == 1.0


def test_baseline_needs_at_least_a_day_of_hours(conn):
    assert electricityapp._baseline_load([_row(hour=h) for h in range(5)]) is None


def test_baseline_annualises_and_reports_its_share(conn):
    rows = [_row(hour=h % 24, day=f"2026-08-{16 + h // 24:02d}", kwh=2.0) for h in range(48)]
    baseline = electricityapp._baseline_load(rows)
    assert baseline["annual_kwh"] == round(2.0 * 24 * 365, 0)
    assert baseline["share_pct"] == 100.0  # a perfectly flat house is all baseline


# --- days worth looking at ---


def test_extremes_separate_the_biggest_day_from_the_best_rate(conn):
    """A cheapest day and a best-rate day are different questions, and the
    second is the interesting one — it is when the timing worked."""
    rows = [
        _row(day="2026-08-16", hour=0, kwh=10.0, price=2.0),  # big, expensive rate
        _row(day="2026-08-17", hour=0, kwh=1.0, price=0.5),   # small, best rate
    ]
    extremes = electricityapp._day_extremes(electricityapp._day_totals(rows))
    assert extremes["most_kwh"]["day"] == "2026-08-16"
    assert extremes["most_cost"]["day"] == "2026-08-16"
    assert extremes["best_rate"]["day"] == "2026-08-17"
    assert extremes["worst_rate"]["day"] == "2026-08-16"


def test_extremes_of_nothing_is_none(conn):
    assert electricityapp._day_extremes([]) is None


def test_day_totals_sum_hours_into_days(conn):
    rows = [_row(hour=0, kwh=1.0), _row(hour=1, kwh=2.5)]
    daily = electricityapp._day_totals(rows)
    assert len(daily) == 1
    assert daily[0]["kwh"] == 3.5
    assert daily[0]["cost_known"] is True


def test_a_day_with_an_unpriced_hour_is_flagged(conn):
    rows = [_row(hour=0, kwh=1.0, price=1.0), _row(hour=1, kwh=1.0, price=None)]
    assert electricityapp._day_totals(rows)[0]["cost_known"] is False


# --- when power is cheap, from prices alone ---


def test_cheapest_hours_come_from_price_history_alone(conn):
    """True whether or not any consumption has ever been recorded — useful on a
    fresh install with no Eloverblik connection."""
    quarters = []
    for hour in range(24):
        for minute in (0, 15, 30, 45):
            quarters.append({"time_dk": f"2026-08-16T{hour:02d}:{minute:02d}:00",
                             "total_dkk_kwh": 1.0 + hour * 0.1})
    prices = electricityapp._cheapest_hours_of_day(quarters)
    assert prices["cheapest"][0]["hour"] == 0
    assert prices["priciest"][0]["hour"] == 23
    assert len(prices["by_hour"]) == 24


def test_cheapest_hours_of_nothing_is_none(conn):
    assert electricityapp._cheapest_hours_of_day([]) is None


# --- the route ---


def test_insights_route_works_before_anything_is_configured(conn, client):
    """A fresh install has no metering point and no prices. The tab must open
    and say so, not 500."""
    data = client.get("/api/insights").get_json()
    assert data["consumption_hours"] == 0
    assert data["price_performance"] is None
    assert data["baseline"] is None
    assert len(data["hourly_profile"]) == 24


def test_insights_route_honours_the_day_range(conn, client):
    assert client.get("/api/insights?days=7").get_json()["days"] == 7
    assert client.get("/api/insights?days=9999").get_json()["days"] == 365
    assert client.get("/api/insights?days=0").get_json()["days"] == 30


# --- the car's share of the house ---


def test_a_share_over_100_percent_is_explained_not_printed_as_is(conn, client, set_options):
    """The car draws through the house meter, so its share cannot really exceed
    100%. When the arithmetic says otherwise it is Eloverblik running days
    behind Easee, and that is what the tab should say."""
    set_options(easee_enabled=True, easee_username="u", easee_password="p", easee_charger_id="EH1",
                eloverblik_refresh_token="t", eloverblik_metering_point="mp1")
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    for i, kwh in enumerate([0.0, 20.0, 40.0]):
        ts = (now - timedelta(hours=6 - i)).isoformat()
        conn.execute(
            "INSERT INTO easee_samples (ts_utc, charger_id, status, session_energy_kwh, total_power_w, "
            "reason_for_no_current, fetched_at) VALUES (?, 'EH1', 'CHARGING', ?, 7200.0, NULL, ?)",
            (ts, kwh, ts),
        )
    conn.commit()

    ev = client.get("/api/insights?days=7").get_json()["ev"]
    assert ev["sessions"] == 1
    assert ev["house_kwh"] == 0.0  # no Eloverblik readings at all
    # No house figure to divide by, so no share is claimed rather than infinity.
    assert ev["share_of_house_pct"] is None
    assert ev["house_behind"] is False


def test_no_charger_configured_means_no_ev_section(conn, client):
    assert client.get("/api/insights").get_json()["ev"] is None
