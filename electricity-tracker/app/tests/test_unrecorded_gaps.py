"""Saveeye estimates that cover a window nothing recorded.

Every hour bracketed by samples gets an estimate, which is why a day can read
as fully covered and still be short. On 29 August 2026 the counter reset with
the device silent for 65 minutes across it, and the estimate credited the 38 Wh
the counter held when it came back — 35 W average, in a house whose baseline is
six times that. The day showed 24/24 hours and 2.35 kWh missing.

The energy is not recoverable; nothing measured it. What is fixable is the
figure presenting itself as a clean measurement, so this reports the gap.

The fixture is the real reset table, read out of the add-on's own database.
"""
from datetime import datetime, timedelta, timezone

import app as electricityapp

LOCAL = electricityapp.LOCAL_TZ

# (last sample before the reset, first sample after it, counter value on return)
REAL_RESETS = [
    ("2026-08-18T23:30:01+00:00", "2026-08-18T23:35:03+00:00", 9),      # 5 min
    ("2026-08-23T08:09:15+00:00", "2026-08-23T08:15:27+00:00", 181),    # 6 min
    ("2026-08-25T16:18:53+00:00", "2026-08-25T16:24:39+00:00", 8),      # 6 min
    ("2026-08-29T10:17:51+00:00", "2026-08-29T11:22:32+00:00", 38),     # 65 min
    ("2026-09-01T07:38:53+00:00", "2026-09-01T07:47:46+00:00", 886),    # 9 min
]


def _seed(conn, before_ts, after_ts, after_wh, serial="TEST-1"):
    """Samples either side of one reset, five minutes apart, so every hour in
    range is genuinely bracketed."""
    before = datetime.fromisoformat(before_ts)
    after = datetime.fromisoformat(after_ts)
    rows, wh = [], 40000
    step = timedelta(minutes=5)
    ts = before - timedelta(hours=3)
    while ts <= before:
        wh += 200
        rows.append((serial, ts.isoformat(), wh))
        ts += step
    wh = after_wh
    ts = after
    end = after + timedelta(hours=3)
    while ts <= end:
        rows.append((serial, ts.isoformat(), wh))
        wh += 200
        ts += step
    conn.executemany(
        "INSERT INTO saveeye_samples (device_serial, ts_utc, cumulative_wh) VALUES (?, ?, ?)",
        rows)
    conn.commit()


def _minutes(conn, around_ts, serial="TEST-1"):
    middle = datetime.fromisoformat(around_ts).astimezone(LOCAL)
    start = (middle - timedelta(hours=4)).replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=10)
    return electricityapp.saveeye_unrecorded_minutes(conn, start, end, serial)


def test_the_29_august_outage_is_reported(conn):
    """65 minutes crediting 38 Wh is 35 W. No occupied house draws that."""
    before, after, wh = REAL_RESETS[3]
    _seed(conn, before, after, wh)
    found = _minutes(conn, before)
    assert found, "the outage should be reported"
    assert 60 <= sum(found.values()) <= 66
    # It straddles two local hours and is split across them, not double counted.
    assert len(found) == 2


def test_the_other_four_resets_stay_quiet(conn):
    """Four of the five are the system working. A warning on all of them is one
    you learn to ignore, and then miss the day it means something."""
    for index, (before, after, wh) in enumerate(REAL_RESETS):
        if index == 3:
            continue
        conn.execute("DELETE FROM saveeye_samples")
        _seed(conn, before, after, wh)
        assert _minutes(conn, before) == {}, f"reset {index} should not be flagged"


def test_a_long_gap_the_counter_ran_through_is_not_flagged(conn):
    """A 30-minute silence coming back holding 15 kWh is paid for. Reporting it
    as unrecorded would be a lie in the other direction."""
    _seed(conn, "2026-08-29T10:00:00+00:00", "2026-08-29T10:30:00+00:00", 15000)
    assert _minutes(conn, "2026-08-29T10:00:00+00:00") == {}


def test_a_short_unaccounted_gap_is_below_the_floor(conn):
    """Five minutes of a house at 500 W is 42 Wh — nothing against a daily
    total, and a warning that fires on it is one you stop reading."""
    _seed(conn, "2026-08-29T10:00:00+00:00", "2026-08-29T10:05:00+00:00", 1)
    assert _minutes(conn, "2026-08-29T10:00:00+00:00") == {}


def test_no_device_and_no_samples_report_nothing(conn):
    start = datetime(2026, 8, 29, tzinfo=LOCAL)
    assert electricityapp.saveeye_unrecorded_minutes(conn, start, start + timedelta(days=1), None) == {}
    assert electricityapp.saveeye_unrecorded_minutes(conn, start, start + timedelta(days=1), "TEST-1") == {}


def test_the_gap_rides_along_on_the_row_it_qualifies(conn, set_options):
    """The figure and the caveat on it have to travel together, or the chart
    draws one without the other."""
    before, after, wh = REAL_RESETS[3]
    _seed(conn, before, after, wh)
    middle = datetime.fromisoformat(before).astimezone(LOCAL)
    start = (middle - timedelta(hours=4)).replace(minute=0, second=0, microsecond=0)
    rows = electricityapp.combined_consumption_with_cost(
        conn, start, start + timedelta(hours=10), "MP", "DK2",
        electricityapp._read_options(), saveeye_device_serial="TEST-1")
    flagged = [r for r in rows if r.get("saveeye_unrecorded_min")]
    assert flagged, "no row carried the gap"
    assert all(r["saveeye_kwh"] is not None for r in flagged)
    assert 60 <= sum(r["saveeye_unrecorded_min"] for r in flagged) <= 66


def test_every_row_carries_the_field_even_when_there_is_no_gap(conn, set_options):
    """A key that only sometimes exists is one the page reads as undefined."""
    _seed(conn, "2026-08-29T10:00:00+00:00", "2026-08-29T10:05:00+00:00", 500)
    start = datetime(2026, 8, 29, 8, tzinfo=LOCAL)
    rows = electricityapp.combined_consumption_with_cost(
        conn, start, start + timedelta(hours=8), "MP", "DK2",
        electricityapp._read_options(), saveeye_device_serial="TEST-1")
    assert rows, "the fixture should produce rows"
    assert all("saveeye_unrecorded_min" in r for r in rows)
