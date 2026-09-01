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
    """The invariant, rather than the current list: /api/easee/history clamps
    `days` to 1..365, so a button outside that would silently give a different
    range than its label promises. Asserting the literal set instead just
    breaks whenever a range is added, which is not a bug."""
    toggle = re.search(r'id="charging-range-toggle".*?</div>', HTML, re.DOTALL).group(0)
    offered = [int(d) for d in re.findall(r'data-days="(\d+)"', toggle)]
    assert offered, "no ranges offered at all"
    assert len(set(offered)) == len(offered), "a range is offered twice"
    for days in offered:
        assert 1 <= days <= 365, f"{days}d is outside what the API will honour"


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


# --- The y axis ---


def _chart_calls():
    """The argument text of every renderSmoothChart call, by paren matching —
    the options are nested object literals, so a regex cannot find their end."""
    calls = []
    for start in (m.end() for m in re.finditer(r"(?<!function )renderSmoothChart\(", JS)):
        depth, i = 1, start
        while depth:
            depth += {"(": 1, ")": -1}.get(JS[i], 0)
            i += 1
        calls.append(JS[start:i - 1])
    return calls


def test_every_chart_labels_its_y_axis_with_a_unit():
    """A curve with no unit on it shows the shape and hides the scale: 0.30 and
    3.00 kr/kWh draw the identical picture. The expanded re-render inherits the
    unit along with the rest of the options it spreads."""
    for call in _chart_calls():
        if "...saved.opts" in call:
            continue
        aria = re.search(r'ariaLabel: "([^"]+)"', call)
        assert "yUnit:" in call, f"chart without a y-axis unit: {aria.group(1) if aria else call[:60]}"


def test_the_y_axis_units_are_the_ones_the_rest_of_the_page_quotes():
    units = set(re.findall(r'yUnit: "([^"]+)"', JS))
    assert units == {"kWh", "kr/kWh"}, f"unexpected axis units: {sorted(units)}"


def test_the_gridlines_and_value_labels_are_styled():
    for class_name in ("chart-grid-line", "chart-axis-value"):
        assert f".{class_name}" in CSS, f"{class_name} has no style rule"
        assert class_name in JS, f"{class_name} is styled but never rendered"


def test_the_expanded_render_uses_a_distinct_gradient_id():
    """Both charts are in the document at once, and url(#id) resolves to the
    first match — so reusing the small chart's gradient id is a latent bug."""
    assert "-expanded" in JS
    assert "gradientId" in JS


# --- Insights tab ---


def test_the_insights_tab_and_its_panel_both_exist():
    assert 'data-tab="insights"' in HTML
    assert "tab-insights" in TEMPLATE_IDS
    assert "tab-dashboard" in TEMPLATE_IDS


def test_the_difference_is_worded_rather_than_signed():
    """A bare "−1.2%" next to a "beating flat" verdict reads just as easily as
    having done 1.2% worse."""
    assert "cheaper" in JS and "dearer" in JS


def test_insights_charts_are_expandable_like_every_other():
    for chart_id in ("insights-profile-chart", "insights-price-chart"):
        assert f'data-chart="{chart_id}"' in HTML


def test_the_app_version_matches_config():
    """APP_VERSION is the cache-buster on static/app.js?v= and style.css?v=, so
    a stale one does more than misreport a number: every browser that has been
    here keeps serving the JS it cached under that URL, and new front-end work
    silently never arrives.

    It had drifted to 1.12.2 while config.yaml said 1.15.1 — four releases of
    JS changes, including the monthly charging table, all published at the same
    URL. Hence a test rather than a habit."""
    import os
    import re

    here = os.path.dirname(__file__)
    with open(os.path.join(here, "..", "app.py"), encoding="utf-8") as handle:
        app_version = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', handle.read(), re.M).group(1)
    with open(os.path.join(here, "..", "..", "config.yaml"), encoding="utf-8") as handle:
        config_version = re.search(r'^version:\s*"([^"]+)"', handle.read(), re.M).group(1)
    assert app_version == config_version, (
        f'APP_VERSION is "{app_version}" but config.yaml says "{config_version}" — '
        "browsers will keep the JS they cached under the old ?v="
    )


def test_the_static_assets_are_cache_busted():
    """The other half of the same guarantee: a version that is kept in sync but
    not actually on the URL busts nothing."""
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "templates", "index.html")
    with open(path, encoding="utf-8") as handle:
        html = handle.read()
    assert "static/app.js?v={{ app_version }}" in html
    assert "static/style.css?v={{ app_version }}" in html


def _read_static(name):
    import os
    sub = "templates" if name.endswith(".html") else "static"
    path = os.path.join(os.path.dirname(__file__), "..", sub, name)
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def test_a_recovered_session_is_marked_on_the_row():
    """Its energy is Easee's and its cost is an assumption. A row that looks
    identical to a measured one invites the comparison it cannot support."""
    js = _read_static("app.js")
    assert "session.cost_is_estimated" in js
    assert "pill-quiet" in js
    assert "pill-quiet" in _read_static("style.css")


def test_a_plug_in_span_says_so_next_to_the_duration():
    """Cable-in to cable-out is not a charging window, and 12 h sitting beside
    a 1 h 50 m row reads as a twelve-hour charge unless it is labelled."""
    js = _read_static("app.js")
    assert "span_is_plugged_in" in js
    assert "plugged in" in js


def test_every_var_in_the_stylesheet_resolves():
    """A var() falling through to its fallback is indistinguishable from a
    working default until somebody looks at the page. Coop Tracker shipped a
    card that was white-on-white for exactly this reason."""
    import re
    css = re.sub(r"/\*.*?\*/", "", _read_static("style.css"), flags=re.S)
    defined = set(re.findall(r"(--[\w-]+)\s*:", css))
    used = set(re.findall(r"var\(\s*(--[\w-]+)", css))
    assert sorted(used - defined) == []
