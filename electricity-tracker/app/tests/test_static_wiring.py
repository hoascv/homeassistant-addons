"""Every element id app.js reaches for must exist in the template.

The dashboard is plain DOM manipulation with no framework and no build step, so
a renamed or mistyped id fails silently at runtime: getElementById returns null,
the card renders empty, and nothing anywhere says why. This is the cheapest
possible guard against that, and it grows automatically with the page.
"""
import pathlib
import re

APP_DIR = pathlib.Path(__file__).resolve().parents[1]
JS = (APP_DIR / "static" / "app.js").read_text()
HTML = (APP_DIR / "templates" / "index.html").read_text()
CSS = (APP_DIR / "static" / "style.css").read_text()

TEMPLATE_IDS = set(re.findall(r'id="([\w-]+)"', HTML))


def _referenced_ids():
    """Ids app.js looks up by literal string. Template-literal lookups are
    skipped: they are computed at runtime and cannot be checked statically."""
    return set(re.findall(r'getElementById\("([\w-]+)"\)', JS))


def test_every_id_app_js_looks_up_exists_in_the_template():
    missing = sorted(_referenced_ids() - TEMPLATE_IDS)
    assert not missing, f"app.js reads ids that the template does not define: {missing}"


def test_the_charging_history_card_is_fully_wired():
    """The newest card, and the one with the most ids to get wrong."""
    for element_id in (
        "charging-history-card", "charging-range-toggle", "charging-chart",
        "charging-sessions", "charging-note", "charging-empty",
        "ch-sessions", "ch-kwh", "ch-cost", "ch-avg",
    ):
        assert element_id in TEMPLATE_IDS, f"{element_id} missing from index.html"
        assert element_id in JS, f"{element_id} never used by app.js"


def test_the_range_toggle_offers_the_ranges_the_api_accepts():
    toggle = re.search(r'id="charging-range-toggle".*?</div>', HTML, re.DOTALL).group(0)
    assert sorted(re.findall(r'data-days="(\d+)"', toggle)) == ["30", "7", "90"]


def test_every_class_the_history_rows_use_is_styled():
    """A row rendered with a class nothing styles looks like a layout bug."""
    for class_name in ("session-row", "session-when", "session-sub", "session-figure", "session-partial"):
        assert f".{class_name}" in CSS, f"{class_name} has no style rule"
        assert class_name in JS, f"{class_name} is styled but never rendered"


def test_the_easee_card_still_carries_the_notes_the_fixes_added():
    for element_id in ("easee-status-pill", "easee-started", "easee-note"):
        assert element_id in TEMPLATE_IDS
        assert element_id in JS
