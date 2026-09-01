"""Reading whatever an assistant pasted back.

The governing rule is the one the module docstring states: report what had to be
dropped rather than failing the whole paste. A pack that lands four of five and
says so beats a parse error, because the user's only other remedy is to go and
ask an assistant again.
"""
import json

import pytest

import importer


def _pack(**overrides):
    base = {
        "recipes": [{
            "name": "Kylling i karry",
            "category": "Familie",
            "servings": 4,
            "ingredients": [{"name": "kyllingebryst", "amount": 600, "unit": "g"}],
            "method": "1. Steg kyllingen.",
        }]
    }
    base["recipes"][0].update(overrides)
    return json.dumps(base)


# --- finding the JSON ---------------------------------------------------------


def test_plain_json_is_read():
    assert importer.extract_json(_pack())["recipes"][0]["name"] == "Kylling i karry"


def test_json_inside_a_fence_is_read():
    assert importer.extract_json(f"```json\n{_pack()}\n```")["recipes"]


def test_json_surrounded_by_chat_is_read():
    """The common case, not the exception: assistants explain themselves either
    side of the thing you asked for."""
    text = f"Here are your recipes!\n\n{_pack()}\n\nLet me know if you want more."
    assert importer.extract_json(text)["recipes"]


def test_braces_inside_a_string_do_not_end_the_object():
    text = '{"recipes": [{"name": "Mad {med} tegn", "category": "Familie", ' \
           '"ingredients": [{"name": "ris", "amount": 1, "unit": "kg"}]}]}'
    assert importer.extract_json(text)["recipes"][0]["name"] == "Mad {med} tegn"


@pytest.mark.parametrize("text", ["", "   ", "no json here at all"])
def test_a_paste_with_no_object_is_refused(text):
    with pytest.raises(importer.PackError):
        importer.extract_json(text)


# --- field names --------------------------------------------------------------


def test_synonyms_for_the_field_names_are_accepted():
    """Assistants reliably produce the right shape and unreliably produce the
    right names for it."""
    raw = {"opskrifter": [{"titel": "X", "navn": "Boller", "kategori": "Familie",
                           "portioner": 2,
                           "ingredienser": [{"navn": "mel", "mængde": 500, "enhed": "g"}]}]}
    out = importer.normalise(raw)
    recipe = out["recipes"][0]
    assert recipe["name"] == "Boller"
    assert recipe["servings"] == 2
    assert recipe["ingredients"][0]["name"] == "mel"


def test_a_bare_single_recipe_is_accepted():
    """What an assistant returns when you ask for one."""
    raw = {"name": "Boller", "category": "Familie",
           "ingredients": [{"name": "mel", "amount": 500, "unit": "g"}]}
    assert importer.normalise(raw)["recipes"][0]["name"] == "Boller"


# --- numbers ------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    (600, 600.0), ("600", 600.0), ("600 g", 600.0), ("1,5", 1.5), ("ca. 2 stk", 2.0),
])
def test_a_number_is_found_even_with_the_unit_glued_on(raw, expected):
    out = importer.normalise({"recipes": [{
        "name": "X", "category": "C",
        "ingredients": [{"name": "mel", "amount": raw, "unit": "g"}]}]})
    assert out["recipes"][0]["ingredients"][0]["amount"] == expected


def test_a_missing_amount_stays_missing():
    """"smør til stegning" has no quantity, and a zero would put "0 g smør" on
    a shopping list."""
    out = importer.normalise({"recipes": [{
        "name": "X", "category": "C",
        "ingredients": [{"name": "smør"}]}]})
    assert out["recipes"][0]["ingredients"][0]["amount"] is None


# --- dropping what cannot be used ---------------------------------------------


def test_a_recipe_without_a_name_is_skipped_and_reported():
    out = importer.normalise({"recipes": [
        {"category": "C", "ingredients": [{"name": "mel"}]},
        {"name": "Good", "category": "C", "ingredients": [{"name": "mel"}]},
    ]})
    assert [r["name"] for r in out["recipes"]] == ["Good"]
    assert any("no name" in w for w in out["warnings"])


def test_a_recipe_without_ingredients_is_skipped():
    """A recipe that cannot contribute to a shopping list is not much of a
    recipe for this app."""
    out = importer.normalise({"recipes": [
        {"name": "Empty", "category": "C", "ingredients": []},
        {"name": "Good", "category": "C", "ingredients": [{"name": "mel"}]},
    ]})
    assert [r["name"] for r in out["recipes"]] == ["Good"]


def test_a_category_can_come_from_the_chooser_instead_of_the_pack():
    out = importer.normalise(
        {"recipes": [{"name": "X", "ingredients": [{"name": "mel"}]}]},
        default_category="Bulk")
    assert out["recipes"][0]["category"] == "Bulk"


def test_no_category_anywhere_is_a_skip_not_a_guess():
    with pytest.raises(importer.PackError):
        importer.normalise({"recipes": [{"name": "X", "ingredients": [{"name": "mel"}]}]})


def test_an_unrecognised_unit_is_kept_and_flagged():
    """It still names something to buy; it simply will not merge with
    anything."""
    out = importer.normalise({"recipes": [{
        "name": "X", "category": "C",
        "ingredients": [{"name": "krydderi", "amount": 1, "unit": "nip"}]}]})
    assert out["recipes"][0]["ingredients"][0]["unit"] == "nip"
    assert any("unrecognised unit" in w for w in out["warnings"])


def test_a_pack_with_nothing_usable_raises_with_the_reason():
    with pytest.raises(importer.PackError, match="no usable recipes"):
        importer.normalise({"recipes": [{"name": "X", "category": "C", "ingredients": []}]})


def test_a_partly_broken_pack_still_lands_the_rest():
    """The whole design in one test."""
    out = importer.normalise({"recipes": [
        {"name": "A", "category": "C", "ingredients": [{"name": "mel"}]},
        {"category": "C", "ingredients": [{"name": "mel"}]},
        {"name": "C", "category": "C", "ingredients": []},
        {"name": "D", "category": "C", "ingredients": [{"name": "ris"}]},
    ]})
    assert [r["name"] for r in out["recipes"]] == ["A", "D"]
    # Two recipes were dropped. The other warnings are the per-ingredient
    # "no Danish name" notes, which are information rather than loss.
    assert len([w for w in out["warnings"] if w.startswith("skipped")]) == 2
