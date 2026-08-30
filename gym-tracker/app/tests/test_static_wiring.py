"""The seam between app.js and index.html.

There is no JS test harness in this add-on, so almost nothing about the player
can be tested here. This is the exception, and it is worth having: app.js
attaches listeners at module level, so one `getElementById` returning null
throws during parse and takes the *whole* app down — every card, not just the
feature with the typo. That failure is invisible to pytest and obvious to a user.

Checking the ids agree is a text comparison, which Python can do perfectly well.
"""
import os
import re

STATIC = os.path.join(os.path.dirname(__file__), "..", "static")
TEMPLATES = os.path.join(os.path.dirname(__file__), "..", "templates")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _template_ids():
    html = _read(os.path.join(TEMPLATES, "index.html"))
    return set(re.findall(r'id="([^"]+)"', html))


def _script_ids():
    js = _read(os.path.join(STATIC, "app.js"))
    return set(re.findall(r'getElementById\("([^"]+)"\)', js))


def test_every_element_the_script_reaches_for_exists():
    """A null here throws at parse time and takes every card with it."""
    missing = sorted(_script_ids() - _template_ids())
    assert not missing, f"app.js reaches for ids the template does not define: {missing}"


def test_the_player_markup_is_present():
    """The player is the one screen that is unreachable by any other test."""
    ids = _template_ids()
    for element in (
        "routine-player", "player-name", "player-round", "player-close",
        "player-ready", "player-total", "player-steps", "player-cues", "player-start",
        "player-run", "player-thumb", "player-step", "player-count",
        "player-step-bar", "player-next", "player-overall-bar",
        "player-skip", "player-pause", "player-stop",
    ):
        assert element in ids, f"the player is missing #{element}"


def test_the_player_never_counts_ticks():
    """Elapsed time is derived from the wall clock, so a throttled or suspended
    tab is still correct the instant it comes back. Counting intervals would
    drift by exactly as long as the phone was asleep."""
    js = _read(os.path.join(STATIC, "app.js"))
    assert "playerPausedAt ?? Date.now()) - playerStartedAt" in js
    # performance.now() can stop advancing across an iOS suspend, which is the
    # case this has to survive.
    assert "performance.now()" not in js


def test_the_audio_context_is_created_inside_the_start_press():
    """That press is the user gesture iOS requires to unlock audio, and there is
    no second chance once the routine is running."""
    js = _read(os.path.join(STATIC, "app.js"))
    start = js.index('document.getElementById("player-start").addEventListener')
    body = js[start:start + 900]
    assert "new Ctx()" in body, "the AudioContext is not created in the Start handler"


def test_optional_browser_features_are_guarded():
    """Vibration is absent on iOS and wake lock needs a secure context. Both
    have to degrade to nothing rather than throw."""
    js = _read(os.path.join(STATIC, "app.js"))
    assert 'typeof navigator.vibrate === "function"' in js
    assert '"wakeLock" in navigator' in js


def test_the_wake_lock_is_requested_after_the_timer_starts():
    """requestWakeLock() bails out when no routine is running, so asking for the
    lock before playerTimer is assigned silently never takes it — and the screen
    sleeps mid-routine, which is the one thing the lock exists to prevent."""
    js = _read(os.path.join(STATIC, "app.js"))
    start = js.index('document.getElementById("player-start").addEventListener')
    body = js[start:js.index("function stopPlayerTimer", start)]
    assert "requestWakeLock()" in body, "the Start handler never asks for the wake lock"
    assert body.index("playerTimer = setInterval") < body.index("requestWakeLock()"), (
        "requestWakeLock() runs before playerTimer is set, so its guard rejects it"
    )


def test_the_player_stays_opaque_in_every_state():
    """The player covers the whole screen, and --accent-soft and friends are
    translucent. Setting one as the background on its own lets the app show
    through mid-routine, which reads as two screens drawn on top of each other."""
    css = _read(os.path.join(STATIC, "style.css"))
    for state in ("is-work", "is-rest"):
        rule = re.search(r"\.player\.%s \{(.*?)\}" % state, css, re.S)
        assert rule, f".player.{state} has no rule"
        background = re.search(r"background:([^;]+);", rule.group(1))
        assert background, f".player.{state} sets no background"
        assert "var(--bg)" in background.group(1), (
            f".player.{state} paints a translucent tint without layering it over "
            "var(--bg), so the page behind shows through"
        )


def test_the_stylesheet_keeps_the_toast_above_the_player():
    """A failure toast raised mid-workout must not appear behind the thing that
    caused it."""
    css = _read(os.path.join(STATIC, "style.css"))
    player_z = int(re.search(r"\.player \{[^}]*z-index: (\d+)", css, re.S).group(1))
    toast_z = int(re.search(r"\.toast \{[^}]*z-index: (\d+)", css, re.S).group(1))
    assert toast_z > player_z, f"toast {toast_z} would be hidden behind the player {player_z}"


def test_the_meals_card_is_wired():
    html = _read(os.path.join(TEMPLATES, "index.html"))
    js = _read(os.path.join(STATIC, "app.js"))
    assert "meals-card" in html, "the card itself is missing from index.html"
    # These three are read by app.js; meals-card is only a container.
    for element_id in ("meals-rows", "meals-recorded", "meals-summary"):
        assert element_id in html, f"{element_id} missing from index.html"
        assert element_id in js, f"{element_id} never used by app.js"


def test_the_meal_states_are_visually_distinct():
    """Untouched must not look like either answer. 'Nobody recorded this' is a
    real state here, not a missing value, so it needs its own appearance."""
    css = _read(os.path.join(STATIC, "style.css"))
    for name in ("meal-btn", "meal-on-ate", "meal-on-skipped"):
        assert f".{name}" in css, f"{name} is emitted by app.js but never styled"


def test_the_card_never_shows_a_bare_recorded_count():
    """A bare '2' invites reading the third meal as eaten — the exact
    inference this feature refuses to make."""
    js = _read(os.path.join(STATIC, "app.js"))
    assert "of ${data.expected} recorded" in js
