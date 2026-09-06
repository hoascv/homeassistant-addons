"""Money, recently: running revenue, costs and net over a window ending today.

The chart exists because the month-by-month one cannot speak for the month you
are standing in — it starts at zero on the 1st and spends the month catching up
with the completed months beside it. These tests pin the two properties that
make the window version answer that: it ends on today, and it accumulates.
"""
from datetime import datetime, timedelta


def _log(client, kind, days_ago, **money):
    ts = (datetime.now() - timedelta(days=days_ago)).isoformat()
    body = {"type": kind, "ts": ts, **money}
    res = client.post("/api/log", json=body)
    assert res.status_code in (200, 201), res.get_json()


def _daily(client, days=None):
    url = "/api/trends/daily-money" + (f"?days={days}" if days else "")
    return client.get(url).get_json()


# --- the window ------------------------------------------------------------


def test_the_window_ends_on_today(client):
    """The whole point: the right-hand edge is where you are, not the end of a
    calendar month that has not happened yet."""
    body = _daily(client, 30)
    assert len(body["days"]) == 30
    assert body["days"][-1] == datetime.now().date().isoformat()
    assert body["days"][0] == (datetime.now().date() - timedelta(days=29)).isoformat()


def test_the_default_window_is_thirty_days(client):
    assert len(_daily(client)["days"]) == 30


def test_a_junk_window_falls_back_rather_than_failing(client):
    res = client.get("/api/trends/daily-money?days=lots")
    assert res.status_code == 200
    assert len(res.get_json()["days"]) == 30


def test_the_window_is_bounded_at_both_ends(client):
    assert len(_daily(client, 1)["days"]) == 7      # a chart needs a line
    assert len(_daily(client, 9999)["days"]) == 365


def test_an_empty_window_is_zeroes_not_gaps(client):
    """Zero money moved is a fact, unlike an uncollected egg — there is no
    'still in the nest' for a sale that did not happen."""
    body = _daily(client, 14)
    assert body["revenue"] == [0] * 14
    assert body["costs"] == [0] * 14
    assert body["net"] == [0] * 14


# --- accumulating ----------------------------------------------------------


def test_the_series_accumulate_rather_than_reset(client):
    _log(client, "sale", 5, count=3, price=60)
    body = _daily(client, 10)
    # Nothing before the sale, then 60 held for every day after it.
    assert body["revenue"][:4] == [0, 0, 0, 0]
    assert body["revenue"][4] == 60
    assert body["revenue"][5:] == [60] * 5


def test_a_quiet_day_holds_the_line_instead_of_dropping_it(client):
    """The reason this is cumulative. Plotted raw, almost every day is a
    genuine zero and the chart is a flat line with spikes."""
    _log(client, "sale", 6, count=2, price=40)
    net = _daily(client, 10)["net"]
    assert net[-1] == 40
    # Never returns to zero after the money moved.
    assert all(v == 40 for v in net[4:])


def test_revenue_and_costs_only_ever_climb(client):
    _log(client, "sale", 8, count=2, price=50)
    _log(client, "expense", 5, cost=120)
    _log(client, "sale", 2, count=1, price=25)
    body = _daily(client, 12)
    for series in ("revenue", "costs"):
        values = body[series]
        assert all(a <= b for a, b in zip(values, values[1:])), series


def test_net_is_revenue_minus_costs_at_every_point(client):
    _log(client, "sale", 9, count=4, price=80)
    _log(client, "expense", 6, cost=200)
    _log(client, "sale", 3, count=2, price=45)
    body = _daily(client, 14)
    for r, c, n in zip(body["revenue"], body["costs"], body["net"]):
        assert round(r - c, 2) == n


def test_net_goes_negative_when_more_went_out_than_came_in(client):
    """The question the chart is for — is the flock paying for itself."""
    _log(client, "expense", 4, cost=340)
    _log(client, "sale", 2, count=2, price=90)
    body = _daily(client, 10)
    assert body["net"][-1] == -250


# --- what the window does and does not include -----------------------------


def test_the_totals_are_what_moved_inside_the_window(client):
    """Each series opens at zero on the window's first day: this is money over
    these days, not the all-time balance."""
    _log(client, "sale", 40, count=5, price=500)   # long before the window
    _log(client, "sale", 3, count=1, price=20)
    body = _daily(client, 14)
    assert body["revenue"][-1] == 20
    assert body["revenue"][0] == 0


def test_a_shorter_window_sees_less(client):
    _log(client, "sale", 20, count=2, price=100)
    _log(client, "expense", 2, cost=30)
    assert _daily(client, 30)["revenue"][-1] == 100
    assert _daily(client, 7)["revenue"][-1] == 0    # the sale is outside it
    assert _daily(client, 7)["costs"][-1] == 30


def test_entries_without_money_are_ignored(client):
    """Collecting and using eggs move no money, and must not show up as zero
    rows that drag a total around."""
    _log(client, "egg", 2, count=12)
    _log(client, "used", 1, count=3)
    body = _daily(client, 10)
    assert body["revenue"][-1] == 0
    assert body["costs"][-1] == 0


def test_several_entries_on_one_day_are_summed(client):
    _log(client, "sale", 3, count=1, price=25)
    _log(client, "sale", 3, count=2, price=45)
    _log(client, "expense", 3, cost=10)
    body = _daily(client, 10)
    assert body["revenue"][-1] == 70
    assert body["costs"][-1] == 10
    assert body["net"][-1] == 60


def test_money_logged_today_is_counted(client):
    """A window that ends today has to include today, or the newest entry is
    invisible until tomorrow."""
    _log(client, "sale", 0, count=1, price=35)
    assert _daily(client, 7)["revenue"][-1] == 35


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
    assert window["revenue"][-1] == monthly["revenue"][-1]
    assert window["costs"][-1] == monthly["costs"][-1]
    assert window["net"][-1] == monthly["net"][-1]
