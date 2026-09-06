"""Money, recently: what moved per bucket over a window ending today.

The chart exists because the month-by-month one cannot speak for the month you
are standing in — it starts at zero on the 1st and spends the month catching up
with the completed months beside it. These tests pin what makes the window
version answer that: it ends on today, its buckets are all the same length, and
each one reports what actually moved inside it.
"""
from datetime import datetime, timedelta


def _log(client, kind, days_ago, **money):
    ts = (datetime.now() - timedelta(days=days_ago)).isoformat()
    res = client.post("/api/log", json={"type": kind, "ts": ts, **money})
    assert res.status_code in (200, 201), res.get_json()


def _daily(client, days=None):
    url = "/api/trends/daily-money" + (f"?days={days}" if days else "")
    return client.get(url).get_json()


def _bucket_for(body, days_ago):
    """The bucket covering the day `days_ago` back, or None if outside."""
    day = (datetime.now().date() - timedelta(days=days_ago)).isoformat()
    for i, (start, end) in enumerate(zip(body["starts"], body["ends"])):
        if start <= day <= end:
            return i
    return None


# --- the window ------------------------------------------------------------


def test_the_window_ends_on_today(client):
    """The whole point: the right-hand edge is where you are, not the end of a
    calendar month that has not happened yet."""
    body = _daily(client, 30)
    assert body["ends"][-1] == datetime.now().date().isoformat()
    assert len(body["starts"]) == 30


def test_the_default_window_is_thirty_days(client):
    assert len(_daily(client)["starts"]) == 30


def test_a_junk_window_falls_back_rather_than_failing(client):
    res = client.get("/api/trends/daily-money?days=lots")
    assert res.status_code == 200
    assert len(res.get_json()["starts"]) == 30


def test_the_window_is_bounded_at_both_ends(client):
    assert len(_daily(client, 1)["starts"]) == 7          # a chart needs a span
    assert _daily(client, 9999)["bucket_days"] == 7       # and buckets up


# --- bucketing -------------------------------------------------------------


def test_short_windows_are_a_bucket_a_day(client):
    for window in (14, 30):
        body = _daily(client, window)
        assert body["grouping"] == "day"
        assert body["bucket_days"] == 1
        assert body["starts"] == body["ends"]


def test_a_long_window_buckets_into_weeks(client):
    body = _daily(client, 90)
    assert body["grouping"] == "week"
    assert body["bucket_days"] == 7
    assert len(body["starts"]) == 13


