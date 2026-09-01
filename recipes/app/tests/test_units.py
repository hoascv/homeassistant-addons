"""Quantity arithmetic.

The place a shopping list goes quietly wrong. Nothing here needs a fixture,
which is the point of keeping the module pure — the arithmetic can be covered
exhaustively rather than sampled.
"""
import pytest

import units


# --- recognising a unit -------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("g", "g"), ("G", "g"), (" kg ", "kg"), ("gram", "g"), ("kilo", "kg"),
    ("dl.", "dl"), ("spiseske", "spsk"), ("stykker", "stk"), ("knivspids", "knsp"),
    (None, ""), ("", ""),
])
def test_units_are_normalised_to_one_spelling(raw, expected):
    assert units.normalise_unit(raw) == expected


@pytest.mark.parametrize("unit,dim", [
    ("g", "mass"), ("kg", "mass"),
    ("ml", "volume"), ("dl", "volume"), ("spsk", "volume"), ("tsk", "volume"),
    ("stk", "count"), ("fed", "count"), ("", "count"),
])
def test_known_units_have_a_dimension(unit, dim):
    assert units.dimension(unit) == dim


def test_an_unknown_unit_has_no_dimension():
    """None means 'not comparable'. Anything else would let it be added to a
    quantity it has nothing to do with."""
    assert units.dimension("nip") is None
    assert units.dimension("æsker") is None


# --- converting ---------------------------------------------------------------


@pytest.mark.parametrize("amount,unit,base", [
    (1, "kg", 1000), (500, "g", 500), (1, "hg", 100),
    (1, "l", 1000), (1, "dl", 100), (1, "cl", 10),
    (1, "spsk", 15), (2, "tsk", 10),
])
def test_amounts_convert_to_their_base(amount, unit, base):
    assert units.to_base(amount, unit)[0] == base


def test_an_unconvertible_unit_keeps_its_amount_and_reports_no_dimension():
    assert units.to_base(3, "nip") == (3, None)


def test_no_amount_stays_no_amount():
    assert units.to_base(None, "g") == (None, "mass")


# --- what merges with what ----------------------------------------------------


def test_the_same_name_in_the_same_dimension_shares_a_key():
    assert units.merge_key("smør", "g") == units.merge_key("Smør", "kg")


def test_different_dimensions_of_one_name_do_not_share_a_key():
    assert units.merge_key("hvidløg", "fed") != units.merge_key("hvidløg", "g")


def test_two_unknown_units_only_merge_with_themselves():
    assert units.merge_key("x", "nip") == units.merge_key("x", "nip")
    assert units.merge_key("x", "nip") != units.merge_key("x", "æsker")


# --- writing it back out ------------------------------------------------------


@pytest.mark.parametrize("base,dim,expected", [
    (500, "mass", (500, "g")),
    (1000, "mass", (1, "kg")),
    (1500, "mass", (1.5, "kg")),
    (50, "volume", (50, "ml")),
    (100, "volume", (1, "dl")),
    (1000, "volume", (1, "l")),
])
def test_quantities_are_shown_in_the_unit_a_person_would_write(base, dim, expected):
    """1200 g is a thing a computer says; 1,2 kg is a thing a list says."""
    assert units.describe(base, dim, "g") == expected


@pytest.mark.parametrize("value,expected", [
    (1.5, 2), (2.0, 2), (0.4, 1), (3.0, 3),
    # Scaling by 6/4 produces this, and three onions would be wrong.
    (2.0000000000000004, 2),
])
def test_things_you_buy_whole_round_up(value, expected):
    """A list asking for 1,5 løg is asking for something the shop will not
    sell. Half an onion spare beats half an onion short."""
    assert units.describe(value, "count", "stk")[0] == expected


def test_vague_counts_are_not_rounded():
    """A knivspids is not a thing you buy, so rounding it to a whole one would
    be inventing a purchase."""
    assert units.describe(0.5, "count", "knsp")[0] == 0.5


def test_a_count_keeps_the_unit_the_recipe_used():
    """'fed' and 'bundt' are not interchangeable and there is nothing to
    convert between."""
    assert units.describe(3, "count", "fed") == (3, "fed")


@pytest.mark.parametrize("value,text", [
    (None, ""), (2.0, "2"), (1.5, "1.5"), (0.26, "0.3"), (750, "750"),
    # 1.04 is within the tolerance that reads as a whole number, and a list
    # saying "1 kg" beats one saying "1.0 kg".
    (1.04, "1"),
])
def test_amounts_are_written_without_a_float_tail(value, text):
    assert units.format_amount(value) == text


# --- scaling ------------------------------------------------------------------


def test_scaling_multiplies_and_stops_at_two_places():
    """133.33333333333331 g is not more accurate than 133.33, only less
    readable, on a quantity nobody weighs to the gram."""
    assert units.scale(100, 4 / 3) == 133.33


def test_scaling_nothing_is_still_nothing():
    assert units.scale(None, 2) is None
