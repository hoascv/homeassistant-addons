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
    expect(page).to_have_title("Recipes")


def test_the_seeded_recipes_render(page, app_server, page_errors):
    """Proves the fetch, the render and the seeding all ran — the add-on ships
    with recipes precisely so the page is useful before anyone imports one."""
    page.goto(app_server)
    page.wait_for_load_state("networkidle")
    expect(page.locator(".recipe-row").first).to_be_visible()
    assert page_errors == []


def test_opening_a_recipe_shows_its_ingredients(page, app_server, page_errors):
    page.goto(app_server)
    page.wait_for_load_state("networkidle")
    page.locator(".recipe-row").first.click()
    expect(page.locator("#recipe-backdrop")).to_be_visible()
    expect(page.locator(".ingredients li").first).to_be_visible()
    assert page_errors == []


def test_the_lists_and_rating_controls_work(page, app_server, page_errors):
    """The newest feature, and the one most likely to break the sheet."""
    page.goto(app_server)
    page.wait_for_load_state("networkidle")
    page.locator(".recipe-row").first.click()
    # Scoped to the sheet: the same attribute is on the filter chip above the
    # list, which comes first in the document and is not what we mean here.
    toggle = page.locator('#detail-body [data-status="todo"]')
    toggle.click()
    expect(page.locator('#detail-body [data-status="todo"]')).to_contain_text("To try")
    page.click('[data-rate="4"]')
    expect(page.locator(".star-on")).to_have_count(4)
    assert page_errors == []


def test_the_import_sheet_builds_a_prompt(page, app_server, page_errors):
    page.goto(app_server)
    page.click("#import-btn")
    page.fill("#prompt-keywords", "chicken, broccoli")
    expect(page.locator("#keyword-chips .chip")).to_have_count(2)
    assert page_errors == []
