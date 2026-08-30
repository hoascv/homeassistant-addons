"""Every element id app.js reaches for must exist in the template.

The page is plain DOM manipulation with no framework and no build step, so a
renamed or mistyped id fails silently at runtime: getElementById returns null,
the card renders empty, and nothing anywhere says why.
"""
import pathlib
import re

APP_DIR = pathlib.Path(__file__).resolve().parents[1]
JS = (APP_DIR / "static" / "app.js").read_text()
HTML = (APP_DIR / "templates" / "index.html").read_text()
CSS = (APP_DIR / "static" / "style.css").read_text()
PY = (APP_DIR / "app.py").read_text()

TEMPLATE_IDS = set(re.findall(r'id="([\w-]+)"', HTML))


def test_every_id_app_js_looks_up_exists_in_the_template():
    referenced = set(re.findall(r'el\("([\w-]+)"\)', JS)) | set(re.findall(r'getElementById\("([\w-]+)"\)', JS))
    missing = sorted(referenced - TEMPLATE_IDS)
    assert not missing, f"app.js reads ids that the template does not define: {missing}"


def test_the_lock_screen_is_fully_wired():
    """The one screen that has to work before anything else does."""
    for element_id in (
        "lock-card", "lock-setup", "lock-unlock", "lock-error",
        "setup-password", "setup-password-2", "setup-btn",
        "unlock-password", "unlock-btn", "locked-stats",
    ):
        assert element_id in TEMPLATE_IDS, f"{element_id} missing from index.html"
        assert element_id in JS, f"{element_id} never used by app.js"


def test_every_sheet_has_a_close_button():
    sheets = set(re.findall(r'class="sheet-backdrop" id="([\w-]+)"', HTML))
    closable = set(re.findall(r'data-close="([\w-]+)"', HTML))
    assert sheets and sheets == closable, f"sheets with no way out: {sorted(sheets - closable)}"


def test_every_class_the_rendered_rows_use_is_styled():
    """A row rendered with a class nothing styles looks like a layout bug."""
    for class_name in (
        "entry-section", "mood-btn", "chip", "goal-row", "goal-checkin",
        "cal-day", "cal-written", "cal-today", "recall", "recall-body", "section-edit",
    ):
        assert f".{class_name}" in CSS, f"{class_name} has no style rule"
        assert class_name in JS, f"{class_name} is styled but never rendered"


def test_the_mood_scale_is_styled_end_to_end():
    for value in range(1, 6):
        assert f"--mood-{value}" in CSS
        assert f".cal-mood-{value}" in CSS


def test_the_session_header_matches_on_both_sides():
    """The token travels in a header the page sets and the add-on reads. A
    mismatch would lock every request out with a 401 that looks like a wrong
    password."""
    header = re.search(r'SESSION_HEADER = "([\w-]+)"', PY).group(1)
    assert f'"{header}"' in JS


def test_the_page_never_puts_the_token_in_a_cookie():
    """Ingress puts every add-on on one origin. A cookie would be offered to
    the neighbours on every request; a header cannot be."""
    assert "document.cookie" not in JS
    assert "set_cookie" not in PY


def test_the_first_run_screen_states_that_there_is_no_recovery():
    """Someone is about to choose a password that stands between them and
    years of their own writing. The warning is part of the product."""
    setup = re.search(r'<div id="lock-setup".*?</div>\s*<div id="lock-unlock"', HTML, re.DOTALL).group(0)
    assert "forget" in setup.lower()
    assert "no reset" in setup.lower() or "not recoverable" in setup.lower()


def test_the_export_says_it_is_plain_text():
    """The one action here that takes the encryption off. It must not be a
    button labelled merely "Export"."""
    assert "plain text" in HTML.lower() or "plain-text" in HTML.lower()


def test_the_backup_and_restore_controls_are_wired():
    for element_id in ("backup-btn", "restore-btn", "restore-file", "restore-error"):
        assert element_id in TEMPLATE_IDS, f"{element_id} missing from index.html"
        assert element_id in JS, f"{element_id} never used by app.js"


def test_the_backup_says_it_is_still_encrypted():
    """The counterpart to the plain-text warning next to it. Two download
    buttons side by side, one safe to keep and one not — if the page does not
    say which is which, the labels are the only thing telling them apart."""
    backup = re.search(r"<h3>Backup</h3>(.*?)<h3>", HTML, re.DOTALL).group(1)
    assert "encrypted" in backup.lower()


def test_the_restore_warns_that_it_replaces_everything():
    """There is no undo and no second copy on the machine, so the warning has
    to be on the page and not only in the confirm dialog."""
    backup = re.search(r"<h3>Backup</h3>(.*?)<h3>", HTML, re.DOTALL).group(1).lower()
    assert "replaces" in backup
    assert "no undo" in backup or "cannot be undone" in backup


def test_the_restore_confirms_before_destroying_anything():
    """A file picker that acted the moment a file was chosen would destroy a
    journal on a misclick."""
    restore = re.search(r"async function restoreFromFile\(.*?\n}", JS, re.DOTALL).group(0)
    assert "confirm(" in restore
    assert restore.index("confirm(") < restore.index('fetch("api/restore"')


def test_the_page_reloads_after_a_restore():
    """The restore closed every session, this one included; carrying on with a
    token for a vault that no longer exists would fail on the next click."""
    restore = re.search(r"async function restoreFromFile\(.*?\n}", JS, re.DOTALL).group(0)
    assert "location.reload()" in restore