def test_every_bucket_covers_the_same_span(client):
    """A part-week bar at the right-hand edge would be the monthly chart's
    problem in miniature — always low, purely because the week isn't over."""
    body = _daily(client, 90)
    spans = {
        (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days + 1
        for start, end in zip(body["starts"], body["ends"])
    }
    assert spans == {7}
    # And the newest week still ends today rather than stopping short of it.
    assert body["ends"][-1] == datetime.now().date().isoformat()


def test_buckets_run_oldest_first_and_do_not_overlap(client):
    body = _daily(client, 90)
    for i in range(1, len(body["starts"])):
        assert body["starts"][i] > body["ends"][i - 1]


# --- what a bucket reports -------------------------------------------------


def test_a_bucket_reports_what_moved_in_it_not_a_running_total(client):
    """The change that made bars possible: a bucket is an amount, so a quiet one
    is genuinely zero and its bar is simply absent."""
    _log(client, "sale", 5, count=3, price=60)
    body = _daily(client, 10)
    hit = _bucket_for(body, 5)
    assert body["revenue"][hit] == 60
    # Every other day reports nothing, rather than carrying the 60 forward.
    assert sum(body["revenue"]) == 60
    assert body["revenue"][-1] == 0


def test_an_empty_window_is_zeroes(client):
    """Zero money moved is a fact, unlike an uncollected egg — there is no
    'still in the nest' for a sale that did not happen, so no gaps."""
    body = _daily(client, 14)
    assert body["revenue"] == [0] * 14
    assert body["costs"] == [0] * 14
    assert body["net"] == [0] * 14
    assert body["totals"] == {"revenue": 0, "costs": 0, "net": 0}


def test_net_is_revenue_minus_costs_in_every_bucket(client):
    _log(client, "sale", 9, count=4, price=80)
    _log(client, "expense", 9, cost=200)
    _log(client, "sale", 3, count=2, price=45)
    body = _daily(client, 14)
    for r, c, n in zip(body["revenue"], body["costs"], body["net"]):
        assert round(r - c, 2) == n


def test_a_bucket_can_hold_both_directions(client):
    """A sale and an expense on the same day draw a bar each, one either side
    of the line — not one netted bar."""
    _log(client, "sale", 4, count=2, price=90)
    _log(client, "expense", 4, cost=340)
    body = _daily(client, 10)
    i = _bucket_for(body, 4)
    assert (body["revenue"][i], body["costs"][i], body["net"][i]) == (90, 340, -250)


def test_several_entries_in_one_bucket_are_summed(client):
    _log(client, "sale", 3, count=1, price=25)
    _log(client, "sale", 3, count=2, price=45)
    _log(client, "expense", 3, cost=10)
    body = _daily(client, 10)
    i = _bucket_for(body, 3)
    assert (body["revenue"][i], body["costs"][i], body["net"][i]) == (70, 10, 60)


def test_a_week_bucket_sums_the_days_inside_it(client):
    _log(client, "sale", 40, count=1, price=30)
    _log(client, "sale", 38, count=1, price=20)
    _log(client, "expense", 39, cost=15)
    body = _daily(client, 90)
    i = _bucket_for(body, 39)
    assert _bucket_for(body, 40) == i and _bucket_for(body, 38) == i
    assert (body["revenue"][i], body["costs"][i]) == (50, 15)


def test_entries_without_money_are_ignored(client):
    """Collecting and using eggs move no money and must not raise a bar."""
    _log(client, "egg", 2, count=12)
    _log(client, "used", 1, count=3)
    body = _daily(client, 10)
    assert body["totals"] == {"revenue": 0, "costs": 0, "net": 0}


def test_money_logged_today_is_counted(client):
    """A window ending today has to include today, or the newest entry is
    invisible until tomorrow."""
    _log(client, "sale", 0, count=1, price=35)
    body = _daily(client, 7)
    assert body["revenue"][-1] == 35


# --- the running net -------------------------------------------------------


def test_the_running_net_accumulates_across_the_window(client):
    """Not drawn — it lives on a different scale from the bars — but it is what
    the tooltip and caption quote, so it has to be right."""
    _log(client, "expense", 6, cost=100)
    _log(client, "sale", 3, count=2, price=40)
    body = _daily(client, 10)
    running = body["running_net"]
    assert running[_bucket_for(body, 7)] == 0
    assert running[_bucket_for(body, 6)] == -100
    assert running[_bucket_for(body, 4)] == -100     # held over a quiet day
    assert running[_bucket_for(body, 3)] == -60
    assert running[-1] == -60


def test_the_running_net_ends_on_the_window_total(client):
    _log(client, "sale", 5, count=1, price=70)
    _log(client, "expense", 2, cost=25)
    body = _daily(client, 14)
    assert body["running_net"][-1] == body["totals"]["net"] == 45


def test_totals_are_the_sum_of_the_buckets(client):
    _log(client, "sale", 8, count=2, price=50)
    _log(client, "expense", 5, cost=120)
    _log(client, "sale", 2, count=1, price=25)
    body = _daily(client, 14)
    assert body["totals"]["revenue"] == round(sum(body["revenue"]), 2) == 75
    assert body["totals"]["costs"] == round(sum(body["costs"]), 2) == 120
    assert body["totals"]["net"] == -45


# --- what the window does and does not include -----------------------------


def test_only_what_moved_inside_the_window_is_counted(client):
    _log(client, "sale", 40, count=5, price=500)   # long before the window
    _log(client, "sale", 3, count=1, price=20)
    assert _daily(client, 14)["totals"]["revenue"] == 20


def test_a_shorter_window_sees_less(client):
    _log(client, "sale", 20, count=2, price=100)
    _log(client, "expense", 2, cost=30)
    assert _daily(client, 30)["totals"]["revenue"] == 100
    assert _daily(client, 7)["totals"]["revenue"] == 0    # the sale is outside it
    assert _daily(client, 7)["totals"]["costs"] == 30


def test_the_two_groupings_agree_on_what_they_both_cover(client):
    """Weekly buckets must be the same money in fewer bars, not different
    money — otherwise widening the range silently rewrites history."""
    for offset in (2, 9, 16, 23):
        _log(client, "sale", offset, count=1, price=30)
        _log(client, "expense", offset, cost=12)
    daily = _daily(client, 30)
    weekly = _daily(client, 90)
    assert weekly["grouping"] == "week" and daily["grouping"] == "day"
    assert daily["totals"] == weekly["totals"]


# --- agreeing with the monthly chart ---------------------------------------


def test_it_uses_the_same_definitions_as_the_monthly_chart(client):
    """sale.price is revenue, expense.cost is what went out. A window covering
    the same days as a month must reach the same totals, or the two charts
    disagree about what a sale was worth."""
    now = datetime.now()
    for day in range(1, min(now.day, 20) + 1):
        _log(client, "sale", now.day - day, count=1, price=10)
        _log(client, "expense", now.day - day, cost=4)

    monthly = client.get("/api/trends?months=1").get_json()
    # A window reaching back at least to the 1st of this month. The 7-day floor
    # can push it into last month in the first week — harmless here only because
    # nothing is logged there, and stated so the equality is not mistaken for a
    # claim that the two windows always cover the same days.
    window = _daily(client, max(7, now.day))
    assert window["totals"]["revenue"] == monthly["revenue"][-1]
    assert window["totals"]["costs"] == monthly["costs"][-1]
    assert window["totals"]["net"] == monthly["net"][-1]
