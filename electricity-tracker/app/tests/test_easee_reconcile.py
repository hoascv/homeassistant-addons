"""Reconciling the polled charging history against Easee's own record.

The polls cannot see the whole of a charge. A session is rebuilt from
five-minute samples, so whatever the charger delivered between the last poll
and the cable coming out is never observed — always missing, never
over-counting, bounded by the poll interval times the charge rate. A real case:
Easee reported 20.58 kWh for a session the add-on reported as 20.06, a 0.52 kWh
gap on a ~11 kW charge, which is about three minutes of charging after the last
poll.

So energy comes from Easee. Timing does not: `carConnected`/`carDisconnected`
is plug-in to unplug, and a car left on an overnight schedule reports twelve
hours of which it charged for two. Most of what follows is about keeping that
division straight, and about refusing to guess when the two sources disagree
about the *shape* of a session rather than its size.
"""
from datetime import datetime, timedelta, timezone

import pytest

import app as et

UTC = timezone.utc
LOCAL = et.LOCAL_TZ


def _utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def _polled(started, ended, energy, cost=10.0, covered=None, partial=False):
    """A session shaped the way easee_sessions() emits them."""
    start, end = _utc(started), _utc(ended)
    covered = energy if covered is None else covered
    return {
        "started_at": start.isoformat(),
        "ended_at": end.isoformat(),
        "day": start.astimezone(LOCAL).date().isoformat(),
        "energy_kwh": energy,
        "cost_dkk": cost,
        "avg_dkk_kwh": round(cost / covered, 4) if cost is not None and covered else None,
        "duration_minutes": round((end - start).total_seconds() / 60),
        "cost_covers_kwh": covered,
        "cost_is_partial": partial,
        "ongoing": False,
    }


def _cloud(connected, disconnected, energy):
    return {
        "connected_at": connected,
        "disconnected_at": disconnected,
        "energy_kwh": energy,
        "_start": _utc(connected),
        "_end": _utc(disconnected) if disconnected else None,
    }


def _flat_price(value):
    return lambda when: value


NOW = _utc("2026-09-01T18:00:00")


def _reconcile(sampled, cloud, price=_flat_price(1.50)):
    return et.reconcile_sessions(sampled, cloud, price, LOCAL, now_utc=NOW)


# --- the case this was built for ---------------------------------------------


def test_the_tail_the_polls_never_saw_is_recovered():
    """The real numbers: 20.06 polled, 20.58 in Easee's record."""
    polled = _polled("2026-09-01T07:17:00", "2026-09-01T09:07:00", 20.06, cost=31.72)
    cloud = _cloud("2026-09-01T07:15:00", "2026-09-01T09:10:00", 20.58)

    [session] = _reconcile([polled], [cloud])

    assert session["energy_kwh"] == 20.58
    assert session["energy_source"] == "easee"
    assert session["polled_energy_kwh"] == 20.06
    # The missing 0.52 kWh was delivered in the hour the polled session ended,
    # the only hour it can have happened in.
    assert session["cost_dkk"] == round(31.72 + 0.52 * 1.50, 2)
    assert session["cost_is_partial"] is False


def test_the_charging_window_is_kept_not_the_plug_in_span():
    """Easee's span is cable-in to cable-out. A car on an overnight schedule
    reports twelve hours of which it charged for two, and reporting that as the
    session would make every duration and every kr/kWh meaningless."""
    polled = _polled("2026-09-01T02:00:00", "2026-09-01T04:00:00", 22.0)
    cloud = _cloud("2026-08-31T18:00:00", "2026-09-01T06:30:00", 22.4)

    [session] = _reconcile([polled], [cloud])

    assert session["duration_minutes"] == 120
    assert session["started_at"] == _utc("2026-09-01T02:00:00").isoformat()
    assert "span_is_plugged_in" not in session


def test_easee_agreeing_with_the_polls_changes_nothing():
    polled = _polled("2026-09-01T07:00:00", "2026-09-01T09:00:00", 20.0, cost=30.0)
    [session] = _reconcile([polled], [_cloud("2026-09-01T07:00:00", "2026-09-01T09:00:00", 20.0)])
    assert session["energy_kwh"] == 20.0
    assert session["cost_dkk"] == 30.0


