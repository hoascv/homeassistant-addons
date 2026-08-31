"""The body-fat forecast's status line.

The weight forecast has said "Behind — trending +0.11 kg/wk, need +0.29" for a
while; this is the same statement for body fat. The interesting part is not the
arithmetic but the thresholds: body fat is measured far less precisely than
weight, so a band tuned for kilograms would flip the badge on hydration.
"""
import datetime

import pytest

import app as gymapp


def _logs(points, start="2026-07-03"):
    """points: [(day_offset, weight, bf)] -> log rows as the app stores them."""
    d0 = datetime.date.fromisoformat(start)
    return [
        {"ts": (d0 + datetime.timedelta(days=off)).isoformat() + "T08:00:00",
         "weight_kg": w, "body_fat_pct": bf}
        for off, w, bf in points
    ]


def _goal(target_bf=15.0, target_date=None, target_weight=105.0):
    if target_date is None:
        target_date = (datetime.date.today() + datetime.timedelta(days=120)).isoformat()
    return {
        "target_weight_kg": target_weight,
        "target_body_fat_pct": target_bf,
        "target_date": target_date,
        "start_weight_kg": 100.0,
    }


def _forecast(points, goal=None):
    return gymapp._forecast(_logs(points), goal or _goal())


# --- the shape of the answer --------------------------------------------------


def test_a_falling_trend_short_of_the_target_reads_behind():
    """The user's actual situation: body fat coming down, but not fast enough
    to reach 15 % by the target date."""
    fc = _forecast([(0, 100, 31.0), (28, 100, 29.5), (56, 100, 27.6)])
    assert fc["bf_available"] is True
    assert fc["bf_status"] == "behind"
    assert fc["bf_slope_per_week"] < 0, "body fat is falling"
    assert fc["bf_projected_pct"] > 15.0, "but lands above the target"
    assert fc["bf_required_per_week"] < fc["bf_slope_per_week"], (
        "the required rate must be steeper than the current one"
    )


def test_body_fat_going_up_against_a_lower_target_is_off_track():
    fc = _forecast([(0, 100, 27.0), (28, 100, 29.0), (56, 100, 31.0)])
    assert fc["bf_status"] == "off_track"
    assert fc["bf_slope_per_week"] > 0


def test_a_trend_landing_past_the_target_reads_ahead():
    fc = _forecast([(0, 100, 31.0), (28, 100, 25.0), (56, 100, 19.0)])
    assert fc["bf_status"] == "ahead"
    assert fc["bf_projected_pct"] < 15.0


def test_a_trend_landing_on_the_target_reads_on_track():
    """Within the band either side, which is what 'on track' means."""
    days = (datetime.date.fromisoformat(_goal()["target_date"])
            - datetime.date.today()).days
    # Fall from 30 to exactly 15 over the whole window.
    per_day = 15.0 / (56 + days)
    fc = _forecast([(0, 100, 30.0), (28, 100, 30 - 28 * per_day), (56, 100, 30 - 56 * per_day)])
    assert fc["bf_status"] == "on_track"


# --- the band -----------------------------------------------------------------


def test_the_band_is_wider_than_the_weight_forecasts():
    """Bioimpedance drifts with hydration by more than a few tenths of a point
    across a morning. A 0.3 band would flip the badge on water."""
    source = (gymapp.__file__).replace(".pyc", ".py")
    with open(source) as handle:
        body = handle.read()
    section = body[body.index("def _body_fat_forecast"):body.index("def _weight_forecast")]
    assert "0.5" in section, "the body-fat band should be wider than the weight one"


def test_a_projection_just_inside_the_band_is_not_called_behind():
    """Half a point short of 15 % is not a story worth a warning badge."""
    goal = _goal(target_bf=15.0)
    days = (datetime.date.fromisoformat(goal["target_date"]) - datetime.date.today()).days
    per_day = (30.0 - 15.3) / (56 + days)   # lands at 15.3, i.e. 0.3 short
    fc = _forecast([(0, 100, 30.0), (56, 100, 30 - 56 * per_day)], goal)
    assert fc["bf_status"] == "on_track"


# --- the pieces the line is built from ----------------------------------------


def test_the_required_rate_is_from_the_latest_reading_to_the_target():
    """Not from the fitted line — what has to happen from here, not from where
    a regression thinks you are."""
    goal = _goal(target_bf=15.0)
    fc = _forecast([(0, 100, 31.0), (56, 100, 27.6)], goal)
    days = (datetime.date.fromisoformat(goal["target_date"]) - datetime.date.today()).days
    expected = round((15.0 - 27.6) / (days / 7.0), 2)
    assert fc["bf_required_per_week"] == expected


def test_a_crossing_date_is_given_only_when_the_trend_actually_gets_there():
    ahead = _forecast([(0, 100, 31.0), (28, 100, 25.0), (56, 100, 19.0)])
    assert ahead["bf_projected_date"] is not None

    wrong_way = _forecast([(0, 100, 27.0), (56, 100, 31.0)])
    assert wrong_way["bf_projected_date"] is None


# --- absent or insufficient data ----------------------------------------------


def test_no_body_fat_goal_means_a_trend_but_no_verdict():
    """The trend is still worth drawing; there is simply nothing to be behind."""
    goal = _goal()
    goal["target_body_fat_pct"] = None
    fc = _forecast([(0, 100, 31.0), (56, 100, 27.6)], goal)
    assert fc["bf_available"] is True
    assert fc["bf_status"] is None
    assert fc["bf_required_per_week"] is None


def test_one_reading_is_not_a_trend():
    fc = _forecast([(0, 100, 27.6)])
    assert fc["bf_available"] is False


def test_weight_logs_without_body_fat_leave_the_weight_forecast_intact():
    """They fail independently — body fat is logged less often than weight."""
    fc = gymapp._forecast(_logs([(0, 100, None), (56, 102, None)]), _goal())
    assert fc["available"] is True
    assert fc["bf_available"] is False


def test_no_target_date_means_no_body_fat_forecast_at_all():
    goal = _goal()
    goal["target_date"] = None
    fc = _forecast([(0, 100, 31.0), (56, 100, 27.6)], goal)
    assert fc["bf_available"] is False


# --- the rendered line --------------------------------------------------------


def test_the_page_renders_a_separate_body_fat_forecast_line():
    import os
    static = os.path.join(os.path.dirname(__file__), "..", "static")
    templates = os.path.join(os.path.dirname(__file__), "..", "templates")
    with open(os.path.join(templates, "index.html")) as handle:
        html = handle.read()
    with open(os.path.join(static, "app.js")) as handle:
        js = handle.read()

    for element_id in ("bf-forecast-line", "bf-forecast-badge", "bf-forecast-text"):
        assert element_id in html, f"{element_id} missing from index.html"
        assert element_id in js, f"{element_id} never used by app.js"
    assert "renderBfForecast" in js


def test_the_line_is_hidden_rather_than_nagging_when_there_is_no_bf_goal():
    """Unlike the weight line, which always says something."""
    import os
    static = os.path.join(os.path.dirname(__file__), "..", "static")
    with open(os.path.join(static, "app.js")) as handle:
        js = handle.read()
    fn = js[js.index("function renderBfForecast"):]
    fn = fn[:fn.index("\n}")]
    assert "line.hidden = true" in fn
    assert "target_body_fat_pct == null" in fn
