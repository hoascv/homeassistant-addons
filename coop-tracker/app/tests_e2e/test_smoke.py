"""Smoke tests only: each proves a slice of UI-to-backend wiring works in
a real browser. Behavioral edge cases stay covered by the backend suite.

The tests share one app process (session fixture) and run in order —
test_log_an_egg's entry is what test_entry_appears_in_history reads.
"""
import json
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import expect

FIXTURE_PHOTO = Path(__file__).parent / "fixtures" / "egg_vision_sample.jpg"


def test_page_loads_and_summary_populates(page, app_server):
    page.goto(app_server)
    expect(page).to_have_title("Coop Tracker")
    # "0", not the initial "–" placeholder: proves loadSummary round-tripped
    expect(page.locator("#stat-eggs-today")).to_have_text("0")


def test_log_an_egg_via_the_sheet(page, app_server):
    page.goto(app_server)
    page.click('.action-btn[data-action="egg"]')
    expect(page.locator("#sheet-backdrop")).to_have_class("sheet-backdrop open")
    page.click('#sheet-form button[type="submit"]')
    expect(page.locator("#sheet-backdrop")).not_to_have_class("sheet-backdrop open")
    expect(page.locator("#stat-eggs-today")).to_have_text("1")


def test_entry_appears_in_history(page, app_server):
    page.goto(app_server)
    expect(page.locator("#history-list .history-item").first).to_contain_text(
        "1 egg collected"
    )


def test_trends_tab_renders_chart(page, app_server):
    page.goto(app_server)
    page.click('.tabbar-btn[data-page="page-trends"]')
    expect(page.locator("#trends-chart-wrap svg")).to_be_visible()
    expect(page.locator("#eggs-per-day-chart-wrap svg")).to_be_visible()
    expect(page.locator("#daily-eggs-chart-wrap svg")).to_be_visible()


def test_my_flock_opens_with_seeded_breeds(page, app_server):
    page.goto(app_server)
    page.click("#flock-open-btn")
    expect(page.locator("#flock-backdrop")).to_have_class("sheet-backdrop open")
    expect(page.locator("#breed-list")).to_contain_text("Isabrown")


def test_log_egg_via_photo_smoke(
    page, app_server, app_server_options_path, ingress_headers
):
    # These two calls bypass the browser, so they do not inherit the context's
    # ingress header and have to carry it themselves — without it the app
    # answers 401, exactly as it would to anything off the published port.
    debug = json.loads(
        urllib.request.urlopen(
            urllib.request.Request(f"{app_server}/api/debug", headers=ingress_headers)
        ).read()
    )
    if not debug["opencv_available"] or not debug["sklearn_available"]:
        pytest.skip("opencv/sklearn not installed in this environment")

    with open(app_server_options_path, "w") as f:
        json.dump({"egg_vision_enabled": True}, f)

    # The fixture photo's box interior spans (80,40)-(1120,860) — the
    # exact width_mm doesn't matter for this smoke test (only chip count
    # and the final save are asserted), just that a box exists so
    # analysis doesn't stop at "no_boxes_registered".
    req = urllib.request.Request(
        f"{app_server}/api/nesting-boxes",
        data=json.dumps({"name": "Smoke Test Box", "width_mm": 320}).encode(),
        headers={"Content-Type": "application/json", **ingress_headers},
    )
    urllib.request.urlopen(req)

    page.goto(app_server)  # fresh nav: window.EGG_VISION is set at render time from options
    page.click('.action-btn[data-action="egg"]')
    expect(page.locator("#egg-photo-btn")).to_be_visible()

    page.set_input_files("#egg-photo-input", str(FIXTURE_PHOTO))
    expect(page.locator("#egg-vision-canvas-wrap")).to_be_visible(timeout=5000)
    expect(page.locator(".egg-chip")).to_have_count(3, timeout=5000)

    page.click("#egg-vision-use-btn")
    expect(page.locator("#count-value")).to_have_text("3")

    page.click('#sheet-form button[type="submit"]')
    expect(page.locator("#sheet-backdrop")).not_to_have_class("sheet-backdrop open")
    # not .first: ties with the earlier egg-logging test's entry on ts
    # (datetime-local input has only minute precision) can put either one
    # first in the ORDER BY ts DESC result — either is a correct save.
    expect(page.locator("#history-list")).to_contain_text("3 eggs collected")