def test_easee_reporting_slightly_less_never_lowers_the_energy():
    """A rounding difference the other way. Taking the smaller number would
    make a charge shrink between page loads for no reason anybody could see."""
    polled = _polled("2026-09-01T07:00:00", "2026-09-01T09:00:00", 20.06, cost=30.0)
    [session] = _reconcile([polled], [_cloud("2026-09-01T07:00:00", "2026-09-01T09:00:00", 20.05)])
    assert session["energy_kwh"] == 20.06
    assert session["cost_dkk"] == 30.0


def test_an_unpriced_tail_hour_marks_the_cost_partial_rather_than_guessing():
    """No spot price for that hour. The extra energy is still real and still
    reported; what is not known is what it cost."""
    polled = _polled("2026-09-01T07:00:00", "2026-09-01T09:00:00", 20.0, cost=30.0)
    cloud = _cloud("2026-09-01T07:00:00", "2026-09-01T09:10:00", 21.0)

    [session] = _reconcile([polled], [cloud], price=lambda when: None)

    assert session["energy_kwh"] == 21.0
    assert session["cost_dkk"] == 30.0
    assert session["cost_is_partial"] is True
    # And the rate is quoted against what the cost covers, never the new total.
    assert session["avg_dkk_kwh"] == round(30.0 / 20.0, 4)


def test_an_already_partial_cost_stays_partial():
    polled = _polled("2026-09-01T07:00:00", "2026-09-01T09:00:00", 20.0,
                     cost=None, covered=12.0, partial=True)
    [session] = _reconcile([polled], [_cloud("2026-09-01T07:00:00", "2026-09-01T09:10:00", 20.6)])
    assert session["energy_kwh"] == 20.6
    assert session["cost_dkk"] is None
    assert session["cost_is_partial"] is True


# --- sessions the polls never saw at all --------------------------------------


def test_a_charge_missed_entirely_is_recovered_from_easee():
    """The add-on was down, or restarted mid-charge. Before this the charge
    simply did not exist; now it does, with its cost marked an estimate."""
    cloud = _cloud("2026-08-30T10:00:00", "2026-08-30T12:00:00", 12.0)

    [session] = _reconcile([], [cloud])

    assert session["energy_kwh"] == 12.0
    assert session["energy_source"] == "easee"
    assert session["cost_is_estimated"] is True
    # Spread evenly across two hours at 1.50 kr.
    assert session["cost_dkk"] == round(12.0 * 1.50, 2)


def test_a_recovered_session_says_its_span_is_plug_in_to_unplug():
    """Its duration is not comparable with a polled session's, and a screen
    that does not say so invites exactly that comparison."""
    [session] = _reconcile([], [_cloud("2026-08-30T10:00:00", "2026-08-30T22:00:00", 12.0)])
    assert session["span_is_plugged_in"] is True
    assert session["duration_minutes"] == 720


def test_a_recovered_session_with_no_prices_reports_no_cost_not_zero():
    """0,00 kr on screen is a claim. "Not known" is the truth."""
    [session] = _reconcile([], [_cloud("2026-08-30T10:00:00", "2026-08-30T12:00:00", 12.0)],
                           price=lambda when: None)
    assert session["cost_dkk"] is None
    assert session["cost_covers_kwh"] == 0.0
    assert session["avg_dkk_kwh"] is None


def test_a_half_priced_span_reports_partial_rather_than_cheap():
    """Half the hours priced would otherwise report half the true cost as if it
    were the whole of it — a charge that looks like a bargain."""
    def price(when):
        return 2.0 if when.hour < 11 else None

    [session] = _reconcile([], [_cloud("2026-08-30T10:00:00", "2026-08-30T12:00:00", 12.0)],
                           price=price)
    assert session["cost_dkk"] == 12.0  # 6 kWh in the priced hour, at 2.00
    assert session["cost_is_partial"] is True


def test_an_ongoing_cloud_session_runs_to_now():
    [session] = _reconcile([], [_cloud("2026-09-01T17:00:00", None, 5.0)])
    assert session["ongoing"] is True
    assert session["duration_minutes"] == 60


# --- refusing to guess --------------------------------------------------------


