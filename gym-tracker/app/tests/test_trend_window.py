"""Which slice of history a trend is fitted through.

A projection four months out is a claim about the *current* trend, but the fit
used everything ever logged — so a starting point from months ago pulled on an
answer about December. With weight, where day-to-day noise is larger than the
weekly signal, which early readings happen to be included can move the
projection by several kilos.

So the fit runs over a trailing window. The tests below are mostly about the
edges of that: sparse data, stale data, and the facts that must NOT be windowed
because they describe the goal rather than the trend.
"""
import datetime

import pytest

import app as gymapp


TODAY = datetime.date.today()


def _pts(*offsets_and_values):
    """(days ago, value) -> [(date, value)], oldest first."""
    return [(TODAY - datetime.timedelta(days=off), v) for off, v in
            sorted(offsets_and_values, key=lambda p: -p[0])]


# --- the window itself --------------------------------------------------------


def test_a_long_series_is_cut_to_the_window():
    points = _pts(*[(d, 100.0) for d in range(0, 120, 7)])  # weekly for ~4 months
    recent = gymapp._recent_points(points)
    span = (recent[-1][0] - recent[0][0]).days
    assert span <= gymapp.TREND_WINDOW_DAYS
    assert len(recent) < len(points)


def test_the_most_recent_reading_is_always_kept():
    points = _pts(*[(d, 100.0) for d in range(0, 120, 7)])
    assert gymapp._recent_points(points)[-1] == points[-1]


def test_a_short_series_is_left_alone():
    """Fewer than four points is too few to be selective about — dropping any
    would make the fit worse rather than more current."""
    points = _pts((30, 100.0), (20, 101.0), (10, 102.0))
    assert gymapp._recent_points(points) == points


def test_a_sparse_series_widens_rather_than_returning_too_few():
    """Monthly weigh-ins: the 28-day window holds one point, which is not a
    line. Take the last four whatever their age instead of refusing to answer."""
    points = _pts((150, 100.0), (120, 101.0), (90, 102.0), (60, 103.0), (30, 104.0))
    recent = gymapp._recent_points(points)
    assert len(recent) == gymapp.TREND_MIN_POINTS
    assert recent == points[-4:]


def test_the_window_is_anchored_on_the_last_reading_not_on_today():
    """Someone who stopped logging a month ago still gets a fit over their last
    month of data, rather than an empty window and no forecast at all."""
    points = _pts((90, 100.0), (83, 101.0), (76, 102.0), (69, 103.0), (62, 104.0))
    recent = gymapp._recent_points(points)
    assert len(recent) >= 2
    assert recent[-1][0] == TODAY - datetime.timedelta(days=62)


# --- what the window changes --------------------------------------------------


def _logs(points):
    return [{"ts": d.isoformat() + "T08:00:00", "weight_kg": v, "body_fat_pct": None}
            for d, v in points]


def _goal():
    return {"target_weight_kg": 105.0, "target_body_fat_pct": None,
            "target_date": (TODAY + datetime.timedelta(days=120)).isoformat(),
            "start_weight_kg": 100.0}


def test_an_old_run_no_longer_drags_on_the_projection():
    """Eight weeks of gaining, then four of holding steady. The honest answer
    about the next four months is the recent flat trend, not an average of the
    two."""
    # Stops at day 35: day 28 belongs to the flat run below, and a duplicate
    # date with two different weights is not a scenario, it is a bug.
    old_gain = [(d, 100.0 + (56 - d) * 0.05) for d in range(84, 34, -7)]
    recent_flat = [(d, 102.8) for d in range(28, -1, -7)]
    points = _pts(*(old_gain + recent_flat))

    fc = gymapp._weight_forecast(_logs(points), _goal())
    assert fc["available"] is True
    assert abs(fc["slope_per_week"]) < 0.1, (
        f"the recent flat run should dominate, got {fc['slope_per_week']} kg/wk"
    )
    assert fc["fit_days"] <= gymapp.TREND_WINDOW_DAYS


def test_the_response_says_what_it_was_fitted_through():
    """So the page can state the basis rather than implying the trend describes
    everything drawn on the chart."""
    points = _pts(*[(d, 100.0 + d * 0.01) for d in range(84, -1, -7)])
    fc = gymapp._weight_forecast(_logs(points), _goal())
    assert fc["fit_days"] <= gymapp.TREND_WINDOW_DAYS
    assert 2 <= fc["fit_points"] <= len(points)


def test_the_drawn_trend_starts_where_the_fit_starts():
    """Drawing the projection back through data it was not fitted on would
    claim the line describes months it never saw."""
    points = _pts(*[(d, 100.0 + d * 0.01) for d in range(84, -1, -7)])
    fc = gymapp._weight_forecast(_logs(points), _goal())
    drawn_from = datetime.date.fromisoformat(fc["trend"][0]["ts"])
    assert (TODAY - drawn_from).days <= gymapp.TREND_WINDOW_DAYS


# --- what must NOT be windowed ------------------------------------------------


