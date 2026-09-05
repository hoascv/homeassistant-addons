"""Every chart carries a labelled y axis.

A line that rises is not information until you know whether it rose by two eggs
or two hundred. Four charts on the Trends tab plotted unlabelled numbers: the
monthly totals, eggs per day by month, eggs per day by day, and the advanced
forecast.

These are structural checks on the JavaScript, not rendering ones — there is no
browser here. They exist because the axis is easy to half-add: draw the labels
but forget to reserve the gutter and they sit on top of the data; reserve the
gutter but forget to offset x and the plot starts underneath them.
"""
import os
import re

import pytest

BUILDERS = [
    ("buildTrendsSvg", "eggs"),
    ("buildEggsPerDaySvg", "eggs/day"),
    ("buildDailyEggsSvg", "eggs/day"),
    ("buildAdvancedForecastSvg", "eggs"),
]


def _static(name):
    sub = "templates" if name.endswith(".html") else "static"
    path = os.path.join(os.path.dirname(__file__), "..", sub, name)
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _body(js, name):
    start = js.index(f"function {name}(")
    return js[start:js.index("\n}\n", start)]


@pytest.mark.parametrize("name,unit", BUILDERS)
def test_every_chart_builds_an_axis(name, unit):
    body = _body(_static("app.js"), name)
    assert f'chartYAxis(maxVal, "{unit}"' in body


@pytest.mark.parametrize("name,unit", BUILDERS)
def test_the_gutter_is_added_beside_the_plot_not_taken_out_of_it(name, unit):
    """An axis that narrowed the chart would redraw the data every time a label
    got a digit wider."""
    body = _body(_static("app.js"), name)
    assert "const width = axis.gutter + plotW;" in body


@pytest.mark.parametrize("name,unit", BUILDERS)
def test_the_plot_starts_after_the_gutter(name, unit):
    """Reserving the space and then not using it puts the first data point
    underneath the labels."""
    body = _body(_static("app.js"), name)
    x_at = body[body.index("const xAt"):][:120]
    assert "axis.gutter +" in x_at, f"{name} does not offset x by the gutter"


@pytest.mark.parametrize("name,unit", BUILDERS)
def test_gridlines_are_drawn_behind_the_data(name, unit):
    """Prepended, not appended: a gridline over the series competes with it."""
    body = _body(_static("app.js"), name)
    assert "axis.render(width)" in body
    rendered = body.index("axis.render(width)")
    first_series = min(
        (body.index(marker) for marker in ("<polyline", "line(", "bandPolygon(")
         if marker in body),
        default=len(body))
    assert rendered < first_series or "let content = axis.render" in body


def test_the_axis_classes_are_styled():
    css = _static("style.css")
    assert ".chart-grid-line" in css
    assert ".chart-axis-value" in css


def test_the_unit_is_stated_once_on_the_top_tick():
    """Rather than a rotated axis title, which costs more width than the labels
    it explains."""
    js = _static("app.js")
    fn = js[js.index("function axisTicks("):js.index("function chartYAxis(")]
    assert "ticks[ticks.length - 1].label += ` ${unit}`" in fn


def test_the_step_never_goes_finer_than_a_tenth():
    """Eggs are counted. The finest thing quoted here is an average like
    3.4/day, and a 0.05 step would label gridlines with numbers they do not
    sit on."""
    js = _static("app.js")
    fn = js[js.index("function axisTicks("):js.index("function chartYAxis(")]
    assert "Math.max(0.1," in fn


# --- the tick algorithm itself ------------------------------------------------


def _ticks(maximum, unit, wanted=3):
    """The JavaScript above, transcribed. Kept in step by
    test_the_transcription_matches_the_javascript below."""
    import math
    rough = maximum / wanted
    magnitude = 10 ** math.floor(math.log10(rough))
    n = rough / magnitude
    step = max(0.1, (1 if n < 1.5 else 2 if n < 3 else 5 if n < 7 else 10) * magnitude)
    decimals = max(0, math.ceil(-math.log10(step)))
    out, seen, value = [], set(), 0.0
    slack = step / 1000
    while value <= maximum + slack:
        label = f"{value:.{decimals}f}"
        if label not in seen:
            seen.add(label)
            out.append(label)
        value += step
    if not out:
        out = ["0"]
    out[-1] += f" {unit}"
    return out


@pytest.mark.parametrize("maximum,unit,expected", [
    # Five hens on a good day, and the same flock through a moult.
    (5.2, "eggs/day", ["0", "2", "4 eggs/day"]),
    (3.4, "eggs/day", ["0", "1", "2", "3 eggs/day"]),
    (0.4, "eggs/day", ["0.0", "0.1", "0.2", "0.3", "0.4 eggs/day"]),
    # Monthly totals, which live three orders of magnitude higher on the same
    # tab — the reason these charts cannot share one axis.
    (148, "eggs", ["0", "50", "100 eggs"]),
    (620, "eggs", ["0", "200", "400", "600 eggs"]),
])
def test_the_ticks_are_numbers_people_read_without_arithmetic(maximum, unit, expected):
    assert _ticks(maximum, unit) == expected


def test_a_flock_that_laid_nothing_still_gets_an_axis():
    """maxVal is floored at 1 by every caller, so this is the degenerate case
    rather than a division by zero."""
    assert _ticks(1, "eggs/day") == ["0.0", "0.5", "1.0 eggs/day"]