def test_several_polled_sessions_in_one_plug_in_are_left_alone():
    """Attributing one cloud total across them would either double-count the
    energy or invent a split of it. Saying nothing is better than either."""
    first = _polled("2026-09-01T02:00:00", "2026-09-01T03:00:00", 10.0)
    second = _polled("2026-09-01T04:00:00", "2026-09-01T05:00:00", 11.0)
    cloud = _cloud("2026-09-01T01:00:00", "2026-09-01T06:00:00", 30.0)

    sessions = _reconcile([first, second], [cloud])

    assert [s["energy_kwh"] for s in sessions] == [11.0, 10.0]
    assert {s["energy_source"] for s in sessions} == {"polled"}
    # And the cloud session is not also emitted, which would count it twice.
    assert len(sessions) == 2


def test_a_polled_session_with_no_cloud_record_survives():
    """The hourly session sync may not have run since the charge finished.
    Dropping it would make a charge vanish from the page for up to an hour."""
    polled = _polled("2026-09-01T15:00:00", "2026-09-01T16:00:00", 9.0)
    sessions = _reconcile([polled], [_cloud("2026-08-20T10:00:00", "2026-08-20T12:00:00", 5.0)])
    assert len(sessions) == 2
    assert sessions[0]["energy_kwh"] == 9.0
    assert sessions[0]["energy_source"] == "polled"


def test_nothing_from_either_source_is_an_empty_list():
    assert _reconcile([], []) == []


def test_sessions_come_back_newest_first():
    """The list is printed in this order and the daily chart reads it."""
    old = _polled("2026-08-20T10:00:00", "2026-08-20T11:00:00", 5.0)
    new = _polled("2026-09-01T10:00:00", "2026-09-01T11:00:00", 6.0)
    sessions = _reconcile([old, new], [
        _cloud("2026-08-20T10:00:00", "2026-08-20T11:05:00", 5.1),
        _cloud("2026-09-01T10:00:00", "2026-09-01T11:05:00", 6.1),
    ])
    assert [s["energy_kwh"] for s in sessions] == [6.1, 5.1]


def test_a_polled_session_matches_across_the_plug_in_boundary():
    """The polls that bracket a charge sit up to one interval outside the
    plug-in moment, so an exact containment test misses the very match that
    matters — and would then report the charge twice."""
    polled = _polled("2026-09-01T07:58:00", "2026-09-01T09:03:00", 11.0)
    cloud = _cloud("2026-09-01T08:00:00", "2026-09-01T09:00:00", 11.4)
    sessions = _reconcile([polled], [cloud])
    assert len(sessions) == 1
    assert sessions[0]["energy_kwh"] == 11.4


# --- reading Easee's timestamps ----------------------------------------------


def test_easee_timestamps_are_read_as_utc_however_they_arrive():
    """Their API has been seen returning both a Z suffix and a bare naive
    stamp. Reading the naive one as local time would move every session by the
    UTC offset — two hours in a Danish summer, enough to file a charge on the
    wrong day."""
    with_z = et._parse_easee_stamp("2026-09-01T07:15:00Z")
    naive = et._parse_easee_stamp("2026-09-01T07:15:00")
    offset = et._parse_easee_stamp("2026-09-01T09:15:00+02:00")
    assert with_z == naive == offset


def test_an_unreadable_timestamp_is_none_not_a_crash():
    assert et._parse_easee_stamp("not a date") is None
    assert et._parse_easee_stamp("") is None
    assert et._parse_easee_stamp(None) is None


# --- the client call ----------------------------------------------------------


def test_get_sessions_asks_for_the_documented_path(monkeypatch):
    import easee
    seen = {}

    def fake_request(path, method="GET", body=None, access_token=None, timeout=15):
        seen["path"] = path
        return []

    monkeypatch.setattr(easee, "_request", fake_request)
    easee.get_sessions("tok", "EH123",
                       datetime(2026, 8, 1, 6, 0, tzinfo=UTC),
                       datetime(2026, 9, 1, 6, 0, tzinfo=UTC))
    assert seen["path"] == (
        "/api/sessions/charger/EH123/sessions/2026-08-01T06:00:00/2026-09-01T06:00:00")


def test_get_sessions_converts_to_utc_before_dropping_the_offset(monkeypatch):
    """isoformat() of a naive datetime is what pyeasee sends, so the offset has
    to go — but the instant must survive it."""
    import easee
    seen = {}
    monkeypatch.setattr(easee, "_request",
                        lambda path, **kw: seen.setdefault("path", path) and [] or [])
    easee.get_sessions("tok", "EH1",
                       datetime(2026, 8, 1, 8, 0, tzinfo=LOCAL),
                       datetime(2026, 8, 1, 9, 0, tzinfo=LOCAL))
    assert "2026-08-01T06:00:00" in seen["path"]


