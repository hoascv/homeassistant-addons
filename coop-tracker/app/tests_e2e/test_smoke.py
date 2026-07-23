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


def test_my_flock_opens_with_seeded_breeds(page, app_server):
    page.goto(app_server)
    page.click("#flock-open-btn")
    expect(page.locator("#flock-backdrop")).to_have_class("sheet-backdrop open")
    expect(page.locator("#breed-list")).to_contain_text("Isabrown")


def test_log_egg_via_photo_smoke(page, app_server, app_server_options_path):
    debug = json.loads(urllib.request.urlopen(f"{app_server}/api/debug").read())
    if not debug["opencv_available"]:
        pytest.skip("opencv not installed in this environment")

    with open(app_server_options_path, "w") as f:
        json.dump({"egg_vision_enabled": True, "egg_vision_coin_diameter_mm": 24.5}, f)

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
