"""Not keeping two copies of the same recipe.

`UNIQUE(name, category)` catches an exact re-import, which is the case it was
written for. Nothing else got through it. A pack pasted from an assistant says
"Chicken Curry" one time and "Chicken curry" the next, and both sat in the
catalogue as separate dishes — one recipe entered eight ways produced six rows.

The rule now: recipes are matched on their name and category case-folded with
whitespace collapsed. What is *not* done is as deliberate — nothing is merged
automatically, because two copies that look alike may genuinely differ and
deleting the wrong one loses work that exists nowhere else.
"""
import sqlite3

import pytest

import schema
import store


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema.create(conn)
    yield conn
    conn.close()


def _save(conn, name, category="Family", **extra):
    store.save_recipe(conn, {"name": name, "category": category,
                             "ingredients": [], **extra})


def _count(conn):
    return conn.execute("SELECT COUNT(*) AS n FROM recipes").fetchone()["n"]


# --- what now counts as the same recipe ---------------------------------------


@pytest.mark.parametrize("second", [
    "Chicken curry",      # the exact repeat the old constraint already caught
    "chicken curry",      # a pack written in lower case
    "CHICKEN CURRY",
    "Chicken Curry",      # title case, the commonest assistant variation
    "  Chicken curry  ",  # pasted with padding
    "Chicken  curry",     # a double space is a typo, not a dish
    "\tChicken curry\n",
])
def test_a_variation_of_the_name_updates_rather_than_adds(db, second):
    _save(db, "Chicken curry")
    _save(db, second)
    assert _count(db) == 1


def test_the_category_is_matched_the_same_way(db):
    _save(db, "Chicken curry", "Family")
    _save(db, "Chicken curry", "family")
    _save(db, "Chicken curry", " FAMILY ")
    assert _count(db) == 1


def test_danish_names_fold_correctly(db):
    """casefold, not lower — a Danish catalogue is full of characters that
    make the difference, and this add-on's whole point is Danish shopping."""
    _save(db, "Grød med bær")
    _save(db, "GRØD MED BÆR")
    assert _count(db) == 1


def test_the_same_dish_in_another_category_is_a_different_recipe(db):
    """Not a duplicate. A family portion and a bulk portion of the same dish
    are different recipes with different servings, which is exactly why
    category is part of the key."""
    _save(db, "Chicken curry", "Family")
    _save(db, "Chicken curry", "Bulk")
    assert _count(db) == 2


def test_genuinely_different_recipes_are_left_alone(db):
    for name in ("Chicken curry", "Chicken curry with rice", "Beef curry"):
        _save(db, name)
    assert _count(db) == 3


# --- what a re-import does to the copy already there ---------------------------


def test_a_reimport_updates_the_content(db):
    _save(db, "Chicken curry", servings=4)
    _save(db, "chicken curry", servings=6)
    assert db.execute("SELECT servings FROM recipes").fetchone()["servings"] == 6


def test_a_reimport_does_not_rename_what_is_already_there(db):
    """Reaching the update path means the names already matched apart from case
    or spacing, so the incoming spelling carries no information — and taking it
    would let one lower-case pack rename the user's "Family" category to
    "family" everywhere it appears."""
    _save(db, "Chicken curry", "Family")
    _save(db, "CHICKEN CURRY", "family")
    row = db.execute("SELECT name, category FROM recipes").fetchone()
    assert row["name"] == "Chicken curry"
    assert row["category"] == "Family"


def test_ingredients_are_replaced_not_accumulated(db):
    """The old behaviour on an exact match, which must survive the wider one:
    re-importing three times should not leave nine ingredients."""
    for _ in range(3):
        store.save_recipe(db, {
            "name": "Chicken CURRY", "category": "Family",
            "ingredients": [{"name": "chicken", "shop_name": "kylling", "amount": 600,
                             "unit": "g", "optional": False}]})
    assert db.execute(
        "SELECT COUNT(*) AS n FROM ingredients").fetchone()["n"] == 1


# --- reporting what is already there ------------------------------------------


def _force_duplicate(conn, name, category="Family"):
    """Write a row the way an older version did, bypassing the new matching.
    This is what an upgraded database actually contains."""
    conn.execute(
        "INSERT INTO recipes (name, category, source, created_at, updated_at) "
        "VALUES (?, ?, 'import', '2026-08-01T10:00:00', '2026-08-01T10:00:00')",
        (name, category))


def test_nothing_duplicated_reports_nothing(db):
    _save(db, "Chicken curry")
    _save(db, "Beef curry")
    assert store.duplicate_groups(db) == []


def test_rows_from_an_older_version_are_found_and_grouped(db):
    _force_duplicate(db, "Chicken curry")
    _force_duplicate(db, "chicken curry")
    _force_duplicate(db, "Chicken  Curry")
    _force_duplicate(db, "Beef curry")
    schema._migrate(db)  # what the upgrade does on first start

    groups = store.duplicate_groups(db)
    assert len(groups) == 1
    assert len(groups[0]["recipes"]) == 3
    assert groups[0]["category"] == "Family"


def test_the_most_recent_copy_is_listed_first(db):
    """The screen marks it, so the reader does not have to work out which one
    "keep the newer" would mean."""
    _force_duplicate(db, "Chicken curry")
    _force_duplicate(db, "chicken curry")
    schema._migrate(db)
    db.execute("UPDATE recipes SET updated_at = '2026-09-01T10:00:00' "
               "WHERE name = 'chicken curry'")

    [group] = store.duplicate_groups(db)
    assert group["recipes"][0]["name"] == "chicken curry"


