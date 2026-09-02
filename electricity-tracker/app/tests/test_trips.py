"""Logging a long trip against the charging history.

"Why did we use 60 kWh that week" is a question the numbers alone cannot
answer, and the answer is usually that somebody drove to Jutland. A trip is a
stretch of days with a label; the chart shades it, so a spike has its reason
beside it.

The figure it reports is **charging during those dates, not the energy the trip
consumed**, and the two are not close. You arrive home empty and plug in that
evening, so the charge that paid for the last 200 km lands on the day you got
back. Which dates a trip covers is therefore the keeper's decision, and the
tests below pin that the app does not quietly make it for them.
"""
import json

import pytest

import app as et


def _session(day, kwh, cost=None):
    """A session shaped the way easee_sessions_reconciled emits them."""
    return {"day": day, "energy_kwh": kwh, "cost_dkk": cost,
            "started_at": f"{day}T10:00:00+02:00", "ended_at": f"{day}T12:00:00+02:00"}


def _trip(started, ended=None, label="Aarhus", km=None):
    return {"id": 1, "started_on": started, "ended_on": ended or started,
            "label": label, "distance_km": km, "notes": None}


# --- what charging a trip picks up --------------------------------------------


def test_charging_inside_the_dates_is_counted():
    [trip] = et.trips_with_charging(
        [_trip("2026-09-01", "2026-09-03")],
        [_session("2026-09-01", 20.0, 30.0), _session("2026-09-02", 15.0, 20.0)])
    assert trip["sessions"] == 2
    assert trip["energy_kwh"] == 35.0
    assert trip["cost_dkk"] == 50.0


def test_the_boundary_days_are_included():
    """A trip "1 to 3 September" is three days to a person, and an exclusive
    end would silently drop the day you drove home."""
    [trip] = et.trips_with_charging(
        [_trip("2026-09-01", "2026-09-03")],
        [_session("2026-09-01", 10.0, 10.0), _session("2026-09-03", 10.0, 10.0)])
    assert trip["sessions"] == 2


def test_charging_outside_the_dates_is_not_counted():
    [trip] = et.trips_with_charging(
        [_trip("2026-09-02")],
        [_session("2026-09-01", 40.0, 60.0), _session("2026-09-03", 40.0, 60.0)])
    assert trip["sessions"] == 0
    assert trip["energy_kwh"] == 0


def test_a_trip_with_no_charging_at_all_still_reports():
    """Perfectly normal — you charged before you left. The trip still explains
    the shape of the week, which is most of why it was logged."""
    [trip] = et.trips_with_charging([_trip("2026-09-02")], [])
    assert trip["sessions"] == 0
    assert trip["cost_dkk"] is None
    assert trip["kwh_per_100km"] is None


def test_one_unpriced_session_makes_the_whole_cost_unknown():
    """Not low. The same rule the monthly roll-up uses, for the same reason: a
    partial figure sitting beside complete ones invites comparison."""
    [trip] = et.trips_with_charging(
        [_trip("2026-09-01", "2026-09-02")],
        [_session("2026-09-01", 20.0, 30.0), _session("2026-09-02", 15.0, None)])
    assert trip["energy_kwh"] == 35.0
    assert trip["cost_dkk"] is None


# --- putting it against the distance ------------------------------------------


def test_distance_gives_a_rate_per_hundred_kilometres():
    [trip] = et.trips_with_charging(
        [_trip("2026-09-01", "2026-09-02", km=480)],
        [_session("2026-09-01", 96.0, 144.0)])
    assert trip["kwh_per_100km"] == 20.0
    assert trip["dkk_per_100km"] == 30.0


def test_without_a_distance_there_is_no_rate():
    """Offered when it can be worked out, blank when it cannot — never zero,
    which would read as a car that used nothing."""
    [trip] = et.trips_with_charging(
        [_trip("2026-09-01", km=None)], [_session("2026-09-01", 96.0, 144.0)])
    assert trip["kwh_per_100km"] is None
    assert trip["dkk_per_100km"] is None


def test_no_charging_means_no_rate_even_with_a_distance():
    [trip] = et.trips_with_charging([_trip("2026-09-01", km=480)], [])
    assert trip["kwh_per_100km"] is None


# --- the days the chart shades ------------------------------------------------


def test_every_day_a_trip_covers_is_marked():
    days = et.trip_days([_trip("2026-09-01", "2026-09-04", label="Aarhus")])
    assert sorted(days) == ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"]
    assert set(days.values()) == {"Aarhus"}


def test_a_one_day_trip_marks_one_day():
    assert list(et.trip_days([_trip("2026-09-02")])) == ["2026-09-02"]


def test_overlapping_trips_do_not_lose_a_day():
    days = et.trip_days([
        _trip("2026-09-01", "2026-09-03", label="First"),
        _trip("2026-09-03", "2026-09-05", label="Second"),
    ])
    assert len(days) == 5
    # The earlier trip owns the shared day; something has to, and taking the
    # first keeps the mapping stable rather than depending on sort order.
    assert days["2026-09-03"] == "First"


def test_a_trip_with_an_unreadable_date_is_skipped_not_fatal():
    """One bad row must not take the chart down with it."""
    days = et.trip_days([_trip("not a date", "2026-09-02"), _trip("2026-09-05")])
    assert list(days) == ["2026-09-05"]


def test_a_month_long_trip_does_not_run_away():
    days = et.trip_days([_trip("2026-09-01", "2026-09-30")])
    assert len(days) == 30