def test_get_sessions_maps_easees_field_names(monkeypatch):
    import easee
    monkeypatch.setattr(easee, "_request", lambda path, **kw: [
        {"carConnected": "2026-09-01T07:15:00Z",
         "carDisconnected": "2026-09-01T09:10:00Z", "kiloWattHours": 20.58},
    ])
    assert easee.get_sessions("tok", "EH1", datetime(2026, 9, 1, tzinfo=UTC),
                              datetime(2026, 9, 2, tzinfo=UTC)) == [
        {"connected_at": "2026-09-01T07:15:00Z",
         "disconnected_at": "2026-09-01T09:10:00Z", "energy_kwh": 20.58},
    ]


def test_a_session_still_running_has_no_disconnect(monkeypatch):
    import easee
    monkeypatch.setattr(easee, "_request", lambda path, **kw: [
        {"carConnected": "2026-09-01T17:00:00Z", "kiloWattHours": 4.2},
    ])
    [session] = easee.get_sessions("tok", "EH1", datetime(2026, 9, 1, tzinfo=UTC),
                                   datetime(2026, 9, 2, tzinfo=UTC))
    assert session["disconnected_at"] is None


def test_sessions_without_a_start_or_energy_are_skipped(monkeypatch):
    """Inventing a value for either would put a fiction in the history."""
    import easee
    monkeypatch.setattr(easee, "_request", lambda path, **kw: [
        {"carConnected": None, "kiloWattHours": 5.0},
        {"carConnected": "2026-09-01T07:00:00Z", "kiloWattHours": None},
        {"carConnected": "2026-09-01T08:00:00Z", "kiloWattHours": "not a number"},
        {"carConnected": "2026-09-01T09:00:00Z", "kiloWattHours": 7.0},
    ])
    sessions = easee.get_sessions("tok", "EH1", datetime(2026, 9, 1, tzinfo=UTC),
                                  datetime(2026, 9, 2, tzinfo=UTC))
    assert [s["energy_kwh"] for s in sessions] == [7.0]


def test_a_non_list_response_is_no_sessions(monkeypatch):
    import easee
    monkeypatch.setattr(easee, "_request", lambda path, **kw: {"error": "nope"})
    assert easee.get_sessions("tok", "EH1", datetime(2026, 9, 1, tzinfo=UTC),
                              datetime(2026, 9, 2, tzinfo=UTC)) == []


# --- the slow sync ------------------------------------------------------------


def _easee_on(set_options):
    set_options(easee_enabled=True, easee_username="u", easee_password="p",
                easee_charger_id="EH1")


def test_the_session_sync_stores_what_easee_returns(conn, set_options, monkeypatch):
    _easee_on(set_options)
    monkeypatch.setattr(et, "_get_easee_access_token", lambda *a, **k: "tok")
    monkeypatch.setattr(et, "_resolve_easee_charger_id", lambda *a, **k: "EH1")
    import easee
    monkeypatch.setattr(easee, "get_sessions", lambda *a, **k: [
        {"connected_at": "2026-09-01T07:15:00Z",
         "disconnected_at": "2026-09-01T09:10:00Z", "energy_kwh": 20.58},
    ])
    et.sync_easee_cloud_sessions(conn, et._read_options())
    rows = conn.execute("SELECT * FROM easee_cloud_sessions").fetchall()
    assert len(rows) == 1 and rows[0]["energy_kwh"] == 20.58


def test_a_session_is_updated_in_place_not_duplicated(conn, set_options, monkeypatch):
    """A session still running at one sync gets its end and final energy from
    the next. Appending would leave two rows for one charge."""
    _easee_on(set_options)
    monkeypatch.setattr(et, "_get_easee_access_token", lambda *a, **k: "tok")
    monkeypatch.setattr(et, "_resolve_easee_charger_id", lambda *a, **k: "EH1")
    import easee

    monkeypatch.setattr(easee, "get_sessions", lambda *a, **k: [
        {"connected_at": "2026-09-01T07:15:00Z", "disconnected_at": None, "energy_kwh": 8.0}])
    et.sync_easee_cloud_sessions(conn, et._read_options())

    monkeypatch.setattr(easee, "get_sessions", lambda *a, **k: [
        {"connected_at": "2026-09-01T07:15:00Z",
         "disconnected_at": "2026-09-01T09:10:00Z", "energy_kwh": 20.58}])
    et.sync_easee_cloud_sessions(conn, et._read_options(), force=True)

    rows = conn.execute("SELECT * FROM easee_cloud_sessions").fetchall()
    assert len(rows) == 1
    assert rows[0]["energy_kwh"] == 20.58
    assert rows[0]["disconnected_at"] == "2026-09-01T09:10:00Z"


