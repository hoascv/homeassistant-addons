"""Storing recipes, and the plan built on top of them."""
import planner
import store
from conftest import a_recipe


def test_a_recipe_round_trips_with_both_names(conn):
    recipe_id = store.save_recipe(conn, a_recipe())
    conn.commit()
    got = store.get_recipe(conn, recipe_id)
    assert got["name"] == "Test dish"
    assert got["ingredients"][0] == {
        "name": "rice", "shop_name": "ris", "amount": 300.0, "unit": "g", "optional": False}


def test_an_ingredient_without_a_danish_name_falls_back_on_read(conn):
    """The column is nullable, so a row written before the two-name split — or
    by a pack that gave one name — still comes back usable."""
    recipe_id = store.save_recipe(conn, a_recipe(ingredients=[{"name": "sumac"}]))
    conn.commit()
    assert store.get_recipe(conn, recipe_id)["ingredients"][0]["shop_name"] == "sumac"


def test_saving_the_same_name_and_category_replaces_rather_than_duplicates(conn):
    """Re-importing a pack is normal — a paste that half-worked gets retried —
    and should leave one copy."""
    store.save_recipe(conn, a_recipe(servings=4))
    store.save_recipe(conn, a_recipe(servings=6))
    conn.commit()
    recipes = store.list_recipes(conn)
    assert len(recipes) == 1
    assert recipes[0]["servings"] == 6


def test_replacing_a_recipe_drops_the_ingredients_it_no_longer_has(conn):
    store.save_recipe(conn, a_recipe(ingredients=[
        {"name": "rice", "shop_name": "ris"}, {"name": "peas", "shop_name": "ærter"}]))
    store.save_recipe(conn, a_recipe(ingredients=[{"name": "rice", "shop_name": "ris"}]))
    conn.commit()
    assert len(store.list_recipes(conn, with_ingredients=True)[0]["ingredients"]) == 1


def test_the_same_name_in_two_categories_is_two_recipes(conn):
    store.save_recipe(conn, a_recipe(category="Family"))
    store.save_recipe(conn, a_recipe(category="Bulk"))
    conn.commit()
    assert len(store.list_recipes(conn)) == 2


def test_deleting_a_recipe_takes_its_ingredients_with_it(conn):
    """The cascade only works because schema.connect turns foreign keys on,
    which SQLite leaves off by default."""
    recipe_id = store.save_recipe(conn, a_recipe())
    conn.commit()
    store.delete_recipe(conn, recipe_id)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) AS n FROM ingredients").fetchone()["n"] == 0


def test_a_recipe_without_a_name_or_category_is_refused(conn):
    import pytest
    with pytest.raises(ValueError):
        store.save_recipe(conn, a_recipe(name="  "))
    with pytest.raises(ValueError):
        store.save_recipe(conn, a_recipe(category=""))


def test_a_batch_lands_what_it_can_and_reports_the_rest(conn):
    added, warnings = store.save_many(conn, [a_recipe("A"), a_recipe(name=""), a_recipe("B")])
    conn.commit()
    assert added == 2
    assert len(warnings) == 1


def test_recipes_can_be_filtered_and_searched(conn):
    store.save_recipe(conn, {**a_recipe("Chicken curry"), "notes": "mild"})
    store.save_recipe(conn, a_recipe("Beef stew", category="Bulk"))
    conn.commit()
    assert len(store.list_recipes(conn, category="Bulk")) == 1
    assert len(store.list_recipes(conn, query="curry")) == 1
    assert len(store.list_recipes(conn, query="mild")) == 1, "notes are searched too"


# --- the plan -----------------------------------------------------------------


def test_planning_a_recipe_twice_updates_the_servings(conn):
    recipe_id = store.save_recipe(conn, a_recipe())
    planner.add(conn, recipe_id, 4)
    planner.add(conn, recipe_id, 8)
    conn.commit()
    entries = planner.entries(conn)
    assert len(entries) == 1
    assert entries[0]["servings"] == 8


def test_clearing_the_plan_also_clears_the_ticks(conn):
    """Otherwise next week's list opens with items crossed off from a shop that
    happened days ago."""
    recipe_id = store.save_recipe(conn, a_recipe())
    planner.add(conn, recipe_id, 4)
    planner.set_tick(conn, planner.item_key("ris", "g"), True)
    conn.commit()
    planner.clear(conn)
    conn.commit()
    assert planner.entries(conn) == []
    assert planner.ticked_keys(conn) == set()


def test_deleting_a_planned_recipe_removes_it_from_the_plan(conn):
    recipe_id = store.save_recipe(conn, a_recipe())
    planner.add(conn, recipe_id, 4)
    conn.commit()
    store.delete_recipe(conn, recipe_id)
    conn.commit()
    assert planner.entries(conn) == []


def test_a_tick_survives_the_list_being_rebuilt(conn):
    """Ticking off six things and then remembering the bread must not clear the
    six — which is why a tick keys on the line's own identity, not a row id."""
    first = store.save_recipe(conn, a_recipe("A"))
    planner.add(conn, first, 4)
    conn.commit()

    key = planner.shopping_list(conn)["sections"][0]["items"][0]["key"]
    planner.set_tick(conn, key, True)
    conn.commit()

    second = store.save_recipe(conn, a_recipe("B", ingredients=[
        {"name": "bread", "shop_name": "rugbrød", "amount": 1, "unit": "stk"}]))
    planner.add(conn, second, 4)
    conn.commit()

    rebuilt = planner.shopping_list(conn)
    ticked = [i for s in rebuilt["sections"] for i in s["items"] if i["ticked"]]
    assert [i["name"] for i in ticked] == ["ris"]
    assert rebuilt["remaining_items"] == 1
