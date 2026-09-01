"""Dates render dd/mm/yy.

What was here before produced MM/DD, which is not merely a different convention
but an actively misleading one for a Danish household: the 1 September charging
session displayed as "09/01", which reads as 9 January.

There is no browser here, so the formatter is transcribed and exercised against
the values these call sites actually pass, with a test pinning the transcription
to the JavaScript it came from.
"""
import os
import re

import pytest


def _js():
    path = os.path.join(os.path.dirname(__file__), "..", "static", "app.js")
    with open(path, encoding="utf-8") as handle:
        return handle.read()


ISO_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def _fmt_date(iso):
    """`fmtDate`, transcribed."""
    parts = ISO_DAY.match(iso or "")
    return f"{parts[3]}/{parts[2]}/{parts[1][2:]}" if parts else ""


def _short_day(iso):
    """`shortDay`, transcribed."""
    parts = ISO_DAY.match(iso or "")
    return f"{parts[3]}/{parts[2]}" if parts else ""


@pytest.mark.parametrize("iso,expected", [
    ("2026-09-01", "01/09/26"),   # the session that read as 9 January
    ("2026-08-26", "26/08/26"),
    ("2026-01-09", "09/01/26"),   # genuinely 9 January, and now says so
    ("2026-12-31", "31/12/26"),
    ("2026-09-01T09:17:00+02:00", "01/09/26"),  # a full timestamp still works
])
def test_dates_render_day_first(iso, expected):
    assert _fmt_date(iso) == expected


@pytest.mark.parametrize("iso,expected", [
    ("2026-09-01", "01/09"),
    ("2026-08-26", "26/08"),
])
def test_axis_labels_drop_the_year(iso, expected):
    """The range is already stated by the selector directly above the chart,
    and these labels repeat every few pixels."""
    assert _short_day(iso) == expected


@pytest.mark.parametrize("value", ["", None, "not-a-date", "2026", "2026-9-1", "x-y-z"])
def test_a_missing_or_broken_date_is_empty_not_nan(value):
    """These land in a chart axis and a row heading. "NaN/NaN/NaN" across a
    tooltip is worse than nothing at all."""
    assert _fmt_date(value) == ""
    assert _short_day(value) == ""


def test_the_iso_string_is_sliced_never_parsed_as_a_date():
    """These values are calendar days in local time, not instants. `new
    Date("2026-09-01")` is UTC midnight, which in Denmark is 02:00 local — and
    formatting that back through any local getter moves dates in the other
    direction for anyone west of Greenwich. Slicing the string cannot drift."""
    js = _js()
    block = js[js.index("function fmtDate(iso)"):js.index("function renderChargingMonths(")]
    assert "new Date" not in block
    assert "ISO_DAY.exec" in block


def test_every_date_call_site_uses_a_formatter():
    """The bug was a raw `d.day` and a MM/DD slice reaching the screen. Any
    date interpolated without going through one of these two is the same bug
    returning."""
    js = _js()
    raw = re.findall(r"\$\{(?:d|session|r)\.day\}", js)
    assert raw == [], f"date interpolated without a formatter: {raw}"
    assert "d.day.slice(5)" not in js, "the old MM-DD axis slice is back"


def test_the_transcription_matches_the_javascript():
    js = _js()
    block = js[js.index("function fmtDate(iso)"):js.index("function renderChargingMonths(")]
    assert "`${parts[3]}/${parts[2]}/${parts[1].slice(2)}`" in block
    assert "`${parts[3]}/${parts[2]}`" in block
    assert "/^(\\d{4})-(\\d{2})-(\\d{2})/" in js