def test_the_session_sync_is_throttled(conn, set_options, monkeypatch):
    """pyeasee throttles this endpoint, so Easee evidently rate-limits it. It
    must not ride the five-minute sampling tick."""
    _easee_on(set_options)
    monkeypatch.setattr(et, "_get_easee_access_token", lambda *a, **k: "tok")
    monkeypatch.setattr(et, "_resolve_easee_charger_id", lambda *a, **k: "EH1")
    import easee
    calls = []
    monkeypatch.setattr(easee, "get_sessions", lambda *a, **k: calls.append(1) or [])

    start = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    et.sync_easee_cloud_sessions(conn, et._read_options(), now=start)
    et.sync_easee_cloud_sessions(conn, et._read_options(), now=start + timedelta(minutes=5))
    et.sync_easee_cloud_sessions(conn, et._read_options(), now=start + timedelta(minutes=55))
    assert len(calls) == 1
    et.sync_easee_cloud_sessions(conn, et._read_options(), now=start + timedelta(minutes=61))
    assert len(calls) == 2


def test_nothing_syncs_while_easee_is_off(conn, set_options, monkeypatch):
    set_options(easee_enabled=False, easee_username="u", easee_password="p")
    import easee
    monkeypatch.setattr(easee, "get_sessions",
                        lambda *a, **k: pytest.fail("should not be called"))
    et.sync_easee_cloud_sessions(conn, et._read_options())


def test_a_failing_session_fetch_leaves_the_history_alone(conn, set_options, monkeypatch):
    """The polled history is still there and still useful. A cloud outage must
    not empty the page."""
    _easee_on(set_options)
    monkeypatch.setattr(et, "_get_easee_access_token", lambda *a, **k: "tok")
    monkeypatch.setattr(et, "_resolve_easee_charger_id", lambda *a, **k: "EH1")
    import easee

    def boom(*a, **k):
        raise easee.EaseeError("503 from Easee")

    monkeypatch.setattr(easee, "get_sessions", boom)
    et.sync_easee_cloud_sessions(conn, et._read_options())
    assert conn.execute("SELECT COUNT(*) AS n FROM easee_cloud_sessions").fetchone()["n"] == 0


# --- through the endpoint -----------------------------------------------------


def test_a_cloud_only_session_reaches_the_history_endpoint(client, conn, set_options):
    """The seam has to be behind the endpoint, not in front of it: the chart,
    the monthly roll-up and the printed list all read this one payload, and if
    they disagreed the page would contradict itself."""
    set_options(easee_enabled=True, easee_username="u", easee_password="p",
                easee_charger_id="EH1")
    conn.execute(
        "INSERT INTO easee_cloud_sessions "
        "(charger_id, connected_at, disconnected_at, energy_kwh, fetched_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("EH1", "2026-09-01T07:15:00Z", "2026-09-01T09:10:00Z", 20.58, "2026-09-01T18:00:00Z"),
    )
    conn.commit()

    body = client.get("/api/easee/history?days=30").get_json()

    assert body["enabled"] is True
    [session] = body["sessions"]
    assert session["energy_kwh"] == 20.58
    assert session["energy_source"] == "easee"
    assert session["cost_is_estimated"] is True
    # And it is carried through the derived views rather than only the list.
    assert body["totals"]["sessions"] == 1
    assert body["monthly"]["months"][0]["energy_kwh"] == 20.58


def test_without_any_cloud_history_the_polled_list_still_answers(client, set_options):
    """Turning the option on, or a first run before the hourly sync fires. An
    empty page here would be a regression against doing nothing."""
    set_options(easee_enabled=True, easee_username="u", easee_password="p",
                easee_charger_id="EH1")
    body = client.get("/api/easee/history?days=30").get_json()
    assert body["enabled"] is True
    assert body["sessions"] == []