def test_each_copy_reports_what_distinguishes_it(db):
    """Ingredient count and dates are the whole basis for choosing between two
    rows that otherwise read identically."""
    _force_duplicate(db, "Chicken curry")
    _force_duplicate(db, "chicken curry")
    schema._migrate(db)
    first = db.execute("SELECT id FROM recipes ORDER BY id").fetchone()["id"]
    db.execute("INSERT INTO ingredients (recipe_id, position, name) VALUES (?, 0, 'kylling')",
               (first,))

    [group] = store.duplicate_groups(db)
    counts = {r["name"]: r["ingredient_count"] for r in group["recipes"]}
    assert counts == {"Chicken curry": 1, "chicken curry": 0}
    assert all("updated_at" in r and "source" in r for r in group["recipes"])


def test_duplicates_are_never_merged_on_their_own(db):
    """The user chose to be shown them rather than have them merged. Reading
    the list must not delete anything."""
    _force_duplicate(db, "Chicken curry")
    _force_duplicate(db, "chicken curry")
    schema._migrate(db)
    store.duplicate_groups(db)
    store.duplicate_groups(db)
    assert _count(db) == 2


def test_a_later_import_converges_on_one_of_the_existing_copies(db):
    """With duplicates already present, an import must pick one and update it
    rather than adding a third."""
    _force_duplicate(db, "Chicken curry")
    _force_duplicate(db, "chicken curry")
    schema._migrate(db)
    _save(db, "CHICKEN CURRY", servings=6)
    assert _count(db) == 2
    assert db.execute(
        "SELECT COUNT(*) AS n FROM recipes WHERE servings = 6").fetchone()["n"] == 1


# --- the upgrade --------------------------------------------------------------


def test_a_database_without_the_column_gains_it(db):
    """CREATE TABLE IF NOT EXISTS does nothing to a table that already exists,
    so without the migration every query naming dedupe_key fails on an install
    that has been running for weeks."""
    db.execute("DROP TABLE recipes")
    db.execute("""CREATE TABLE recipes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        category TEXT NOT NULL, servings INTEGER, method TEXT, notes TEXT,
        protein_g REAL, kcal REAL, minutes INTEGER,
        source TEXT NOT NULL DEFAULT 'import',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(name, category))""")
    _force_duplicate(db, "Chicken curry")

    schema.create(db)

    columns = {row[1] for row in db.execute("PRAGMA table_info(recipes)")}
    assert "dedupe_key" in columns
    assert db.execute("SELECT dedupe_key FROM recipes").fetchone()[0] == \
        schema.key_text("Chicken curry", "Family")
    _save(db, "CHICKEN CURRY")
    assert _count(db) == 1


def test_the_migration_is_safe_to_run_twice(db):
    _save(db, "Chicken curry")
    schema.create(db)
    schema.create(db)
    assert _count(db) == 1


# --- through the endpoint -----------------------------------------------------


def test_the_endpoint_reports_the_groups(client):
    for name in ("Chicken curry", "chicken curry"):
        client.post("/api/import", json={"text":
            f'```json\n{{"recipes": [{{"name": "{name}", "category": "Family", '
            f'"servings": 4, "ingredients": []}}]}}\n```'})
    body = client.get("/api/duplicates").get_json()
    # The importer goes through save_recipe, so these merged rather than
    # duplicating — which is the point.
    assert body["groups"] == []


def test_the_summary_carries_the_count_for_the_nudge(client):
    """So the Recipes tab can show it without every page load paying for the
    full grouping query."""
    body = client.get("/api/summary").get_json()
    assert body["duplicate_groups"] == 0


def test_the_nudge_only_appears_when_there_is_something_to_do():
    """A permanent "0 duplicates" line is one you stop reading, and then miss
    the day it says 3."""
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "static", "app.js")
    with open(path, encoding="utf-8") as handle:
        js = handle.read()
    assert "nudge.hidden = groups === 0;" in js


def test_deleting_a_copy_asks_first():
    """The one destructive button in a panel whose whole premise is that the
    two rows are hard to tell apart."""
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "static", "app.js")
    with open(path, encoding="utf-8") as handle:
        js = handle.read()
    handler = js[js.index('el("dupe-list").addEventListener'):]
    assert "confirm(" in handler[:900]


def test_the_migration_collapses_internal_spaces_like_the_save_path_does(db):
    """SQLite's trim() removes padding but cannot collapse a double space, so a
    SQL backfill wrote a weaker key than key_text() produces — and
    "Chicken  curry" was then neither reported as a duplicate nor matched by a
    later import. One definition, used by both."""
    _force_duplicate(db, "Chicken  curry")
    _force_duplicate(db, "Chicken curry")
    db.execute("UPDATE recipes SET dedupe_key = NULL")
    schema._migrate(db)

    keys = {row[0] for row in db.execute("SELECT dedupe_key FROM recipes")}
    assert keys == {schema.key_text("Chicken curry", "Family")}
    assert len(store.duplicate_groups(db)[0]["recipes"]) == 2


def test_the_key_lives_in_one_place():
    """store imports schema, so this is the only direction that lets the
    migration and the save path share a definition rather than drift."""
    import os
    with open(os.path.join(os.path.dirname(__file__), "..", "store.py"),
              encoding="utf-8") as handle:
        store_src = handle.read()
    assert "def dedupe_key(" not in store_src, "a second copy of the rule"
    assert "schema.key_text(" in store_src
