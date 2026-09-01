"""The seam between app.js and index.html.

No JS runner here, so this is the little that can be checked: app.js attaches
listeners at module level, and one getElementById returning null throws during
parse and takes the whole page down — every card, not just the feature with the
typo. That failure is invisible to pytest and obvious to a user.
"""
import os
import re

APP_DIR = os.path.join(os.path.dirname(__file__), "..")
JS = open(os.path.join(APP_DIR, "static", "app.js"), encoding="utf-8").read()
CSS = open(os.path.join(APP_DIR, "static", "style.css"), encoding="utf-8").read()
HTML = open(os.path.join(APP_DIR, "templates", "index.html"), encoding="utf-8").read()

TEMPLATE_IDS = set(re.findall(r'id="([\w-]+)"', HTML))
JS_CREATED_IDS = set(re.findall(r'id="([\w-]+)"', JS))


def test_every_id_app_js_looks_up_exists_somewhere():
    referenced = set(re.findall(r'el\("([\w-]+)"\)', JS))
    missing = sorted(referenced - TEMPLATE_IDS - JS_CREATED_IDS)
    assert not missing, f"app.js reads ids nothing defines: {missing}"


def test_every_class_the_rendered_rows_use_is_styled():
    """A class emitted with no rule renders as unstyled text, which looks like
    the feature silently not working."""
    emitted = set(re.findall(r'class="([\w-]+)"', JS))
    for name in sorted(emitted):
        assert f".{name}" in CSS, f"app.js emits {name} but nothing styles it"


def test_the_shopping_list_shows_the_danish_name_as_the_item():
    """The whole point: you are standing in a Danish shop. The English name is
    secondary context, not the label."""
    start = JS.index("function renderPlan(")
    block = JS[start:JS.index("function refreshCount(", start)]
    assert "item.name" in block
    assert "shop-en" in block, "the English name should still be shown, quietly"


def test_a_staple_is_dimmed_rather_than_hidden():
    """Hiding it means noticing you are out of salt in the shop, not at home."""
    assert ".staple .shop-name" in CSS
    assert "display: none" not in CSS[CSS.index(".staple .shop-name"):][:120]


def test_the_page_never_interpolates_untrusted_text_unescaped():
    """Recipe names come from a pasted assistant reply and go into innerHTML."""
    for raw in ("${r.name}", "${item.name}", "${recipe.name}", "${e.recipe}"):
        assert raw not in JS, f"{raw} is interpolated without escaping"


def test_copying_works_outside_a_secure_context():
    """navigator.clipboard needs https, and ingress is plain http — so on most
    installs the modern API is simply absent. Without the old execCommand path
    the copy button does nothing on the machine it was built for."""
    assert "execCommand" in JS
    assert "isSecureContext" in JS, "the modern API should still be preferred where it works"


def test_the_copy_fallback_uses_a_selectable_element():
    """display:none cannot be selected, so a hidden textarea would silently
    copy nothing."""
    block = JS[JS.index("async function copyText("):JS.index("el(\"copy-prompt\")")]
    assert "display: none" not in block and 'style.display' not in block
    assert "position" in block and "-1000px" in block
    assert "setSelectionRange" in block, "iOS ignores .select() on a readonly field"


def test_a_failed_copy_leaves_the_prompt_selected():
    """The last resort should leave one keypress to go, not a drag-select of
    six paragraphs on a phone."""
    handler = JS[JS.index('el("copy-prompt").addEventListener'):]
    assert "selectNodeContents" in handler
    assert "details.open = true" in handler