def test_the_direction_of_progress_comes_from_the_whole_goal():
    """Which way counts as 'ahead' is a property of the goal. If it were taken
    from the window, a fortnight of noise could reverse the definition and flip
    the badge for reasons that have nothing to do with progress."""
    goal = _goal()
    goal["start_weight_kg"] = None          # force the fallback to the series
    # Started at 96, now hovering near 103 — a bulk, however the last month went.
    points = _pts(*([(120, 96.0)] + [(d, 103.0 + (d % 2) * 0.4) for d in range(56, -1, -7)]))

    fc = gymapp._weight_forecast(_logs(points), goal)
    # Rising toward 105 from 96 is progress; the verdict must not read as
    # off_track merely because the recent window wobbles.
    assert fc["status"] != "off_track"


def test_the_required_rate_still_measures_from_the_latest_reading():
    points = _pts(*[(d, 100.0) for d in range(84, -1, -7)])
    goal = _goal()
    fc = gymapp._weight_forecast(_logs(points), goal)
    days = (datetime.date.fromisoformat(goal["target_date"]) - TODAY).days
    assert fc["required_per_week"] == round((105.0 - 100.0) / (days / 7.0), 2)


# --- body fat -----------------------------------------------------------------


def test_body_fat_uses_the_same_window():
    logs = [{"ts": (TODAY - datetime.timedelta(days=d)).isoformat() + "T08:00:00",
             "weight_kg": 100.0, "body_fat_pct": 30.0 - (84 - d) * 0.05}
            for d in range(84, -1, -7)]
    goal = _goal()
    goal["target_body_fat_pct"] = 15.0
    fc = gymapp._forecast(logs, goal)
    assert fc["bf_available"] is True
    assert fc["bf_fit_days"] <= gymapp.TREND_WINDOW_DAYS


def test_sparse_body_fat_readings_still_produce_a_trend():
    """Body fat is logged far less often than weight, so the sparse fallback
    does most of the work — it must not leave the panel blank."""
    logs = [{"ts": (TODAY - datetime.timedelta(days=d)).isoformat() + "T08:00:00",
             "weight_kg": 100.0, "body_fat_pct": bf}
            for d, bf in [(150, 31.0), (110, 30.0), (70, 29.0), (35, 28.0), (2, 27.6)]]
    goal = _goal()
    goal["target_body_fat_pct"] = 15.0
    fc = gymapp._forecast(logs, goal)
    assert fc["bf_available"] is True
    assert fc["bf_slope_per_week"] < 0


# --- refusing to claim a direction it cannot support --------------------------


def test_a_slope_smaller_than_its_own_error_is_not_a_direction():
    """The user's actual weight series: 3 kg of day-to-day water movement
    against a weekly signal of about 90 g. The fitted line has a sign, but the
    data does not support it — and a badge that reverses next week teaches
    people to ignore the badge."""
    noisy = [(58, 101.3), (53, 100.45), (46, 102.15), (42, 100.4), (35, 102.4),
             (32, 100.7), (28, 99.2), (24, 100.15), (21, 102.1), (18, 101.6),
             (11, 100.7), (0, 100.0)]
    points = _pts(*noisy)
    fc = gymapp._weight_forecast(_logs(points), _goal())
    assert fc["trend_unclear"] is True
    assert fc["status"] == "unclear"


def test_a_clean_trend_is_still_called():
    """The guard must not swallow a real signal — a steady gain with little
    scatter should still get a verdict."""
    points = _pts(*[(d, 100.0 + (84 - d) * 0.04) for d in range(84, -1, -7)])
    fc = gymapp._weight_forecast(_logs(points), _goal())
    assert fc["trend_unclear"] is False
    assert fc["status"] in ("ahead", "on_track", "behind")


def test_two_points_cannot_be_called_noisy():
    """A line through two points has no residuals; the uncertainty is unknown,
    not zero, so the guard must not fire."""
    points = _pts((14, 100.0), (0, 101.0))
    fc = gymapp._weight_forecast(_logs(points), _goal())
    assert fc["trend_unclear"] is False


def test_body_fat_gets_the_same_guard():
    logs = [{"ts": (TODAY - datetime.timedelta(days=d)).isoformat() + "T08:00:00",
             "weight_kg": 100.0, "body_fat_pct": bf}
            for d, bf in [(28, 28.0), (21, 31.0), (14, 27.5), (7, 30.5), (0, 28.5)]]
    goal = _goal()
    goal["target_body_fat_pct"] = 15.0
    fc = gymapp._forecast(logs, goal)
    assert fc["bf_trend_unclear"] is True
    assert fc["bf_status"] == "unclear"


def test_the_page_declines_to_quote_a_projection_it_cannot_support():
    """Naming a number would give a figure about to change sign the authority
    of a printed forecast."""
    import os
    static = os.path.join(os.path.dirname(__file__), "..", "static")
    with open(os.path.join(static, "app.js")) as handle:
        js = handle.read()
    assert "unclear: {" in js, "no badge defined for the unclear status"
    fn = js[js.index("function renderForecast"):]
    fn = fn[:fn.index("\n}")]
    guard = fn[fn.index("trend_unclear"):]
    assert "projected_weight_kg" not in guard.split("return;")[0]
