"""Rates that no flock could have produced.

A hen lays at most one egg a day, so a figure above the flock size is not a
bumper harvest — it is the spreading rule's assumption breaking. Each
collection is credited to the days since the previous one, which assumes every
visit empties the nest. Eggs missed on one visit and found on the next get
credited entirely to that shorter gap.

Reported, never corrected. The number is what the collections say, and the
thing that is wrong is a fact about the collecting rather than about the
arithmetic — so it is the keeper who needs telling.
"""
import pytest

import app as coop


@pytest.mark.parametrize("values,birds,expected", [
    ([4.0, 5.0, 3.0], 5, []),
    ([4.0, 6.0, 3.0], 5, [1]),
    ([7.0, 6.0], 5, [0, 1]),
    # Exactly at the ceiling is possible: every hen laid.
    ([5.0], 5, []),
])
def test_only_rates_above_the_flock_are_flagged(values, birds, expected):
    assert coop.impossible_days(values, birds) == expected


def test_gaps_are_not_flagged():
    """None is a day no collection speaks for, not a day of impossible laying."""
    assert coop.impossible_days([None, 4.0, None], 5) == []


def test_a_hair_over_is_not_flagged():
    """Floating point from dividing a basket across days. 5.0000001 from five
    hens is five hens, and ringing it would teach the keeper to ignore rings."""
    assert coop.impossible_days([5.0000001], 5) == []
    assert coop.impossible_days([5.01], 5) == [0]


@pytest.mark.parametrize("birds", [0, None])
def test_no_flock_configured_means_no_ceiling(birds):
    """Nothing is known about the bound, so nothing is claimed about it. A
    keeper who has not filled in their flock should not get every day ringed."""
    assert coop.impossible_days([9.0, 12.0], birds) == []


def test_the_trends_endpoint_reports_the_flock_and_the_flags(client, set_options):
    set_options(flock_isabrown_count=3, flock_sussex_count=2)
    body = client.get("/api/trends").get_json()
    assert body["birds"] == 5
    assert body["impossible"] == []


def test_the_daily_endpoint_reports_them_too(client, set_options):
    set_options(flock_isabrown_count=5, flock_sussex_count=0)
    body = client.get("/api/trends/daily?days=30").get_json()
    assert body["birds"] == 5
    assert "impossible" in body


def test_a_real_over_collection_is_flagged_end_to_end(client, set_options, conn):
    """Two collections a day apart, the second of six from five hens — the
    shape that actually produces this, and the one the user hit."""
    from datetime import datetime, timedelta
    set_options(flock_isabrown_count=5, flock_sussex_count=0)
    today = datetime.now().replace(hour=17, minute=0, second=0, microsecond=0)
    for offset, count in ((2, 4), (1, 6)):
        conn.execute("INSERT INTO logs (type, count, ts) VALUES ('egg', ?, ?)",
                     (count, (today - timedelta(days=offset)).isoformat(timespec="seconds")))
    conn.commit()

    body = client.get("/api/trends/daily?days=30").get_json()
    rates = body["eggs_per_day"]
    assert body["impossible"], "six eggs from five hens in one day was not flagged"
    assert all(rates[i] > 5 for i in body["impossible"])


def test_the_chart_rings_them_rather_than_hiding_them():
    """Clamping to the ceiling would be inventing a number, and dropping the
    point would hide that anything was wrong at all."""
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "static", "app.js")
    with open(path, encoding="utf-8") as handle:
        js = handle.read()
    assert "egg-impossible" in js
    assert "data.impossible" in js
