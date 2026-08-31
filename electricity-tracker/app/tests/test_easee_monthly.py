"""Charging rolled up per calendar month.

"How much did I charge in July" is not a question a rolling 30-day window can
answer, which is why this groups by calendar month rather than by block.

Most of what follows is about the two ways a monthly table lies if you let it:
by averaging in a month that has not finished, and by showing a cost for a
month whose sessions were only partly priced. Both put a misleadingly low
number next to complete months and invite the comparison.
"""
import datetime

import pytest

import app as elapp


def _session(day, kwh, cost=10.0, partial=False, covers=None):
    return {
        "day": day,
        "energy_kwh": kwh,
        "cost_dkk": cost,
        "cost_covers_kwh": kwh if covers is None else covers,
        "cost_is_partial": partial,
    }


TODAY = datetime.date(2026, 8, 31)


# --- grouping -----------------------------------------------------------------


def test_sessions_are_grouped_by_calendar_month_newest_first():
    out = elapp.easee_monthly_charging([
        _session("2026-06-04", 10.0), _session("2026-06-20", 12.0),
        _session("2026-07-02", 30.0),
    ], today=TODAY)
    assert [m["month"] for m in out["months"]] == ["2026-07", "2026-06"]
    june = out["months"][1]
    assert june["sessions"] == 2
    assert june["energy_kwh"] == 22.0


def test_no_sessions_is_an_empty_answer_not_a_crash():
    assert elapp.easee_monthly_charging([], today=TODAY) == {"months": [], "average": None}


def test_the_month_carries_its_average_price():
    out = elapp.easee_monthly_charging(
        [_session("2026-07-02", 20.0, cost=50.0)], today=TODAY)
    assert out["months"][0]["avg_dkk_kwh"] == 2.5


# --- the month that has not finished ------------------------------------------


def test_the_current_month_is_marked_partial():
    out = elapp.easee_monthly_charging(
        [_session("2026-08-02", 10.0), _session("2026-07-02", 10.0)], today=TODAY)
    by_month = {m["month"]: m for m in out["months"]}
    assert by_month["2026-08"]["partial"] is True
    assert by_month["2026-07"]["partial"] is False


def test_the_current_month_is_excluded_from_the_average():
    """Four days into a month is not a month. Including it would make every
    early-in-the-month glance look like a collapse in usage."""
    out = elapp.easee_monthly_charging([
        _session("2026-06-04", 100.0), _session("2026-07-04", 100.0),
        _session("2026-08-01", 5.0),   # the current month, barely started
    ], today=TODAY)
    assert out["average"]["months"] == 2
    assert out["average"]["energy_kwh"] == 100.0


def test_only_a_partial_month_means_no_average_at_all():
    """Nothing complete to average. Saying so beats averaging the fragment."""
    out = elapp.easee_monthly_charging([_session("2026-08-02", 10.0)], today=TODAY)
    assert out["months"][0]["partial"] is True
    assert out["average"] is None


def test_the_partial_month_is_still_shown():
    """You want to see it; you just must not have it averaged in."""
    out = elapp.easee_monthly_charging([_session("2026-08-02", 10.0)], today=TODAY)
    assert [m["month"] for m in out["months"]] == ["2026-08"]


# --- months whose cost is not fully known -------------------------------------


def test_a_month_with_an_unpriced_session_reports_no_cost():
    """A month missing half its spot prices would sit next to complete ones
    looking like a bargain. The energy is still true, so it stays."""
    out = elapp.easee_monthly_charging([
        _session("2026-07-02", 10.0, cost=25.0),
        _session("2026-07-20", 10.0, cost=None),
    ], today=TODAY)
    july = out["months"][0]
    assert july["energy_kwh"] == 20.0
    assert july["cost_dkk"] is None
    assert july["avg_dkk_kwh"] is None


def test_a_partly_costed_session_also_disqualifies_the_months_cost():
    """cost_is_partial means only some of the kWh had a price. Adding it to a
    monthly total understates the month by however much was missing."""
    out = elapp.easee_monthly_charging([
        _session("2026-07-02", 10.0, cost=25.0),
        _session("2026-07-20", 10.0, cost=8.0, partial=True, covers=3.0),
    ], today=TODAY)
    assert out["months"][0]["cost_dkk"] is None


def test_the_average_says_how_many_months_its_cost_covers():
    """An average over three of five months is a fact; presenting it as over
    five would be a guess."""
    out = elapp.easee_monthly_charging([
        _session("2026-05-02", 10.0, cost=20.0),
        _session("2026-06-02", 10.0, cost=30.0),
        _session("2026-07-02", 10.0, cost=None),
    ], today=TODAY)
    avg = out["average"]
    assert avg["months"] == 3, "energy is known for all three"
    assert avg["cost_months"] == 2, "but cost only for two"
    assert avg["cost_dkk"] == 25.0


def test_no_month_has_a_known_cost_at_all():
    out = elapp.easee_monthly_charging(
        [_session("2026-07-02", 10.0, cost=None)], today=TODAY)
    assert out["average"]["cost_dkk"] is None
    assert out["average"]["cost_months"] == 0


# --- through the endpoint -----------------------------------------------------


def test_the_history_endpoint_carries_the_monthly_rollup(client):
    body = client.get("/api/easee/history?days=365").get_json()
    assert "monthly" in body
    assert set(body["monthly"]) == {"months", "average"}


# --- the rendered table -------------------------------------------------------


def _read(name):
    import os
    base = os.path.join(os.path.dirname(__file__), "..")
    sub = "templates" if name.endswith(".html") else "static"
    with open(os.path.join(base, sub, name)) as handle:
        return handle.read()


def test_the_table_is_hidden_until_there_is_something_to_compare():
    """One month in a monthly table is the same number written twice."""
    js = _read("app.js")
    fn = js[js.index("function renderChargingMonths"):]
    assert "monthly.months.length < 2" in fn[:fn.index("\n}")]


def test_the_average_row_states_how_many_months_it_covers():
    js = _read("app.js")
    assert "avg.months" in js and "complete" in js


def test_a_twelve_month_range_is_offered():
    assert 'data-days="365"' in _read("index.html")


def test_every_class_the_month_table_uses_is_styled():
    js, css = _read("app.js"), _read("style.css")
    fn = js[js.index("function renderChargingMonths"):]
    fn = fn[:fn.index("\nfunction renderChargingHistory")]
    import re
    for cls in set(re.findall(r'class="(month-[\w-]+)"', fn)):
        assert f".{cls}" in css, f"{cls} is emitted but never styled"
