"""Smoke tests: the page opens in a real browser and does not fall over.

Deliberately shallow. Behaviour is covered by the backend suite; what only a
browser can tell us is whether the JavaScript actually *ran*, which is the
thing that shipped broken and that nothing else here can see.
"""
from playwright.sync_api import expect


def test_the_script_survives_being_loaded(page, app_server, page_errors):
    """The test this whole suite was added for.

    1.18.0 called an `el()` helper that does not exist in this file. The call
    sits at the left margin, so it ran during script evaluation, threw, and
    stopped everything after it — including init(). Every figure on the page
    stayed a dash and the charging cards never appeared. Fifteen substring
    assertions over app.js saw nothing wrong, because a string being present
    says nothing about whether it runs.
    """
    page.goto(app_server)
    page.wait_for_load_state("networkidle")
    assert page_errors == [], "the page reported errors: " + "; ".join(page_errors)


def test_the_page_renders_its_shell(page, app_server):
    page.goto(app_server)
    expect(page).to_have_title("Electricity Tracker")
    expect(page.locator("#price-chart")).to_be_attached()


def test_init_runs_all_the_way_through(page, app_server, page_errors):
    """Every figure starts as a dash and some legitimately stay that way with
    no price data. What must not happen is init() dying part-way, which is
    indistinguishable from that on screen and not indistinguishable here."""
    page.goto(app_server)
    page.wait_for_load_state("networkidle")
    assert page_errors == []
    expect(page.locator("#trips")).to_be_attached()


def test_every_dashboard_chart_has_a_working_expander(page, app_server, page_errors):
    """Asserted in the browser rather than by reading the markup: a button that
    is present and wired to nothing passes a substring check and does nothing
    on a phone."""
    page.goto(app_server)
    page.wait_for_load_state("networkidle")
    assert page.locator(".chart-expand-btn").count() >= 3
    assert page_errors == []


def test_the_insights_tab_opens(page, app_server, page_errors):
    page.goto(app_server)
    page.wait_for_load_state("networkidle")
    page.click('[data-tab="insights"]')
    page.wait_for_timeout(500)
    assert page_errors == []


def test_logging_a_trip_round_trips(page, app_server, page_errors):
    """The feature whose broken helper caused all this, exercised end to end so
    the same class of mistake cannot pass again."""
    page.goto(app_server)
    page.wait_for_load_state("networkidle")
    page.locator("#trips summary").click()
    page.fill("#trip-from", "2026-09-01")
    page.fill("#trip-label", "Aarhus and back")
    page.fill("#trip-km", "480")
    page.click("#trip-add")
    expect(page.locator("#trip-list")).to_contain_text("Aarhus and back")
    assert page_errors == []


def _chart_centre(page, selector, why):
    """The centre of a chart, in viewport coordinates, scrolled into view first.

    `page.mouse` works in viewport coordinates and does not scroll to reach
    anything — unlike `locator.hover()`, which these tests avoid on purpose:
    the hit targets overlap on a dense chart and Playwright refuses a hover
    whose centre a sibling covers.

    Coop Tracker had precisely this go wrong. Its Trends page grew a chart, the
    one being hovered slid past the 720px viewport, and the mouse spent two
    releases aimed at empty space below the fold — where `elementFromPoint` is
    null, so no tooltip appeared, no click landed, and no test could say why.
    Scrolling first is what stops the same thing happening here the next time
    this page gets longer.
    """
    chart = page.locator(selector).first
    chart.scroll_into_view_if_needed()
    page.wait_for_timeout(100)
    box = chart.bounding_box()
    assert box, why
    # Clamped into the viewport: a chart taller than the window is scrolled
    # into view without its centre necessarily being on screen, and a point
    # outside the window is exactly the failure this helper exists to prevent.
    viewport = page.viewport_size
    x = min(max(box["x"] + box["width"] * 0.5, 1), viewport["width"] - 1)
    y = min(max(box["y"] + box["height"] * 0.5, 1), viewport["height"] - 1)
    return x, y


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
    
    x, y = _chart_centre(page, "#price-chart svg", "no chart rendered to hover")
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
    
    x, y = _chart_centre(page, "#price-chart svg", "no chart rendered to hover")
    page.mouse.move(x, y)
    page.wait_for_timeout(200)
    # That it appeared at all is asserted here too: without it this test passes
    # just as happily when the tooltip never shows up, which is what Coop
    # Tracker's copy of it did through two releases of a broken hover.
    assert page.locator(".chart-tip").is_visible(), "no tooltip to dismiss"
    page.mouse.move(5, 5)
    page.wait_for_timeout(200)
    assert page.locator(".chart-tip").is_hidden()
    assert page_errors == []
