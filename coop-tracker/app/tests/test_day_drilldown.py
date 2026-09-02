"""What is behind one point on the eggs-per-day chart.

The figure is an attributed rate rather than a count, so "what happened on the
20th" has two answers and the useful one is not the obvious one: the eggs
credited to a day usually arrive in a collection made later and spread back
over the days since the previous visit.

That is exactly what lets a rate exceed the flock size, so a drill-down showing
only the logs dated that day would explain nothing about the day most worth
asking about. Most of what follows pins that it explains the right thing.
"""
from datetime import datetime, timedelta

import pytest

import app as coop


@pytest.fixture
def log_egg(conn):
    def _log(days_ago, count):
        ts = (datetime.now().replace(hour=17, minute=0, second=0, microsecond=0)
              - timedelta(days=days_ago)).isoformat(timespec="seconds")
        conn.execute("INSERT INTO logs (type, count, ts) VALUES ('egg', ?, ?)", (count, ts))
        conn.commit()
    return _log


def _day(days_ago):
    return (datetime.now().date() - timedelta(days=days_ago)).isoformat()


# --- the source of a figure ---------------------------------------------------


def test_a_day_reports_the_collection_it_came_from(client, log_egg):
    log_egg(5, 4)
    log_egg(1, 12)  # 12 found after 4 days away

    body = client.get(f"/api/trends/day?date={_day(3)}").get_json()
    assert body["rate"] == 3.0
    assert body["source"]["collected_on"] == _day(1)
    assert body["source"]["collected"] == 12
    assert body["source"]["span_days"] == 4


def test_the_collection_day_names_itself(client, log_egg):
    log_egg(2, 4)
    log_egg(1, 5)
    body = client.get(f"/api/trends/day?date={_day(1)}").get_json()
    assert body["source"]["collected_on"] == _day(1)
    assert body["source"]["span_days"] == 1


def test_the_entries_logged_on_a_covered_day_are_listed(client, log_egg, conn):
    """Separate from the collection that produced the rate: a day can carry a
    cleaning or a feeding that has nothing to do with the eggs credited to it."""
    log_egg(1, 5)
    ts = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    conn.execute("INSERT INTO logs (type, notes, ts) VALUES ('cleaning', 'raked it out', ?)",
                 (ts,))
    conn.commit()

    body = client.get(f"/api/trends/day?date={_day(1)}").get_json()
    kinds = {e["type"] for e in body["entries"]}
    assert kinds == {"egg", "cleaning"}


def test_a_day_credited_from_a_later_collection_lists_that_collection(client, log_egg):
    """The point of the drill-down. Standing on the 17th, the eggs came from a
    basket found on the 19th, and it is that basket you want to see."""
    log_egg(6, 4)
    log_egg(3, 9)

    body = client.get(f"/api/trends/day?date={_day(5)}").get_json()
    assert body["source"]["collected_on"] == _day(3)
    assert [e["count"] for e in body["source"]["entries"]] == [9]
    assert body["entries"] == [], "nothing was logged on the day itself"


# --- the impossible case ------------------------------------------------------


def test_an_impossible_rate_is_named_as_such(client, log_egg, set_options):
    set_options(flock_isabrown_count=5, flock_sussex_count=0)
    log_egg(2, 4)
    log_egg(1, 6)  # six from five hens, one day after the last visit

    body = client.get(f"/api/trends/day?date={_day(1)}").get_json()
    assert body["rate"] == 6.0
    assert body["impossible"] is True
    assert body["birds"] == 5


def test_a_possible_rate_is_not(client, log_egg, set_options):
    set_options(flock_isabrown_count=5, flock_sussex_count=0)
    log_egg(2, 4)
    log_egg(1, 5)
    body = client.get(f"/api/trends/day?date={_day(1)}").get_json()
    assert body["impossible"] is False


def test_a_capped_gap_says_so(client, log_egg, set_options):
    """A gap longer than the spread cap is truncated, and a drill-down that did
    not mention it would show a figure nothing on screen explains."""
    set_options(flock_isabrown_count=5, flock_sussex_count=0)
    log_egg(80, 3)
    log_egg(1, 60)

    body = client.get(f"/api/trends/day?date={_day(1)}").get_json()
    assert body["source"]["capped"] is True
    assert body["source"]["span_days"] == coop.EGGS_PER_DAY_MAX_SPREAD_DAYS


# --- days nothing covers ------------------------------------------------------


def test_an_uncovered_day_says_why_rather_than_showing_nothing(client, log_egg):
    """A break in the line is not a day of no eggs. Which it is, is the
    difference between "the hens stopped" and "nobody has been out yet"."""
    log_egg(10, 4)
    body = client.get(f"/api/trends/day?date={_day(1)}").get_json()
    assert body["covered"] is False
    assert body["rate"] is None
    assert body["source"] is None


def test_a_day_before_any_log_is_uncovered(client, log_egg):
    log_egg(1, 4)
    body = client.get(f"/api/trends/day?date={_day(40)}").get_json()
    assert body["covered"] is False


def test_a_bad_date_is_a_400(client):
    for value in ("", "yesterday", "20-08-2026"):
        assert client.get(f"/api/trends/day?date={value}").status_code == 400


# --- one implementation -------------------------------------------------------


def test_the_drill_down_and_the_chart_cannot_disagree(client, log_egg):
    """Both read the same spreading. A drill-down that computed the rate a
    second way would eventually contradict the chart it was opened from."""
    log_egg(6, 4)
    log_egg(2, 12)

    chart = client.get("/api/trends/daily?days=10").get_json()
    for index, day in enumerate(chart["days"]):
        drilled = client.get(f"/api/trends/day?date={day}").get_json()
        assert drilled["rate"] == chart["eggs_per_day"][index], day


# --- the page -----------------------------------------------------------------


def _static(name):
    import os
    sub = "templates" if name.endswith(".html") else "static"
    with open(os.path.join(os.path.dirname(__file__), "..", sub, name),
              encoding="utf-8") as handle:
        return handle.read()


def test_only_the_day_chart_offers_a_drill_down():
    """A month's point averages thirty collections; there is no single set of
    entries behind it to show, so it must not look clickable."""
    js = _static("app.js")
    for name in ("buildTrendsSvg", "buildEggsPerDaySvg", "buildAdvancedForecastSvg"):
        start = js.index(f"function {name}(")
        body = js[start:js.index("\n}\n", start)]
        assert "data.days[i]" not in body, f"{name} advertises a day it cannot explain"

    start = js.index("function buildDailyEggsSvg(")
    daily = js[start:js.index("\n}\n", start)]
    assert "(i) => data.days[i]" in daily


def test_a_drillable_point_shows_a_pointer():
    css = _static("style.css")
    assert ".chart-hit.is-drillable" in css
    assert "cursor: pointer" in css[css.index(".chart-hit.is-drillable"):][:80]


def test_the_sheet_exists_and_closes():
    html = _static("index.html")
    assert 'id="day-backdrop"' in html
    assert 'id="day-close"' in html
