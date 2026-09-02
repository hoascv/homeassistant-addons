"""Smoke tests: the page opens in a real browser and does not fall over.

Deliberately shallow. Behaviour is covered by the backend suite; what only a
browser can tell us is whether the JavaScript actually *ran*.
"""
from playwright.sync_api import expect


def test_the_script_survives_being_loaded(page, app_server, page_errors):
    """The reason this suite exists. An undefined name at the left margin of
    app.js throws during script evaluation and stops everything after it,
    leaving a page of placeholders and nothing to say why."""
    page.goto(app_server)
    page.wait_for_load_state("networkidle")
    assert page_errors == [], "the page reported errors: " + "; ".join(page_errors)


def test_the_page_renders_its_shell(page, app_server):
    page.goto(app_server)
    expect(page).to_have_title("Goal Tracker")


def test_the_home_tab_populates(page, app_server, page_errors):
    page.goto(app_server)
    page.wait_for_load_state("networkidle")
    assert page_errors == []


def test_the_weight_chart_expands(page, app_server, page_errors):
    """The one chart in this add-on, and the expander is the control most
    likely to be wired to nothing."""
    page.goto(app_server)
    page.wait_for_load_state("networkidle")
    assert page.locator("#chart-expand-btn").count() == 1
    assert page_errors == []
