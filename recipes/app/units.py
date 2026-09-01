"""Quantities: parsing them, adding them up, and writing them back out.

Pure functions over plain values — no database, no Flask, no configuration.
That is deliberate: quantity arithmetic is where a shopping list is most likely
to be quietly wrong, and it should be testable in microseconds without a
fixture standing anything up.

The unit tables below are data rather than branches, so supporting a new unit is
an entry, not an edit to the logic. Everything a recipe can plausibly say is
either convertible to a canonical base (mass, volume) or stands alone (stk, fed,
bundt) — and the ones that stand alone must never be silently converted, which
is why `None` here means "not comparable" rather than "unknown".
"""
import math

# Canonical base per dimension, and the factor to reach it. Danish recipes use
# dl and spsk far more than ml, but ml is the base because it divides cleanly.
_MASS = {"g": 1.0, "gram": 1.0, "kg": 1000.0, "hg": 100.0}
_VOLUME = {
    "ml": 1.0, "cl": 10.0, "dl": 100.0, "l": 1000.0, "liter": 1000.0,
    # Danish kitchen measures. Exact by convention, not by physics — a
    # spiseske is defined as 15 ml here because that is what a recipe means
    # by it, whatever the spoon in the drawer holds.
    "tsk": 5.0, "spsk": 15.0,
}

# Units that describe a count of things and cannot become a mass or a volume.
# 3 fed hvidløg plus 100 g hvidløg is two lines on a list, not one, because
# nobody buys 112 g of garlic.
_COUNT = {"stk", "fed", "bundt", "pose", "dåse", "pakke", "håndfuld", "skive", "knsp"}

# Countables you buy as whole things. A shopping list saying "1,5 stk løg" is
# asking for something the shop does not sell, so these round *up* — half an
# onion short is a worse outcome than half an onion spare.
_DISCRETE = {"stk", "fed", "bundt", "pose", "dåse", "pakke", "skive"}

# Spellings that mean the same unit. Kept apart from the tables above so a
# synonym is one line and never a second conversion factor to keep in step.
_ALIASES = {
    "gr": "g", "gram": "g", "grams": "g", "kilo": "kg", "kilogram": "kg",
    "liter": "l", "litre": "l", "litres": "l", "deciliter": "dl",
    "teskefuld": "tsk", "teske": "tsk", "spiseskefuld": "spsk", "spiseske": "spsk",
    "stk.": "stk", "styk": "stk", "stykker": "stk", "fed.": "fed",
    "knivspids": "knsp", "dl.": "dl", "g.": "g", "kg.": "kg", "ml.": "ml",
}


def normalise_unit(unit):
    """A unit as this module knows it, or '' for none given.

    Unitless is a legitimate answer — "2 æg" has no unit and should not be
    given one — so this returns '' rather than guessing at 'stk'.
    """
    if not unit:
        return ""
    cleaned = str(unit).strip().lower().rstrip(".")
    cleaned = _ALIASES.get(cleaned, _ALIASES.get(cleaned + ".", cleaned))
    return cleaned


def dimension(unit):
    """'mass', 'volume', 'count', or None when the unit is unrecognised.

    None matters: an unrecognised unit must not be merged with anything, since
    the only safe assumption about a unit nobody has taught this module is that
    adding it to something else would produce a number that means nothing.
    """
    unit = normalise_unit(unit)
    if unit in _MASS:
        return "mass"
    if unit in _VOLUME:
        return "volume"
    if unit in _COUNT or unit == "":
        return "count"
    return None


def to_base(amount, unit):
    """(value in the dimension's base unit, dimension), or (amount, None).

    Returning the amount unchanged alongside a None dimension keeps callers
    from having to special-case the unconvertible: they can still carry the
    number, they just must not add it to a different unit.
    """
    if amount is None:
        return None, dimension(unit)
    unit = normalise_unit(unit)
    dim = dimension(unit)
    if dim == "mass":
        return amount * _MASS[unit], dim
    if dim == "volume":
        return amount * _VOLUME[unit], dim
    return amount, dim


def merge_key(name, unit):
    """What makes two ingredient lines the same line on a shopping list.

    Same name and same *dimension* — not the same unit. 500 g and 1 kg of
    hakket oksekød are one purchase; 3 fed hvidløg and 1 bundt persille are
    not, and neither is anything whose unit this module does not recognise.
    """
    dim = dimension(unit)
    return (name.strip().lower(), dim if dim else f"?{normalise_unit(unit)}")


# Spoon measures a shopping list should keep as spoons. "22,5 ml tomatpuré" is
# arithmetically right and useless in a shop; "1,5 spsk" is what the recipe
# meant and what you can judge a tube against.
_SPOONS = {"tsk", "spsk"}
_SPOON_CEILING_ML = 100


def _best_unit(base_amount, dim, source_unit=""):
    """The unit a person would write this quantity in.

    1200 g is a thing a computer says; 1,2 kg is a thing a shopping list says.
    The thresholds are where the smaller unit starts to look silly rather than
    where the conversion becomes exact.
    """
    if dim == "mass":
        return ("kg", 1000.0) if base_amount >= 1000 else ("g", 1.0)
    if dim == "volume":
        if source_unit in _SPOONS and base_amount < _SPOON_CEILING_ML:
            return source_unit, _VOLUME[source_unit]
        if base_amount >= 1000:
            return "l", 1000.0
        if base_amount >= 100:
            return "dl", 100.0
        return "ml", 1.0
    return "", 1.0


def format_amount(value):
    """A number as a shopping list writes it: no trailing zeros, one decimal.

    Danish decimal comma is a rendering concern and belongs in the page, not
    here — this module produces a value, not a localised string.
    """
    if value is None:
        return ""
    rounded = round(value, 1)
    if abs(rounded - round(rounded)) < 0.05:
        return str(int(round(rounded)))
    return f"{rounded:g}"


def describe(base_amount, dim, unit):
    """(amount, unit) as it should be shown, given a total in base units.

    `unit` is only consulted for dimensions that have no conversion — a count
    keeps whatever the recipe called it, since 'fed' and 'bundt' are not
    interchangeable and there is nothing to convert between.
    """
    if base_amount is None:
        return None, normalise_unit(unit)
    if dim in ("mass", "volume"):
        best, factor = _best_unit(base_amount, dim, normalise_unit(unit))
        return round(base_amount / factor, 2), best

    clean = normalise_unit(unit)
    if clean in _DISCRETE:
        # A hair of tolerance first: scaling by 6/4 produces 2.0000000000000004
        # often enough, and rounding that up to 3 onions is a bug you only
        # notice while carrying them home.
        return math.ceil(base_amount - 1e-9), clean
    return round(base_amount, 2), clean


def scale(amount, factor):
    """An amount scaled for a different number of servings.

    Rounded to two places rather than left as a float tail: a shopping list
    saying 133.33333333333331 g is not more accurate, only less readable, and
    the rounding error on a quantity nobody weighs to the gram is irrelevant.
    """
    if amount is None:
        return None
    return round(amount * factor, 2)
