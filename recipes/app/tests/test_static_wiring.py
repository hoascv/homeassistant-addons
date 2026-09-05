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


def test_a_seeded_recipe_does_not_claim_you_added_it():
    """Its created_at is when the add-on first started — true, and misleading."""
    fn = JS[JS.index("function addedLine("):JS.index("\n// --- tabs")]
    assert '"seed"' in fn
    assert "Shipped with the add-on" in fn


def test_an_untouched_recipe_shows_one_date_not_two():
    fn = JS[JS.index("function addedLine("):JS.index("\n// --- tabs")]
    assert "updated !== added" in fn


def test_every_kind_of_input_the_page_uses_is_styled():
    """An input type missing from the rule falls back to the browser default:
    a white box about twenty characters wide, which on the dark theme reads as
    the field being broken rather than merely unstyled."""
    block = CSS[CSS.index("/* Forms */"):CSS.index(".row {")]
    used = set(re.findall(r'<input type="([\w-]+)"', HTML + JS))
    for kind in sorted(used - {"checkbox", "radio"}):
        assert f'input[type="{kind}"]' in block, f"the page uses {kind} inputs, nothing styles them"


def test_loading_a_reply_empties_the_paste_box():
    """Left there, the next press of Load it re-imports the same batch and the
    only sign is the duplicate nudge afterwards."""
    fn = JS[JS.index("async function sendImport("):JS.index('el("check-import")')]
    assert 'el("import-text").value = ""' in fn


def test_checking_a_reply_leaves_the_paste_box_alone():
    """Checking is what you do before loading — clearing it would throw away
    the very text the report is about."""
    fn = JS[JS.index("async function sendImport("):JS.index('el("check-import")')]
    line = next(l for l in fn.splitlines() if 'el("import-text").value = ""' in l)
    # body.added is the field only the real import returns; the preview has none.
    assert "body.added != null" in line, "the clear should be guarded by body.added"


def test_the_made_it_button_does_not_look_like_the_toggle_beside_it():
    """Two identical pills side by side read as a pair of selections, which is
    how a counter that only goes up gets pressed seven times for one dinner."""
    bar = JS[JS.index('<div class="cook-actions">'):JS.index("cooked-line")]
    made = bar[bar.rindex("<button", 0, bar.index("+ Made it")):]
    assert "btn-count" in made, "it should not be styled as the toggle beside it"
    assert "btn-on" not in made, "btn-on means selected — the wrong thing for a counter"


def test_a_logged_cooking_can_be_taken_back():
    """A count that only goes up is a record you cannot correct."""
    assert "data-uncooked" in JS
    # The second detail-body listener: the first one handles the plan buttons.
    handler = JS[JS.index('const cooked = event.target.closest("[data-cooked]")'):]
    assert '`api/recipes/${id}/cooked`, { method: "DELETE" }' in handler


def test_the_undo_appears_only_once_there_is_something_to_undo():
    line = JS[JS.index('class="muted cooked-line"'):JS.index('class="muted added"')]
    assert "recipe.times_cooked" in line, "undo should be conditional on the count"


def test_both_ways_of_adding_are_behind_the_one_plus_button():
    """A second icon in the topbar for "the other kind of adding" is a menu
    growing out of a button that already means "add"."""
    assert 'data-mode="paste"' in HTML and 'data-mode="write"' in HTML
    assert 'id="mode-paste"' in HTML and 'id="mode-write"' in HTML
    handler = JS[JS.index('el("add-mode").addEventListener'):]
    assert 'el("mode-paste").hidden' in handler[:600]
    assert 'el("mode-write").hidden' in handler[:600]


def test_the_written_form_has_a_field_for_everything_a_recipe_holds():
    """A form that cannot record servings is one you notice the first time you
    use it, and the fields already exist in the schema."""
    for field in ("own-name", "own-category", "own-servings", "own-minutes",
                  "own-protein", "own-kcal", "own-method", "own-notes"):
        assert f'id="{field}"' in HTML, f"the form has no {field}"


def test_both_category_pickers_are_filled_from_one_place():
    """A category added to the options file appearing in one sheet and not the
    other is the kind of difference nobody reports and everybody works around."""
    fn = JS[JS.index("async function loadSummary("):JS.index("\n}\n", JS.index("async function loadSummary("))]
    assert '["prompt-category", "own-category"]' in fn


def test_a_written_recipe_goes_through_the_same_reader_as_a_paste():
    """Otherwise a typed "grams" never merges with a pasted "g" on the list,
    and nothing about the app explains why."""
    app_py = open(os.path.join(APP_DIR, "app.py"), encoding="utf-8").read()
    route = app_py[app_py.index('@app.route("/api/recipes", methods=["POST"])'):]
    assert "importer.normalise(" in route[:1600]
    assert 'source="own"' in route[:1600]


def test_writing_over_an_existing_recipe_has_to_be_asked_for():
    """Replacing on a name match is right for a re-pasted pack. The thing being
    replaced here is something somebody typed and cannot get back."""
    app_py = open(os.path.join(APP_DIR, "app.py"), encoding="utf-8").read()
    route = app_py[app_py.index('@app.route("/api/recipes", methods=["POST"])'):]
    assert "store.find_by_name(" in route[:1600]
    assert "409" in route[:1600]
    handler = JS[JS.index("async function saveOwn("):]
    assert "response.status === 409" in handler[:2000], "the form should offer to replace"


def test_the_amount_is_sent_as_typed_rather_than_converted_twice():
    """The importer already reads 1,5 — see its _number. Converting in the form
    as well would put one rule in two places."""
    fn = JS[JS.index("function ownIngredients(")::]
    assert 'replace(",", ".")' not in fn[:900]


def test_the_ingredient_rows_are_never_emptied_to_nothing():
    """A form you have to rebuild from zero reads as broken."""
    handler = JS[JS.index('el("own-ingredients").addEventListener'):]
    assert "addIngredientRows(1)" in handler[:500]
