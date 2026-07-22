"""Smoke tests only: each proves a slice of UI-to-backend wiring works in
a real browser. Behavioral edge cases stay covered by the backend suite.

The tests share one app process (session fixture) and run in order —
test_log_an_egg's entry is what test_entry_appears_in_history reads.
"""
from playwright.sync_api import expect


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


def test_my_flock_opens_with_seeded_breeds(page, app_server):
    page.goto(app_server)
    page.click("#flock-open-btn")
    expect(page.locator("#flock-backdrop")).to_have_class("sheet-backdrop open")
    expect(page.locator("#breed-list")).to_contain_text("Isabrown")
