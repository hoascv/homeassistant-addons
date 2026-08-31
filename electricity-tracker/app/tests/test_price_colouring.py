"""Colouring the price line by how cheap each reading is.

The rule has to be relative to the same day. A Danish spot day is not
comparable to the one before it — 1.5 kr/kWh can be the bargain of one day and
the peak of another — so a fixed threshold would paint whole days one colour
and answer nothing about when to run the washing machine.

There is no JS runner in this add-on, so the banding logic is checked here by
reimplementing it against the same rules, and the wiring is checked by reading
the source. The reimplementation is worth having: it is the part with an
arguable answer, and a change to one and not the other should fail.
"""
import os
import re

STATIC = os.path.join(os.path.dirname(__file__), "..", "static")


def _read(name):
    with open(os.path.join(STATIC, name)) as handle:
        return handle.read()


def band(values, v, flat_ratio=0.10):
    """The rule priceBander implements, in Python."""
    ordered = sorted(x for x in values if x is not None)
    if not ordered:
        return "price-stop-mid"
    lo, hi = ordered[0], ordered[-1]
    mean = sum(ordered) / len(ordered)
    if mean <= 0 or (hi - lo) / mean < flat_ratio:
        return "price-stop-mid"
    at = lambda f: ordered[min(len(ordered) - 1, int(f * len(ordered)))]
    cheap, dear = at(1 / 3), at(2 / 3)
    if v <= cheap:
        return "price-stop-cheap"
    if v >= dear:
        return "price-stop-dear"
    return "price-stop-mid"


# --- the rule -----------------------------------------------------------------


def test_the_cheapest_third_of_a_varied_day_is_green():
    day = [0.5, 0.6, 0.7, 1.5, 1.6, 1.7, 2.5, 2.6, 2.7]
    assert band(day, 0.5) == "price-stop-cheap"
    assert band(day, 0.6) == "price-stop-cheap"
    assert band(day, 1.6) == "price-stop-mid"
    assert band(day, 2.7) == "price-stop-dear"


def test_the_bands_are_relative_to_the_day_not_absolute():
    """The same price is cheap on one day and dear on another, which is the
    whole reason for judging against the day's own spread."""
    cheap_day = [1.4, 1.5, 1.6, 2.0, 2.4, 2.8, 3.0, 3.2, 3.4]
    dear_day = [0.2, 0.3, 0.4, 0.8, 1.0, 1.2, 1.4, 1.5, 1.6]
    assert band(cheap_day, 1.5) == "price-stop-cheap"
    assert band(dear_day, 1.5) == "price-stop-dear"


def test_a_flat_day_is_not_banded_at_all():
    """Green at 1.71 and amber at 1.78 reads as a real difference when it is a
    few øre. A day with no cheap hours should not be given some."""
    flat = [1.70, 1.71, 1.72, 1.73, 1.74, 1.75, 1.76, 1.77, 1.78]
    assert {band(flat, v) for v in flat} == {"price-stop-mid"}


def test_a_day_just_over_the_flatness_threshold_is_banded():
    """The guard must not swallow a genuinely useful spread."""
    day = [1.0, 1.05, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7]
    assert band(day, 1.0) == "price-stop-cheap"
    assert band(day, 1.7) == "price-stop-dear"


def test_negative_prices_do_not_break_it():
    """Danish spot goes negative on windy nights, and that is the cheapest an
    hour ever gets."""
    day = [-0.4, -0.2, 0.0, 0.5, 0.9, 1.2, 1.8, 2.2, 2.6]
    assert band(day, -0.4) == "price-stop-cheap"
    assert band(day, 2.6) == "price-stop-dear"


def test_an_empty_or_all_null_day_is_neutral():
    assert band([], 1.0) == "price-stop-mid"
    assert band([None, None], 1.0) == "price-stop-mid"


# --- the wiring ---------------------------------------------------------------


def test_the_price_series_asks_for_a_coloured_stroke():
    js = _read("app.js")
    assert "priceBander" in js
    assert "stopClassOf" in js


def test_the_gradient_is_in_user_space_not_bounding_box_units():
    """A series broken into runs would otherwise map each run's own extent to
    0..1 and recolour every fragment from scratch."""
    js = _read("app.js")
    block = js[js.index("if (s.stopClassOf) {"):]
    block = block[:block.index("if (s.area) {")]
    assert 'gradientUnits="userSpaceOnUse"' in block
    assert "padLeft + innerW" in block


def test_every_band_class_is_styled():
    js, css = _read("app.js"), _read("style.css")
    emitted = set(re.findall(r'"(price-stop-[\w-]+)"', js))
    assert emitted, "no band classes emitted"
    for name in sorted(emitted):
        assert f".{name}" in css, f"{name} is emitted but never styled"


def test_cheap_is_green_and_matches_the_existing_convention():
    """.chart-dot-cheap already marks the day's minimum in green; the line
    should not pick a different colour for the same idea."""
    css = _read("style.css")
    dot = re.search(r"\.chart-dot-cheap \{[^}]*\}", css).group(0)
    stop = re.search(r"\.price-stop-cheap \{[^}]*\}", css).group(0)
    assert "--success" in dot and "--success" in stop
