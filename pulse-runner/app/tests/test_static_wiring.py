"""The seam between app.js and index.html — see gym-tracker's test of the
same name for why this matters: app.js attaches listeners at module level,
so one `getElementById` returning null throws during parse and takes the
whole script down, not just one feature. That failure is invisible to
pytest and obvious to a user, so it's checked here as a plain text diff.
"""
import os
import re

STATIC = os.path.join(os.path.dirname(__file__), "..", "static")
TEMPLATES = os.path.join(os.path.dirname(__file__), "..", "templates")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _js():
    return _read(os.path.join(STATIC, "app.js"))


def _template_ids():
    html = _read(os.path.join(TEMPLATES, "index.html"))
    return set(re.findall(r'id="([^"]+)"', html))


def _script_ids():
    return set(re.findall(r'getElementById\("([^"]+)"\)', _js()))


def test_every_element_the_script_reaches_for_exists():
    missing = sorted(_script_ids() - _template_ids())
    assert not missing, f"app.js reaches for ids the template does not define: {missing}"


def test_audio_context_is_created_only_inside_a_user_gesture():
    """iOS requires the AudioContext to be constructed inside the handler for
    a real user gesture, with no second chance later. `ensureAudioUnlocked`
    is that construction site; it must only ever run from `onPress`, which is
    wired directly to the canvas's pointerdown/keydown listeners."""
    js = _js()
    start = js.index("function ensureAudioUnlocked")
    body = js[start:start + 400]
    assert "new Ctx()" in body

    press_start = js.index("function onPress()")
    press_body = js[press_start:press_start + 400]
    assert "ensureAudioUnlocked()" in press_body

    assert 'addEventListener("pointerdown"' in js
    assert "onPress()" in js[js.index('addEventListener("pointerdown"'):js.index('addEventListener("pointerdown"') + 200]


def test_physics_loop_is_fixed_timestep_not_naive_frame_delta():
    """A precision platformer's jump arcs must be identical regardless of the
    display's refresh rate. Locks the accumulator design in so a future edit
    can't quietly replace it with `vy * (now - last)` per-frame integration,
    which would make jump height framerate-dependent."""
    js = _js()
    assert "FIXED_DT" in js
    assert "accumulator" in js
    assert "performance.now()" not in js  # rAF's own timestamp is used instead


def test_reduced_motion_gates_the_death_burst():
    js = _js()
    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in js
    spawn_start = js.index("function spawnDeathBurst")
    spawn_body = js[spawn_start:spawn_start + 200]
    assert "reducedMotion.matches" in spawn_body


def test_visibilitychange_pauses_rather_than_catches_up():
    """The game loop's stated policy: a background gap pauses the run instead
    of fast-forwarding physics through missed time."""
    js = _js()
    assert "visibilitychange" in js
    handler_start = js.index('addEventListener("visibilitychange"')
    handler_body = js[handler_start:handler_start + 400]
    assert "stopFrameLoop()" in handler_body
