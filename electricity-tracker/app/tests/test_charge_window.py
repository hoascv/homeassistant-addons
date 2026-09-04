"""When to plug in.

The dashboard already names the cheapest hour, and that is the wrong unit. A
charge needs several consecutive hours, and on a real day the cheapest quarter
is very often pressed against an expensive one — so the answer is the cheapest
*run* long enough to finish, scored by its mean, which is what the charge
actually pays.
"""
import datetime

import pytest

import app as electricityapp


def _rows(prices, start="2026-09-04T00:00:00"):
    """Quarter-hourly rows, one price each, in the shape the pricer emits."""
    t0 = datetime.datetime.fromisoformat(start)
    return [
        {"time_dk": (t0 + datetime.timedelta(minutes=15 * i)).isoformat(),
         "total_dkk_kwh": p}
        for i, p in enumerate(prices)
    ]


# --- finding the run ----------------------------------------------------------


def test_the_cheapest_run_wins_not_the_cheapest_quarter():
    """The crux. The single cheapest quarter here is the 0.10 at index 3, and
    it is wedged between expensive ones — a charge starting there pays more
    than one starting in the flat cheap stretch that follows."""
    rows = _rows([2.0, 2.0, 2.0, 0.10, 2.0, 0.5, 0.5, 0.5, 0.5, 2.0])
    window = electricityapp.cheapest_window(rows, slots=4)
    assert window["start"] == rows[5]["time_dk"]
    assert window["average_dkk_kwh"] == 0.5


def test_the_window_ends_when_the_last_slot_ends():
    """A two-hour window starting at 02:00 ends at 04:00. Reporting the last
    slot's start would be a quarter of an hour short, and somebody planning a
    charge around it would come up short by the same."""
    rows = _rows([1.0] * 8, start="2026-09-04T02:00:00")
    window = electricityapp.cheapest_window(rows, slots=8)
    assert window["start"].endswith("02:00:00")
    assert window["end"] == "2026-09-04T04:00"


def test_it_reports_the_spread_inside_the_window():
    """A flat cheap run and a run averaging the same by mixing very cheap with
    very dear are not the same offer, and the mean alone hides that."""
    rows = _rows([0.2, 1.8, 0.2, 1.8])
    window = electricityapp.cheapest_window(rows, slots=4)
    assert window["average_dkk_kwh"] == 1.0
    assert window["cheapest_dkk_kwh"] == 0.2
    assert window["priciest_dkk_kwh"] == 1.8


def test_not_enough_prices_is_no_answer_rather_than_a_short_one():
    """A partial answer would name a window ending after the prices do."""
    assert electricityapp.cheapest_window(_rows([1.0, 1.0]), slots=8) is None
    assert electricityapp.cheapest_window([], slots=4) is None


def test_unpriced_quarters_are_skipped_not_counted_as_free():
    """A missing price is not a cheap one, and treating it as zero would point
    every recommendation straight at the gap."""
    rows = _rows([1.0, 1.0, 1.0, 1.0])
    rows[1]["total_dkk_kwh"] = None
    window = electricityapp.cheapest_window(rows, slots=3)
    assert window["average_dkk_kwh"] == 1.0


# --- against starting now -----------------------------------------------------


def test_it_says_what_waiting_is_worth():
    """The comparison is what makes the answer actionable rather than merely
    true: "cheapest at 02:00" is no help without "and now costs this much"."""
    rows = _rows([2.0, 2.0, 2.0, 2.0, 0.5, 0.5, 0.5, 0.5])
    window = electricityapp.cheapest_window(rows, slots=4, now_key=rows[0]["time_dk"])
    assert window["now_average_dkk_kwh"] == 2.0
    assert window["saving_dkk_kwh"] == 1.5


def test_now_being_the_cheapest_moment_shows_nothing_to_gain():
    """Plug in. The honest answer some of the time, and a card that only ever
    says "wait" would be one people stop believing."""
    rows = _rows([0.5, 0.5, 0.5, 0.5, 2.0, 2.0, 2.0, 2.0])
    window = electricityapp.cheapest_window(rows, slots=4, now_key=rows[0]["time_dk"])
    assert window["saving_dkk_kwh"] == 0.0


def test_no_comparison_when_starting_now_would_run_off_the_prices():
    """There is no honest number for a charge that finishes past the last
    published quarter, so none is offered."""
    rows = _rows([1.0] * 5)
    window = electricityapp.cheapest_window(rows, slots=4, now_key=rows[3]["time_dk"])
    assert window["now_average_dkk_kwh"] is None
    assert window["saving_dkk_kwh"] is None


# --- how long a charge takes --------------------------------------------------


def test_the_length_comes_from_this_household_s_own_sessions():
    sessions = [{"duration_minutes": 95}, {"duration_minutes": 110},
                {"duration_minutes": 130}]
    assert electricityapp.typical_charge_minutes(sessions) == 110


def test_a_median_so_one_overnight_plug_in_does_not_stretch_it():
    """A car left connected after it finished reports hours of nothing. The
    mean would follow it; the median does not."""
    sessions = [{"duration_minutes": 100}, {"duration_minutes": 110},
                {"duration_minutes": 105}, {"duration_minutes": 700}]
    assert electricityapp.typical_charge_minutes(sessions) <= 110