def _hit_centre(page, selector, why):
    """The centre of the last hit target on a chart, in viewport coordinates,
    with the chart scrolled into view first.

    `page.mouse` works in viewport coordinates and does not scroll to reach
    anything — unlike `locator.hover()`, which these tests deliberately avoid
    because overlapping hit targets make Playwright refuse a hover whose centre
    a sibling covers.

    Trends has grown a chart at a time, and once the daily chart slid past the
    720px viewport the mouse was being aimed at empty space below the fold:
    `elementFromPoint` there returns null, so no tooltip appeared and no click
    landed. Nothing in the page was broken, and nothing said so either.
    """
    hits = page.locator(selector)
    assert hits.count(), why
    hits.last.scroll_into_view_if_needed()
    page.wait_for_timeout(100)
    box = hits.last.bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def test_hovering_a_chart_shows_a_tooltip(page, app_server, page_errors):
    """The browser's own <title> tooltip waits about a second, is styled by the
    OS and does nothing on a touchscreen, so the charts carry their own. Driven
    by moving a real mouse, because "the markup contains a tooltip div" is the
    kind of assertion that passes while nothing appears on screen.

    Moved by coordinate rather than hovering one circle: the hit targets
    overlap on a dense chart, and Playwright refuses a hover whose centre a
    sibling covers. The handler picks the nearest by x, so that overlap does
    not matter to it.
    """
    page.goto(app_server)
    page.wait_for_load_state("networkidle")
    page.click('.tabbar-btn[data-page="page-trends"]')
    page.wait_for_timeout(800)
    # Aimed at a hit target rather than the middle of the plot: this suite runs
    # against a near-empty database, so the only covered day is today at the
    # far right, and the tooltip correctly declines to appear for a point 44px
    # or more from the cursor.
    x, y = _hit_centre(page, "#daily-eggs-chart-wrap .chart-hit", "no hover targets on the chart")
    page.mouse.move(x, y)
    page.wait_for_timeout(300)

    tip = page.locator(".chart-tip")
    assert tip.is_visible(), "no tooltip appeared"
    assert tip.text_content().strip(), "the tooltip appeared empty"
    assert page_errors == []


def test_the_tooltip_goes_away(page, app_server, page_errors):
    """Left on screen it would sit over the next thing you looked at."""
    page.goto(app_server)
    page.wait_for_load_state("networkidle")
    page.click('.tabbar-btn[data-page="page-trends"]')
    page.wait_for_timeout(800)
    x, y = _hit_centre(page, "#daily-eggs-chart-wrap .chart-hit", "no hover targets on the chart")
    page.mouse.move(x, y)
    page.wait_for_timeout(200)
    # That it appeared at all is asserted here too: without this the test passes
    # just as happily when the tooltip never shows up, which is exactly what it
    # did while the hover was landing off-screen.
    assert page.locator(".chart-tip").is_visible(), "no tooltip to dismiss"
    page.mouse.move(5, 5)
    page.wait_for_timeout(200)
    assert page.locator(".chart-tip").is_hidden()
    assert page_errors == []


def test_clicking_a_chart_point_opens_its_entries(page, app_server, page_errors):
    """The drill-down, driven for real. The figure on this chart is an
    attributed rate, so the answer to "what is this" often names a different
    day from the one clicked — which is exactly why a tooltip was not enough."""
    page.goto(app_server)
    page.wait_for_load_state("networkidle")
    page.click('.tabbar-btn[data-page="page-trends"]')
    page.wait_for_timeout(800)

    x, y = _hit_centre(
        page, "#daily-eggs-chart-wrap .chart-hit[data-day]", "no drillable points on the daily chart"
    )
    page.mouse.click(x, y)
    page.wait_for_timeout(400)

    backdrop = page.locator("#day-backdrop")
    assert "open" in (backdrop.get_attribute("class") or ""), "the sheet did not open"
    expect(page.locator("#day-body")).to_contain_text("Logged on this day")

    page.click("#day-close")
    page.wait_for_timeout(200)
    assert "open" not in (page.locator("#day-backdrop").get_attribute("class") or "")
    assert page_errors == []
