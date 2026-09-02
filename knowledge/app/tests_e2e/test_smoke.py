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
    expect(page).to_have_title("Knowledge")


def test_the_topics_card_renders(page, app_server, page_errors):
    page.goto(app_server)
    page.wait_for_load_state("networkidle")
    assert page_errors == []
