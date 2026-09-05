"""Writing a recipe in by hand.

The catalogue could only be filled by pasting an assistant's reply, which is
the right way to get twenty recipes and the wrong way to get one — the dish
somebody's grandmother made is not something a prompt produces.

The rule this file mostly exists to hold: a recipe typed here and the same
recipe pasted must land in the database identically. There is one reader, and
both doors go through it.
"""
import json

import store


def _own(name="Frikadeller", category="Family", **extra):
    recipe = {
        "name": name,
        "category": category,
        "servings": 4,
        "minutes": 30,
        "method": "1. Mix. 2. Fry.",
        "ingredients": [
            {"name": "minced pork", "shop_name": "hakket svinekød",
             "amount": "500", "unit": "g"},
        ],
    }
    recipe.update(extra)
    return recipe


def test_a_written_recipe_is_saved_and_readable(client):
    body = client.post("/api/recipes", json=_own()).get_json()
    assert body["id"]
    recipe = client.get(f"/api/recipes/{body['id']}").get_json()
    assert recipe["name"] == "Frikadeller"
    assert recipe["servings"] == 4 and recipe["minutes"] == 30
    assert recipe["ingredients"][0]["shop_name"] == "hakket svinekød"


def test_it_lands_exactly_where_the_same_paste_would(client):
    """One reader, two doors. If these ever diverge, a recipe typed in behaves
    differently on the shopping list from the same recipe pasted, and nothing
    about the app explains why."""
    written = client.post("/api/recipes", json=_own(name="Written")).get_json()
    client.post("/api/import", json={"text": json.dumps(
        {"recipes": [_own(name="Pasted")]})})

    a = client.get(f"/api/recipes/{written['id']}").get_json()
    [b] = [r for r in client.get("/api/recipes").get_json() if r["name"] == "Pasted"]
    b = client.get(f"/api/recipes/{b['id']}").get_json()
    for field in ("servings", "minutes", "method", "category"):
        assert a[field] == b[field]
    assert a["ingredients"] == b["ingredients"]


def test_the_units_are_normalised_the_way_a_paste_is(client):
    """Otherwise a typed "grams" never merges with a pasted "g" on the list."""
    body = client.post("/api/recipes", json=_own(ingredients=[
        {"name": "flour", "shop_name": "mel", "amount": "500", "unit": "grams"}])).get_json()
    recipe = client.get(f"/api/recipes/{body['id']}").get_json()
    assert recipe["ingredients"][0]["unit"] == "g"


def test_a_missing_danish_name_falls_back_and_says_so(client):
    """A shopping list with one untranslated line is still a shopping list.
    Losing the recipe over it would not be."""
    body = client.post("/api/recipes", json=_own(ingredients=[
        {"name": "cod", "amount": "400", "unit": "g"}])).get_json()
    recipe = client.get(f"/api/recipes/{body['id']}").get_json()
    assert recipe["ingredients"][0]["shop_name"] == "cod"
    assert any("cod" in w for w in body["warnings"])


def test_it_is_marked_as_your_own_not_shipped(client):
    """A seeded recipe says "shipped with the add-on" rather than a date. One
    you typed did happen on a day, and that day is worth keeping."""
    body = client.post("/api/recipes", json=_own()).get_json()
    assert client.get(f"/api/recipes/{body['id']}").get_json()["source"] == "own"


# --- not overwriting what you cannot get back ---------------------------------


def test_a_name_already_used_is_refused_rather_than_replaced(client):
    """Replacing on a name match is right for a pasted pack — re-pasting a
    half-worked import is normal. It is wrong for something somebody typed."""
    client.post("/api/recipes", json=_own(method="the original"))
    again = client.post("/api/recipes", json=_own(method="a different one"))
    assert again.status_code == 409
    assert "Frikadeller" in again.get_json()["detail"]

    [row] = [r for r in client.get("/api/recipes").get_json() if r["name"] == "Frikadeller"]
    assert client.get(f"/api/recipes/{row['id']}").get_json()["method"] == "the original"


def test_the_refusal_matches_on_case_and_spacing_like_the_saver_does(client):
    """find_by_name and save_recipe must agree, or the check passes and the
    save overwrites anyway — the exact bug the check exists to prevent."""
    client.post("/api/recipes", json=_own(name="Frikadeller"))
    assert client.post("/api/recipes", json=_own(name="  frikadeller  ")).status_code == 409


def test_replacing_is_possible_once_it_has_been_asked_for(client):
    client.post("/api/recipes", json=_own(method="the original"))
    body = client.post("/api/recipes",
                       json=dict(_own(method="the new one"), replace=True)).get_json()
    assert body["replaced"] is True
    assert client.get(f"/api/recipes/{body['id']}").get_json()["method"] == "the new one"
    assert len(client.get("/api/recipes").get_json()) == 1, "replaced, not added alongside"


def test_a_different_category_is_a_different_recipe(client):
    """Same dish, two categories, is two rows — that is what the categories
    are for."""
    client.post("/api/recipes", json=_own(category="Family"))
    assert client.post("/api/recipes", json=_own(category="Bulk")).status_code == 200


# --- what the form cannot send ------------------------------------------------


def test_a_recipe_with_no_name_is_a_400(client):
    assert client.post("/api/recipes", json=_own(name="")).status_code == 400


def test_a_recipe_with_no_ingredients_is_a_400(client):
    """A recipe with nothing to buy contributes nothing to a shopping list,
    which is what this app is for."""
    assert client.post("/api/recipes", json=_own(ingredients=[])).status_code == 400


def test_an_empty_body_is_a_400_not_a_crash(client):
    assert client.post("/api/recipes", json={}).status_code == 400


# --- the lookup itself --------------------------------------------------------


def test_find_by_name_ignores_case_and_spacing(conn):
    store.save_recipe(conn, {"name": "Chicken curry", "category": "Family",
                                "ingredients": []})
    assert store.find_by_name(conn, "  chicken   CURRY ", "family") is not None
    assert store.find_by_name(conn, "Chicken curry", "Bulk") is None
    assert store.find_by_name(conn, "Nothing like it", "Family") is None


def test_a_danish_comma_in_an_amount_is_read_as_a_number(client):
    """The page prints 1,5 and the keyboard types 1,5. A form that rejects what
    the app itself writes is one that argues with its own output."""
    body = client.post("/api/recipes", json=_own(ingredients=[
        {"name": "milk", "shop_name": "mælk", "amount": "1,5", "unit": "dl"}])).get_json()
    recipe = client.get(f"/api/recipes/{body['id']}").get_json()
    assert recipe["ingredients"][0]["amount"] == 1.5