# --- the live card must not contradict the list underneath it -----------------


def _sampled_session_rows(conn, charger_id="EH1"):
    """Samples describing one finished charge: 07:15 plug in, energy rising to
    20.06 by 09:05, unplugged before the next poll."""
    energies = [(0, 0.0), (5, 1.0), (60, 11.0), (110, 20.06)]
    for minutes, kwh in energies:
        ts = (datetime(2026, 9, 1, 7, 15, tzinfo=UTC) + timedelta(minutes=minutes)).isoformat()
        conn.execute(
            "INSERT INTO easee_samples (ts_utc, charger_id, status, session_energy_kwh, "
            "total_power_w, fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
            (ts, charger_id, "CHARGING", kwh, 11000, ts))
    end = datetime(2026, 9, 1, 9, 20, tzinfo=UTC).isoformat()
    conn.execute(
        "INSERT INTO easee_samples (ts_utc, charger_id, status, session_energy_kwh, "
        "total_power_w, fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
        (end, charger_id, "DISCONNECTED", 0.0, 0, end))


def _cloud_row(conn, energy=20.58, disconnected="2026-09-01T09:10:00Z"):
    conn.execute(
        "INSERT INTO easee_cloud_sessions (charger_id, connected_at, disconnected_at, "
        "energy_kwh, fetched_at) VALUES (?, ?, ?, ?, ?)",
        ("EH1", "2026-09-01T07:15:00Z", disconnected, energy, "2026-09-01T18:00:00Z"))


def test_the_live_card_shows_the_same_energy_as_the_history(client, conn, set_options):
    """They were showing 20.06 and 20.58 for one charge on one screen. Putting
    the reconciliation behind the history endpoint alone was not enough — the
    card is a different endpoint reading the same event."""
    set_options(easee_enabled=True, easee_username="u", easee_password="p",
                easee_charger_id="EH1")
    _sampled_session_rows(conn)
    _cloud_row(conn)
    conn.commit()

    card = client.get("/api/easee/now").get_json()["session"]
    history = client.get("/api/easee/history?days=30").get_json()["sessions"][0]

    assert card["session_energy_kwh"] == history["energy_kwh"] == 20.58
    assert card["session_cost_dkk"] == history["cost_dkk"]
    assert card["energy_source"] == "easee"


def test_a_running_charge_is_left_on_the_live_counter(client, conn, set_options):
    """Easee's record is fetched hourly, so during a charge it is behind the
    counter. Correcting from it would make the number on screen jump backwards
    between refreshes — worse than being a little low for an hour."""
    set_options(easee_enabled=True, easee_username="u", easee_password="p",
                easee_charger_id="EH1")
    for minutes, kwh in ((0, 0.0), (5, 1.0), (60, 11.0)):
        ts = (datetime(2026, 9, 1, 7, 15, tzinfo=UTC) + timedelta(minutes=minutes)).isoformat()
        conn.execute(
            "INSERT INTO easee_samples (ts_utc, charger_id, status, session_energy_kwh, "
            "total_power_w, fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
            (ts, "EH1", "CHARGING", kwh, 11000, ts))
    # Easee's hourly record is stale mid-charge: it still says 5 kWh.
    _cloud_row(conn, energy=5.0, disconnected=None)
    conn.commit()

    card = client.get("/api/easee/now").get_json()["session"]
    assert card["session_energy_kwh"] == 11.0
    assert "energy_source" not in card


def test_the_live_card_survives_having_no_cloud_record(client, conn, set_options):
    set_options(easee_enabled=True, easee_username="u", easee_password="p",
                easee_charger_id="EH1")
    _sampled_session_rows(conn)
    conn.commit()
    card = client.get("/api/easee/now").get_json()["session"]
    assert card["session_energy_kwh"] == 20.06


def test_insights_counts_the_corrected_energy(client, conn, set_options):
    """The EV's share of the house is computed from this. Undercounting the car
    while the house meter is complete understates the share."""
    set_options(easee_enabled=True, easee_username="u", easee_password="p",
                easee_charger_id="EH1")
    _sampled_session_rows(conn)
    _cloud_row(conn)
    conn.commit()
    body = client.get("/api/insights?days=30").get_json()
    assert body["ev"]["energy_kwh"] == 20.58
