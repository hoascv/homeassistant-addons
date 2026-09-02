"""The stylesheet's own consistency.

Two of these shipped broken. `.ferment` painted itself with `var(--card, #fff)`
and `.training-photo` with `var(--surface-2, rgba(255,255,255,0.04))`, and
neither token has ever existed in this stylesheet — so both silently rendered
their fallback. The ferment card came out white with the dark theme's near-white
text on it, unreadable, and nothing failed: CSS has no undefined-variable error,
and a fallback is indistinguishable from a working default until you look at it.

Hence a test rather than more care. A var() that always resolves to its fallback
is dead code wearing a theme's clothes.
"""
import os
import re

STYLESHEET = os.path.join(os.path.dirname(__file__), "..", "static", "style.css")


def _css():
    with open(STYLESHEET, encoding="utf-8") as handle:
        return handle.read()


def _strip_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _defined(css):
    return set(re.findall(r"(--[\w-]+)\s*:", css))


def _used(css):
    return set(re.findall(r"var\(\s*(--[\w-]+)", css))


def test_every_custom_property_used_is_defined():
    """Including the ones with a fallback. The fallback is the trap: it makes a
    typo look like a working default, which is exactly how the ferment card
    reached a user as white-on-white."""
    css = _strip_comments(_css())
    missing = sorted(_used(css) - _defined(css))
    assert missing == [], f"used but never defined: {', '.join(missing)}"


def test_the_theme_tokens_are_all_answered_in_dark_mode():
    """A token defined only in :root keeps its light value on a dark
    background. Colours must appear in both blocks; sizes need not."""
    css = _strip_comments(_css())
    root = css[css.index(":root"):css.index("@media (prefers-color-scheme: dark)")]
    dark = css[css.index("@media (prefers-color-scheme: dark)"):]
    dark = dark[:dark.index("\n}\n", dark.index("{"))]

    # Deliberately light-only: geometry, and accents chosen to sit on the
    # coloured action buttons, which are the same colour in both themes.
    theme_free = {"--radius"}
    colours = {
        name for name in _defined(root)
        if name not in theme_free and not name.startswith("--accent")
    }
    unanswered = sorted(colours - _defined(dark))
    assert unanswered == [], f"no dark value for: {', '.join(unanswered)}"


def test_the_ferment_card_uses_the_shared_surface():
    """The specific regression: a card that paints its own background must take
    it from the same token as every other card, or it will not follow the theme."""
    css = _css()
    block = css[css.index(".ferment {"):css.index(".ferment-header")]
    assert "var(--surface)" in block
    assert "var(--text)" in block, "a card that sets a background must set its ink too"


def _rgb(value):
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _relative_luminance(colour):
    def channel(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in colour)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(ink, surface):
    lighter, darker = sorted(
        (_relative_luminance(ink), _relative_luminance(surface)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _palettes():
    css = _strip_comments(_css())
    root = css[css.index(":root"):css.index("@media (prefers-color-scheme: dark)")]
    dark = css[css.index("@media (prefers-color-scheme: dark)"):]
    dark = dark[:dark.index("\n}\n", dark.index("{"))]

    def values(block):
        return dict(re.findall(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{3,6})\s*;", block))

    light = values(root)
    return light, {**light, **values(dark)}


def test_text_colours_are_readable_on_their_own_surface():
    """The bug the user hit measured 1.18:1 — the dark theme's near-white ink
    on the white the card fell back to. Numbers rather than eyes, because a
    stylesheet only shows you the theme you happen to be in."""
    inks = ["--text", "--text-muted", "--warn-ink", "--spent-ink",
            "--danger", "--positive", "--negative"]
    for name, palette in (("light", _palettes()[0]), ("dark", _palettes()[1])):
        surface = _rgb(palette["--surface"])
        for ink in inks:
            ratio = _contrast(_rgb(palette[ink]), surface)
            assert ratio >= 4.5, f"{ink} on --surface in {name}: {ratio:.2f}:1"


# --- the script has to survive being loaded -----------------------------------


def test_no_top_level_call_is_undefined():
    """A statement at the left margin runs during script evaluation, so an
    undefined name there does not break that one feature — it stops the whole
    file. Every figure on the page stays a dash and nothing in the UI says why.

    This shipped: the trips code in Electricity Tracker was written with an
    `el()` helper carried over from another add-on in this repo, where it does
    exist. Nothing caught it, because no test here executes JavaScript.
    """
    import os
    import re

    path = os.path.join(os.path.dirname(__file__), "..", "static", "app.js")
    with open(path, encoding="utf-8") as handle:
        source = handle.read()

    defined = set(re.findall(
        r"^(?:async\s+)?(?:function|const|let|var)\s+([A-Za-z_$][\w$]*)", source, re.M))
    called_at_load = set(re.findall(r"^([a-z][A-Za-z0-9_$]*)\(", source, re.M))
    missing = sorted(called_at_load - defined)
    assert missing == [], f"called at load but never defined: {missing}"