def test_a_plug_in_that_drew_almost_nothing_is_not_a_charge():
    sessions = [{"duration_minutes": 3}, {"duration_minutes": 4},
                {"duration_minutes": 120}]
    assert electricityapp.typical_charge_minutes(sessions) == 120


def test_no_history_means_no_measured_length():
    """The endpoint falls back to a default; this function refuses to invent
    one, so the caller can say which it used."""
    assert electricityapp.typical_charge_minutes([]) is None
    assert electricityapp.typical_charge_minutes([{"duration_minutes": None}]) is None


# --- through the endpoint -----------------------------------------------------


def test_the_endpoint_answers_with_no_prices(client):
    body = client.get("/api/charge-window").get_json()
    assert body["window"] is None
    assert body["minutes"] == int(electricityapp.DEFAULT_CHARGE_HOURS * 60)
    assert body["minutes_source"] == "default"


def test_an_explicit_length_is_honoured(client):
    body = client.get("/api/charge-window?hours=3").get_json()
    assert body["minutes"] == 180
    assert body["minutes_source"] == "asked for"


def test_a_nonsense_length_is_a_400(client):
    assert client.get("/api/charge-window?hours=banana").status_code == 400


def test_an_absurd_length_is_clamped_not_refused(client):
    """Somebody typing 100 hours meant something; a day is the most that can
    be answered from published prices."""
    assert client.get("/api/charge-window?hours=100").get_json()["minutes"] == 24 * 60
    assert client.get("/api/charge-window?hours=0.01").get_json()["minutes"] == 15


# --- the card -----------------------------------------------------------------


def _static(name):
    import os
    sub = "templates" if name.endswith(".html") else "static"
    with open(os.path.join(os.path.dirname(__file__), "..", sub, name),
              encoding="utf-8") as handle:
        return handle.read()


def test_the_card_sits_with_the_prices_not_the_charging_history():
    """The question is asked before a charge; the charging card is a record of
    ones that already happened."""
    html = _static("index.html")
    assert html.index('id="charge-window"') < html.index('id="curve-card"')


def test_no_prices_says_so_rather_than_showing_nothing():
    """"No answer" and "no prices published yet" look identical on a blank
    card, and only one of them is worth waiting out."""
    js = _static("app.js")
    fn = js[js.index("async function loadChargeWindow("):]
    fn = fn[:fn.index("\n}\n")]
    assert "Not enough prices yet" in fn
    assert "13:00" in fn


# --- end to end, on a day shaped like a real one ------------------------------


def _seed_day(conn, start_midnight, shape):
    """One day of quarter-hourly spot prices from an hour->price map."""
    for hour, spot in shape.items():
        for quarter in range(4):
            at = start_midnight + datetime.timedelta(hours=hour, minutes=15 * quarter)
            conn.execute(
                "INSERT OR REPLACE INTO prices (time_dk, price_area, "
                "spot_price_dkk_kwh, fetched_at) VALUES (?, 'DK2', ?, ?)",
                (at.isoformat(timespec="seconds"), spot, "2026-09-04T00:00:00"))


# Cheap overnight, a solar dip at midday, an evening peak — the shape a Danish
# day actually has.
DAY = {0: 0.28, 1: 0.24, 2: 0.21, 3: 0.20, 4: 0.22, 5: 0.31, 6: 0.52, 7: 0.88,
       8: 0.95, 9: 0.71, 10: 0.48, 11: 0.33, 12: 0.26, 13: 0.25, 14: 0.34,
       15: 0.55, 16: 0.84, 17: 1.32, 18: 1.48, 19: 1.21, 20: 0.93, 21: 0.68,
       22: 0.47, 23: 0.35}


def test_the_overnight_trough_is_chosen_over_the_midday_dip(client, conn, set_options):
    """Both are cheap; only one is cheapest. A day with two dips is exactly
    where picking by eye off the chart goes wrong."""
    set_options(price_area="DK2")
    midnight = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    for day in range(3):
        _seed_day(conn, midnight + datetime.timedelta(days=day), DAY)
    conn.commit()

    body = client.get("/api/charge-window?hours=2").get_json()
    window = body["window"]
    assert window is not None
    started = datetime.datetime.fromisoformat(window["start"])
    assert 1 <= started.hour <= 4, f"picked {window['start']}, not the overnight trough"


def test_the_evening_peak_is_never_chosen(client, conn, set_options):
    set_options(price_area="DK2")
    midnight = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    for day in range(3):
        _seed_day(conn, midnight + datetime.timedelta(days=day), DAY)
    conn.commit()

    window = client.get("/api/charge-window?hours=2").get_json()["window"]
    assert not (17 <= datetime.datetime.fromisoformat(window["start"]).hour <= 20)


def test_a_longer_charge_still_lands_overnight(client, conn, set_options):
    """Four hours no longer fits the midday dip at all, so the trough has to
    win — and the window must not straddle the morning peak to get there."""
    set_options(price_area="DK2")
    midnight = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    for day in range(3):
        _seed_day(conn, midnight + datetime.timedelta(days=day), DAY)
    conn.commit()

    window = client.get("/api/charge-window?hours=4").get_json()["window"]
    ends = datetime.datetime.fromisoformat(window["end"])
    assert ends.hour <= 6, f"the window ran into the morning peak: {window['end']}"
