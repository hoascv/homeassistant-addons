"""The recipes that ship with the add-on."""
import seeding
import store


def test_the_first_run_seeds_both_categories(conn):
    added = seeding.seed_if_needed(conn, "Family", "Bulk")
    assert added > 10
    assert set(store.categories_in_use(conn)) == {"Family", "Bulk"}


def test_seeding_happens_only_once(conn):
    seeding.seed_if_needed(conn, "Family", "Bulk")
    assert seeding.seed_if_needed(conn, "Family", "Bulk") == 0


def test_a_deleted_seed_stays_deleted(conn):
    """Re-seeding on every start would undo an edit the moment the add-on
    restarted, which is a worse failure than shipping nothing."""
    seeding.seed_if_needed(conn, "Family", "Bulk")
    victim = store.list_recipes(conn)[0]
    store.delete_recipe(conn, victim["id"])
    conn.commit()

    seeding.seed_if_needed(conn, "Family", "Bulk")
    assert victim["name"] not in [r["name"] for r in store.list_recipes(conn)]


def test_a_new_seed_version_adds_only_what_is_missing(conn, monkeypatch):
    seeding.seed_if_needed(conn, "Family", "Bulk")
    before = len(store.list_recipes(conn))
    store.delete_recipe(conn, store.list_recipes(conn)[0]["id"])
    conn.commit()

    monkeypatch.setattr(seeding, "SEED_VERSION", "2")
    added = seeding.seed_if_needed(conn, "Family", "Bulk")
    assert added == 1
    assert len(store.list_recipes(conn)) == before


def test_the_seeds_follow_the_configured_category_names(conn):
    """A household that renamed Bulk should still get the recipes."""
    seeding.seed_if_needed(conn, "Familie", "Træning")
    assert set(store.categories_in_use(conn)) == {"Familie", "Træning"}


def test_every_seeded_ingredient_has_a_danish_shop_name(conn):
    """The shipped recipes are the worked example of the two-name model; one
    missing a Danish label would put an English word on a Danish shopping
    list."""
    seeding.seed_if_needed(conn, "Family", "Bulk")
    for recipe in store.list_recipes(conn, with_ingredients=True):
        for ingredient in recipe["ingredients"]:
            assert ingredient["shop_name"], f"{recipe['name']}: {ingredient['name']}"
            assert ingredient["shop_name"] != ingredient["name"] or " " not in ingredient["name"]


def test_every_seeded_recipe_states_its_servings(conn):
    """The shopping list scales from it. A seed without one would silently
    ignore the household size."""
    seeding.seed_if_needed(conn, "Family", "Bulk")
    for recipe in store.list_recipes(conn, with_ingredients=True):
        assert recipe["servings"], recipe["name"]


def test_every_bulk_recipe_states_its_protein(conn):
    """It is the figure the category exists for."""
    seeding.seed_if_needed(conn, "Family", "Bulk")
    for recipe in store.list_recipes(conn, category="Bulk"):
        assert recipe["protein_g"], recipe["name"]
