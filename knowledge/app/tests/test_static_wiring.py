"""The page is plain DOM manipulation with no framework and no build step, so a
renamed id or an unstyled class fails silently at runtime: getElementById
returns null, the section renders empty, and nothing anywhere says why.

The other add-ons here have had a file like this for a while; Knowledge did not,
and the markdown briefings are exactly the kind of change it exists to guard —
a renderer that emits a class the stylesheet has never heard of looks, from the
outside, identical to the feature not working at all.
"""
import pathlib
import re

APP_DIR = pathlib.Path(__file__).resolve().parents[1]
JS = (APP_DIR / "static" / "app.js").read_text()
CSS = (APP_DIR / "static" / "style.css").read_text()
HTML = (APP_DIR / "templates" / "index.html").read_text()

TEMPLATE_IDS = set(re.findall(r'id="([\w-]+)"', HTML))


# Some elements are built by app.js itself and injected with innerHTML — the
# flashcard's "Show answer" button is created and then looked up two lines
# later — so a lookup is legitimate if the id appears in either place.
JS_CREATED_IDS = set(re.findall(r'id="([\w-]+)"', JS))


def test_every_id_app_js_looks_up_exists_somewhere():
    referenced = (set(re.findall(r'el\("([\w-]+)"\)', JS))
                  | set(re.findall(r'getElementById\("([\w-]+)"\)', JS)))
    missing = sorted(referenced - TEMPLATE_IDS - JS_CREATED_IDS)
    assert not missing, (
        f"app.js reads ids that neither the template nor app.js itself defines: {missing}"
    )


# --- rendered briefings -------------------------------------------------------


def test_the_briefing_uses_the_server_rendered_html():
    """The renderer lives in Python so it can be unit-tested against injection.
    The page has to actually use its output rather than re-escaping the raw
    text, or the whole exercise is decorative."""
    assert "s.briefing_html" in JS
    assert "escapeHtml(s.briefing)" in JS, "the plain-text fallback should survive"


def test_rendered_markdown_is_not_left_in_a_pre_wrap_container():
    """.briefing sets white-space: pre-wrap for the plain-text fallback. Left on
    once the content is real markup, every newline between the emitted tags
    shows up as a blank line."""
    assert re.search(r"\.md\s*\{[^}]*white-space:\s*normal", CSS)


def test_code_blocks_are_monospace_and_scroll():
    """The whole point of the feature: a box diagram only lines up in a
    monospace font, and a wide one must not push the page sideways on a phone."""
    block = re.search(r"\.md \.md-code \{[^}]*\}", CSS)
    assert block, "no .md-code rule in the stylesheet"
    assert "overflow-x: auto" in block.group(0)
    assert "white-space: pre" in block.group(0)

    code = re.search(r"\.md \.md-code code \{[^}]*\}", CSS)
    assert code and "monospace" in code.group(0)


def test_every_class_the_renderer_emits_is_styled():
    """A class emitted by markdown.py with no rule renders as unstyled text,
    which from the outside looks exactly like the feature not working."""
    renderer = (APP_DIR / "markdown.py").read_text()
    emitted = set(re.findall(r'class="(md-[\w-]+)"', renderer))
    assert emitted, "no md- classes found in markdown.py"
    for name in sorted(emitted):
        assert f".{name}" in CSS, f"markdown.py emits {name} but nothing styles it"


def test_the_page_never_renders_a_raw_briefing_with_innerhtml():
    """s.briefing is the unrendered source. Interpolating it directly would put
    whatever an assistant pasted into the DOM as markup — the exact thing
    markdown.py's escape-first ordering exists to prevent."""
    assert "${s.briefing}" not in JS
    assert "${s.practical_task}" not in JS
