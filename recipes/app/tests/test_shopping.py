"""Building a Danish supermarket list from English recipes.

Every test here calls one function with a literal. No database, no Flask, no
fixture — which is the payoff for keeping this module pure, and the reason the
arithmetic can be covered exhaustively instead of sampled.

The failures worth guarding are the quiet ones: a list that adds 3 fed hvidløg
to 100 g hvidløg, or that scales a recipe by a factor it invented, is wrong in a
way you only discover in the shop.

Ingredients carry two names — English for the recipe, Danish for the shelf — and
the list is built entirely from the Danish one. `_ing` below writes both, so a
test that only gave one would not silently pass through the fallback.
"""


import shopping


def _ing(name, shop_name, amount=None, unit="", optional=False):
    return {"name": name, "shop_name": shop_name, "amount": amount,
            "unit": unit, "optional": optional}


def _recipe(name="Chili", servings=4, ingredients=(), **extra):
    return {"name": name, "servings": servings,
            "ingredients": [dict(i) for i in ingredients], **extra}


def _entry(recipe, servings=None):
    return {"recipe": recipe, "servings": servings if servings is not None else recipe["servings"]}


def _items(result):
    return {i["name"]: i for section in result["sections"] for i in section["items"]}


# --- scaling ------------------------------------------------------------------


def test_a_recipe_taken_at_its_own_servings_is_unscaled():
    out = shopping.build_list([_entry(_recipe(ingredients=[
        _ing("minced beef", "hakket oksekød", 500, "g")]))])
    assert _items(out)["hakket oksekød"]["amount"] == 500


def test_wanting_more_servings_scales_the_quantities():
    recipe = _recipe(servings=4, ingredients=[
        _ing("minced beef", "hakket oksekød", 500, "g")])
    out = shopping.build_list([_entry(recipe, servings=6)])
    assert _items(out)["hakket oksekød"]["amount"] == 750


def test_wanting_fewer_servings_scales_down():
    recipe = _recipe(servings=4, ingredients=[_ing("rice", "ris", 300, "g")])
    out = shopping.build_list([_entry(recipe, servings=2)])
    assert _items(out)["ris"]["amount"] == 150


def test_a_recipe_with_no_stated_servings_is_taken_at_face_value():
    """Scaling by a guessed base would silently change every quantity."""
    recipe = _recipe(servings=None, ingredients=[_ing("rice", "ris", 300, "g")])
    out = shopping.build_list([{"recipe": recipe, "servings": 6}])
    assert _items(out)["ris"]["amount"] == 300


# --- merging ------------------------------------------------------------------


def test_the_same_ingredient_across_two_recipes_becomes_one_line():
    a = _recipe("Chili", 4, [_ing("onions", "løg", 2, "stk")])
    b = _recipe("Suppe", 4, [_ing("onion", "løg", 1, "stk")])
    out = shopping.build_list([_entry(a), _entry(b)])
    line = _items(out)["løg"]
    assert line["amount"] == 3
    assert sorted(line["recipes"]) == ["Chili", "Suppe"]


def test_different_units_of_the_same_dimension_are_added():
    """500 g and 1 kg of mince is one purchase, and the list should say 1.5 kg
    rather than making you do it in the aisle."""
    a = _recipe("A", 4, [_ing("minced beef", "hakket oksekød", 500, "g")])
    b = _recipe("B", 4, [_ing("minced beef", "hakket oksekød", 1, "kg")])
    line = _items(shopping.build_list([_entry(a), _entry(b)]))["hakket oksekød"]
    assert (line["amount"], line["unit"]) == (1.5, "kg")


def test_counts_and_masses_of_the_same_name_stay_separate():
    """Nobody buys 112 g of garlic. 3 fed and 100 g are two different lines."""
    a = _recipe("A", 4, [_ing("garlic", "hvidløg", 3, "fed")])
    b = _recipe("B", 4, [_ing("garlic", "hvidløg", 100, "g")])
    out = shopping.build_list([_entry(a), _entry(b)])
    lines = [i for s in out["sections"] for i in s["items"] if i["name"] == "hvidløg"]
    assert len(lines) == 2
    assert {(l["amount"], l["unit"]) for l in lines} == {(3, "fed"), (100, "g")}


def test_an_amountless_ingredient_makes_the_merged_line_amountless():
    """"smør til stegning" has no quantity. Adding the halves that did have
    numbers would print a total that was never true."""
    a = _recipe("A", 4, [_ing("butter", "smør", 50, "g")])
    b = _recipe("B", 4, [_ing("butter", "smør", None, "g")])
    line = _items(shopping.build_list([_entry(a), _entry(b)]))["smør"]
    assert line["amount"] is None
    assert line["amount_text"] == ""


def test_optional_survives_only_while_every_recipe_says_so():
    """An ingredient one recipe can do without and another cannot is not
    optional — you still have to buy it."""
    a = _recipe("A", 4, [_ing("chilli", "chili", 1, "stk", optional=True)])
    b = _recipe("B", 4, [_ing("chilli", "chili", 1, "stk")])
    assert _items(shopping.build_list([_entry(a), _entry(b)]))["chili"]["optional"] is False

    both = shopping.build_list([_entry(a), _entry(_recipe("C", 4, [
        _ing("chilli", "chili", 1, "stk", optional=True)]))])
    assert _items(both)["chili"]["optional"] is True