# --- through the API ----------------------------------------------------------


def test_logging_and_listing_a_trip(client):
    response = client.post("/api/trips", json={
        "started_on": "2026-09-01", "ended_on": "2026-09-03",
        "label": "Aarhus and back", "distance_km": 480})
    assert response.status_code == 201

    trips = client.get("/api/trips").get_json()["trips"]
    assert len(trips) == 1
    assert trips[0]["label"] == "Aarhus and back"
    assert trips[0]["distance_km"] == 480


def test_the_end_defaults_to_the_start(client):
    """A one-day trip is the common case and should not need the field filled
    in twice."""
    client.post("/api/trips", json={"started_on": "2026-09-02", "label": "Odense"})
    [trip] = client.get("/api/trips").get_json()["trips"]
    assert trip["ended_on"] == "2026-09-02"


def test_a_trip_needs_a_label(client):
    response = client.post("/api/trips", json={"started_on": "2026-09-01", "label": "  "})
    assert response.status_code == 400
    assert "label" in response.get_json()["error"]


@pytest.mark.parametrize("started", [None, "", "not a date", "01/09/2026"])
def test_a_trip_needs_a_real_start_date(client, started):
    response = client.post("/api/trips", json={"started_on": started, "label": "Aarhus"})
    assert response.status_code == 400


def test_a_trip_cannot_end_before_it_starts(client):
    response = client.post("/api/trips", json={
        "started_on": "2026-09-05", "ended_on": "2026-09-01", "label": "Aarhus"})
    assert response.status_code == 400
    assert "before it starts" in response.get_json()["error"]


@pytest.mark.parametrize("distance", ["banana", -5, 0])
def test_a_nonsense_distance_is_refused(client, distance):
    response = client.post("/api/trips", json={
        "started_on": "2026-09-01", "label": "Aarhus", "distance_km": distance})
    assert response.status_code == 400


def test_a_blank_distance_is_simply_absent(client):
    """Optional, and an empty form field must not become a zero — which would
    then divide into an infinite kWh/100 km."""
    client.post("/api/trips", json={
        "started_on": "2026-09-01", "label": "Aarhus", "distance_km": ""})
    [trip] = client.get("/api/trips").get_json()["trips"]
    assert trip["distance_km"] is None
    assert trip["kwh_per_100km"] is None


def test_deleting_a_trip(client):
    client.post("/api/trips", json={"started_on": "2026-09-01", "label": "Aarhus"})
    [trip] = client.get("/api/trips").get_json()["trips"]
    assert client.delete(f"/api/trips/{trip['id']}").status_code == 200
    assert client.get("/api/trips").get_json()["trips"] == []


def test_deleting_a_trip_that_is_not_there(client):
    assert client.delete("/api/trips/999").status_code == 404


def test_trips_come_back_newest_first(client):
    for day, label in (("2026-08-01", "Old"), ("2026-09-01", "New")):
        client.post("/api/trips", json={"started_on": day, "label": label})
    assert [t["label"] for t in client.get("/api/trips").get_json()["trips"]] == ["New", "Old"]


def test_the_charging_history_carries_the_days_to_shade(client, set_options):
    set_options(easee_enabled=True, easee_username="u", easee_password="p",
                easee_charger_id="EH1")
    client.post("/api/trips", json={
        "started_on": "2026-09-01", "ended_on": "2026-09-02", "label": "Aarhus"})
    body = client.get("/api/easee/history?days=30").get_json()
    assert body["trip_days"] == {"2026-09-01": "Aarhus", "2026-09-02": "Aarhus"}


def test_trips_are_in_the_change_feed_and_the_export(client):
    assert "ev_trips" in et.TRACKED_TABLES
    client.post("/api/trips", json={"started_on": "2026-09-01", "label": "Aarhus"})
    assert len(client.get("/api/export").get_json()["tables"]["ev_trips"]) == 1


# --- the chart ----------------------------------------------------------------


def _static(name):
    import os
    sub = "templates" if name.endswith(".html") else "static"
    with open(os.path.join(os.path.dirname(__file__), "..", sub, name),
              encoding="utf-8") as handle:
        return handle.read()


def test_the_band_is_drawn_behind_the_data():
    """`extras` is composed after the lines, so a band built there would paint
    over the chart it is supposed to sit behind."""
    js = _static("app.js")
    composition = js[js.index("`<defs>${defs}</defs>`"):][:200]
    assert "grid + bands + areas + lines" in composition


def test_a_trip_is_a_band_not_a_pin():
    """A trip is a stretch of days, and one mark would say the driving happened
    at a single moment."""
    js = _static("app.js")
    assert "chart-trip-band" in js
    assert "chart-trip-band" in _static("style.css")


def test_a_one_day_trip_is_still_wide_enough_to_see():
    js = _static("app.js")
    assert "const half = (stepX || 8) / 2;" in js


def test_the_tooltip_names_the_trip():
    """The spike and its reason in the same place is the entire point."""
    js = _static("app.js")
    fn = js[js.index("function renderChargingHistory("):]
    assert "trip ? `${base} · ${trip}`" in fn


def test_the_form_reports_errors_in_place():
    """A dialog makes you dismiss it before you can look at the field it is
    complaining about."""
    js = _static("app.js")
    assert 'error.textContent = body.error' in js
    assert 'id="trip-error"' in _static("index.html")