def test_the_transcription_matches_the_javascript():
    """The Python above is a copy, and a copy drifts. This pins the three
    constants it depends on to the source it was copied from."""
    js = _static("app.js")
    fn = js[js.index("function axisTicks("):js.index("function chartYAxis(")]
    assert "wanted = 3" in fn
    assert "normalised < 1.5 ? 1 : normalised < 3 ? 2 : normalised < 7 ? 5 : 10" in fn
    assert re.search(r"for \(let v = 0; v <= max \+ slack; v \+= step\)", fn)


# --- expanding ----------------------------------------------------------------


def test_every_chart_has_an_expand_button():
    """Three of the four had none. These are small on a phone, and the daily
    view packs ninety points into a few hundred pixels — precisely the chart
    worth making bigger."""
    import re
    html = _static("index.html")
    wraps = re.findall(r'<div class="trends-chart-wrap"[^>]*id="([\w-]+)"', html)
    # Not a fixed count: the sibling test below says a new chart should need a
    # button and no JavaScript, and a number here would have to be edited every
    # time one arrives — which is how the rule gets bumped instead of kept. The
    # floor only guards against the regex silently matching nothing.
    assert len(wraps) >= 4, f"found {wraps}"
    for wrap in wraps:
        block = html[html.index(f'id="{wrap}"'):]
        block = block[:block.index("</div>")]
        assert "trends-expand-btn" in block, f"{wrap} has no expand button"


def test_expansion_is_delegated_rather_than_wired_per_chart():
    """A fifth chart should need a button and no JavaScript."""
    js = _static("app.js")
    assert 'event.target.closest(".trends-expand-btn")' in js
    assert 'getElementById("trends-expand-btn")' not in js, "a per-id listener is back"


def test_escape_collapses_whichever_chart_is_open():
    js = _static("app.js")
    handler = js[js.index('document.addEventListener("keydown"'):]
    assert ".trends-chart-wrap.is-fullscreen" in handler[:400]


def test_leaving_the_tab_collapses_every_chart():
    """A chart left expanded is position: fixed, so it would float over the
    Home tab with no way back to the button that closed it."""
    js = _static("app.js")
    fn = js[js.index("function setTrendsFullscreen("):]
    fn = fn[:fn.index("\n}\n")]
    assert 'querySelectorAll(".trends-chart-wrap")' in fn
    assert "isFullscreen === false" in fn


def test_the_expand_button_can_position_itself():
    """It is absolutely positioned, which only works because the wrap it sits
    in establishes a containing block."""
    css = _static("style.css")
    wrap = css[css.index(".trends-chart-wrap {"):]
    wrap = wrap[:wrap.index("}")]
    assert "position: relative" in wrap


# --- reading a value off the chart --------------------------------------------


def test_every_chart_has_hover_targets():
    """None of these charts could tell you an exact date or value. The line
    showed a shape and that was all — you could see a spike on roughly the 22nd
    and had no way to ask what it was."""
    js = _static("app.js")
    for name, _unit in BUILDERS:
        start = js.index(f"function {name}(")
        body = js[start:js.index("\n}\n", start)]
        assert "hitTargets(" in body, f"{name} has no hover targets"


def test_the_hit_target_is_hoverable():
    """`fill: none` is not hit-tested by the browser, so the tooltip would
    never appear — the target has to be transparent, not absent."""
    css = _static("style.css")
    block = css[css.index(".chart-hit {"):]
    assert "fill: transparent" in block[:120]


# --- the flock ceiling --------------------------------------------------------


def test_the_eggs_per_day_charts_draw_the_flock_ceiling():
    """A hen lays at most one egg a day, so the flock size is a hard bound.
    Drawn because it turns a number into a proportion: four from five hens
    reads very differently once you can see where five is."""
    js = _static("app.js")
    for name in ("buildDailyEggsSvg", "buildEggsPerDaySvg"):
        start = js.index(f"function {name}(")
        body = js[start:js.index("\n}\n", start)]
        assert "flockCeiling(" in body, f"{name} draws no ceiling"


def test_the_ceiling_is_part_of_the_range():
    """With five hens laying four, an axis that stopped at four would hide how
    much headroom there was."""
    js = _static("app.js")
    for name in ("buildDailyEggsSvg", "buildEggsPerDaySvg"):
        start = js.index(f"function {name}(")
        body = js[start:js.index("\n}\n", start)]
        assert "Math.max(1, birds," in body, f"{name} ignores birds in its range"


def test_the_ceiling_label_avoids_the_expand_button():
    """It sits in the top-right corner of every one of these charts, and a
    right-aligned label near the top of the plot lands underneath it."""
    js = _static("app.js")
    fn = js[js.index("function flockCeiling("):js.index("function monthLabel(")]
    assert "gutter + 3" in fn
    assert 'text-anchor="end"' not in fn


def test_the_forecast_divider_is_offset_by_the_gutter():
    """Missed when the y axis arrived in 1.49.0: the divider was drawn at a raw
    `historyCount * pointSpacing`, one gutter-width left of the boundary it
    marks, so it pointed at the wrong month."""
    js = _static("app.js")
    assert "const dividerX = historyCount * pointSpacing;" not in js
    assert js.count("const dividerX = axis.gutter + historyCount * pointSpacing;") == 2