def test_an_unrecognised_unit_is_never_merged_into_a_known_one():
    """The only safe assumption about a unit nobody taught us is that adding it
    to something else produces a number meaning nothing."""
    a = _recipe("A", 4, [_ing("spice", "krydderi", 1, "nip")])
    b = _recipe("B", 4, [_ing("spice", "krydderi", 10, "g")])
    out = shopping.build_list([_entry(a), _entry(b)])
    lines = [i for s in out["sections"] for i in s["items"] if i["name"] == "krydderi"]
    assert len(lines) == 2


# --- sections -----------------------------------------------------------------


def test_items_are_grouped_into_supermarket_sections_in_walking_order():
    recipe = _recipe("Alt", 4, [
        _ing("rice", "ris", 300, "g"),
        _ing("carrots", "gulerødder", 3, "stk"),
        _ing("milk", "mælk", 5, "dl"),
    ])
    out = shopping.build_list([_entry(recipe)])
    order = [s["section"] for s in out["sections"]]
    assert order == ["Frugt & grønt", "Mejeri", "Kolonial"]


def test_items_within_a_section_are_sorted_by_name():
    recipe = _recipe("Alt", 4, [
        _ing("tomatoes", "tomat", 2, "stk"),
        _ing("cucumber", "agurk", 1, "stk"),
    ])
    out = shopping.build_list([_entry(recipe)])
    assert [i["name"] for i in out["sections"][0]["items"]] == ["agurk", "tomat"]


# --- staples ------------------------------------------------------------------


def test_a_staple_is_flagged_but_still_listed():
    """Hiding it means noticing you are out of salt in the shop, not at home."""
    recipe = _recipe("A", 4, [
        _ing("salt", "salt", 1, "tsk"),
        _ing("rice", "ris", 300, "g"),
    ])
    out = shopping.build_list([_entry(recipe)], staples=["salt", "peber"])
    items = _items(out)
    assert items["salt"]["staple"] is True
    assert items["ris"]["staple"] is False
    assert out["staple_items"] == 1


def test_staple_matching_ignores_case_and_padding():
    recipe = _recipe("A", 4, [_ing("olive oil", "Olivenolie", 2, "spsk")])
    out = shopping.build_list([_entry(recipe)], staples=["  olivenolie  "])
    assert _items(out)["Olivenolie"]["staple"] is True


def test_blank_staple_entries_are_ignored():
    """A trailing comma in the setting should not make every item a staple."""
    recipe = _recipe("A", 4, [_ing("rice", "ris", 300, "g")])
    out = shopping.build_list([_entry(recipe)], staples=["", "  ", "salt"])
    assert _items(out)["ris"]["staple"] is False


# --- the whole answer ---------------------------------------------------------


def test_an_empty_plan_is_an_empty_list_not_a_crash():
    out = shopping.build_list([])
    assert out == {"sections": [], "total_items": 0, "staple_items": 0, "recipes": 0}


def test_the_counts_describe_what_is_on_the_list():
    a = _recipe("A", 4, [_ing("rice", "ris", 300, "g"),
                         _ing("salt", "salt", 1, "tsk")])
    b = _recipe("B", 4, [_ing("milk", "mælk", 5, "dl")])
    out = shopping.build_list([_entry(a), _entry(b)], staples=["salt"])
    assert (out["recipes"], out["total_items"], out["staple_items"]) == (2, 3, 1)


# --- the two names ------------------------------------------------------------


def test_the_list_is_built_from_the_danish_name():
    """You are standing in a Danish shop. The English name is for the recipe."""
    recipe = _recipe("A", 4, [_ing("minced beef", "hakket oksekød", 500, "g")])
    out = shopping.build_list([_entry(recipe)])
    assert "hakket oksekød" in _items(out)
    assert "minced beef" not in _items(out)


def test_two_english_names_for_one_danish_product_merge():
    """"minced beef" and "beef mince" are one purchase, because both point at
    the same shelf label."""
    a = _recipe("A", 4, [_ing("minced beef", "hakket oksekød", 500, "g")])
    b = _recipe("B", 4, [_ing("beef mince", "hakket oksekød", 300, "g")])
    line = _items(shopping.build_list([_entry(a), _entry(b)]))["hakket oksekød"]
    assert line["amount"] == 800
    assert sorted(line["as_written"]) == ["beef mince", "minced beef"]


def test_the_line_says_what_the_recipes_called_it():
    """So a Danish label you do not recognise can be traced back to the English
    line that asked for it."""
    recipe = _recipe("A", 4, [_ing("plain skyr", "skyr naturel", 250, "g")])
    assert _items(shopping.build_list([_entry(recipe)]))["skyr naturel"]["as_written"] == ["plain skyr"]


def test_an_ingredient_with_no_danish_name_falls_back_to_the_english_one():
    """A shopping list with one untranslated line is still a shopping list; the
    alternative is losing the recipe."""
    recipe = _recipe("A", 4, [{"name": "sumac", "amount": 1, "unit": "tsk"}])
    assert "sumac" in _items(shopping.build_list([_entry(recipe)]))


def test_staples_are_matched_against_the_danish_name():
    """The setting is written as shelf labels, because that is what the list
    shows."""
    recipe = _recipe("A", 4, [_ing("olive oil", "olivenolie", 2, "spsk")])
    out = shopping.build_list([_entry(recipe)], staples=["olivenolie"])
    assert _items(out)["olivenolie"]["staple"] is True
