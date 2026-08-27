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


# --- Expanding a chart ---


def _expand_buttons():
    return re.findall(r'<button[^>]*class="chart-expand-btn"[^>]*>', HTML)


def test_every_chart_has_an_expand_button():
    """Every chart host in the page, and no stragglers: adding a chart without
    one is the omission this catches."""
    hosts = set(re.findall(r'<div id="([\w-]+)" class="chart-host"', HTML))
    expandable = {re.search(r'data-chart="([\w-]+)"', b).group(1) for b in _expand_buttons()}
    assert hosts == expandable, f"charts without an expand button: {sorted(hosts - expandable)}"


def test_each_expand_button_points_at_a_real_chart_host():
    """A typo in data-chart would open an empty modal and say nothing about why."""
    for button in _expand_buttons():
        chart_id = re.search(r'data-chart="([\w-]+)"', button).group(1)
        assert f'id="{chart_id}"' in HTML, f"{chart_id} is not an element in the page"


def test_each_expand_button_carries_a_title_and_a_label():
    """The title becomes the modal heading; the aria-label is what a screen
    reader announces, and three identical "Expand" buttons would be useless."""
    titles = set()
    for button in _expand_buttons():
        title = re.search(r'data-title="([^"]+)"', button)
        assert title, f"expand button without data-title: {button}"
        assert "aria-label" in button, f"expand button without aria-label: {button}"
        titles.add(title.group(1))
    assert len(titles) == len(_expand_buttons()), "expand buttons share a title"


def test_the_expanded_chart_modal_is_wired():
    for element_id in ("chart-backdrop", "chart-modal-host", "chart-modal-title", "chart-modal-close"):
        assert element_id in TEMPLATE_IDS, f"{element_id} missing from index.html"
        assert element_id in JS, f"{element_id} never used by app.js"


def test_the_expanded_render_uses_a_distinct_gradient_id():
    """Both charts are in the document at once, and url(#id) resolves to the
    first match — so reusing the small chart's gradient id is a latent bug."""
    assert "-expanded" in JS
    assert "gradientId" in JS
