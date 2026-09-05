"""To-try lists, cooked history, and ratings.

This is the part that makes the add-on a record of what the household actually
eats rather than a pile of imported text — the thing it was asked for in the
first place.

Three separate ideas, kept separate because they answer different questions:

    status         where a recipe is: to try, made, or neither
    times_cooked   how often, which a boolean cannot say
    rating         how good, which having cooked it does not tell you
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


@pytest.fixture
def recipe_id(db):
    store.save_recipe(db, {"name": "Chicken curry", "category": "Family",
                           "servings": 4, "ingredients": []})
    return db.execute("SELECT id FROM recipes").fetchone()["id"]


# --- the two lists ------------------------------------------------------------


def test_a_new_recipe_is_on_neither_list(db, recipe_id):
    assert store.get_recipe(db, recipe_id)["status"] is None


def test_it_can_go_on_the_to_try_list(db, recipe_id):
    assert store.set_status(db, recipe_id, store.TODO)["status"] == "todo"


def test_pressing_the_same_button_again_takes_it_off(db, recipe_id):
    """The buttons are toggles. A separate "remove" would be a third control
    for what the first one obviously means."""
    store.set_status(db, recipe_id, store.TODO)
    assert store.set_status(db, recipe_id, store.TODO)["status"] is None


def test_the_two_lists_are_mutually_exclusive(db, recipe_id):
    """A recipe is either one you mean to try or one you have made, and a thing
    that is both is really just the second."""
    store.set_status(db, recipe_id, store.TODO)
    assert store.set_status(db, recipe_id, store.COOKED)["status"] == "cooked"


def test_an_invented_status_is_refused(db, recipe_id):
    with pytest.raises(ValueError, match="status must be"):
        store.set_status(db, recipe_id, "maybe")


def test_setting_the_status_of_a_missing_recipe_reports_nothing(db):
    assert store.set_status(db, 999, store.TODO) is None


# --- cooking is an event ------------------------------------------------------


def test_making_it_counts_rather_than_flags(db, recipe_id):
    """"We make this every other week" is the useful fact, and a boolean cannot
    say it."""
    for expected in (1, 2, 3):
        recipe = store.log_cooked(db, recipe_id)
        assert recipe["times_cooked"] == expected
    assert recipe["status"] == "cooked"
    assert recipe["last_cooked_at"]


def test_toggling_the_list_never_touches_the_count(db, recipe_id):
    """Which is why these are two functions. A status that also counted would
    tick up every time somebody pressed a filter chip."""
    store.log_cooked(db, recipe_id)
    store.set_status(db, recipe_id, store.TODO)
    store.set_status(db, recipe_id, store.TODO)
    assert store.get_recipe(db, recipe_id)["times_cooked"] == 1


def test_the_last_cooked_date_moves_forward(db, recipe_id):
    store.log_cooked(db, recipe_id, when="2026-08-01T18:00:00")
    store.log_cooked(db, recipe_id, when="2026-09-01T18:00:00")
    assert store.get_recipe(db, recipe_id)["last_cooked_at"] == "2026-09-01T18:00:00"


def test_cooking_a_missing_recipe_reports_nothing(db):
    assert store.log_cooked(db, 999) is None


# --- taking one back ----------------------------------------------------------
#
# The button that logs a cooking sits next to a toggle and was read as one, so
# a single dinner arrived as seven presses. A count that only goes up is a
# record you cannot correct.


def test_undo_removes_one_cooking_not_the_history(db, recipe_id):
    """Seven presses that should have been one are seven separate mistakes. A
    "clear" would also make losing a real history a single tap."""
    for _ in range(3):
        store.log_cooked(db, recipe_id)
    assert store.undo_cooked(db, recipe_id)["times_cooked"] == 2
    assert store.undo_cooked(db, recipe_id)["times_cooked"] == 1


def test_undo_restores_the_previous_date(db, recipe_id):
    """The whole reason each cooking is a row. With only a count and one date,
    undoing would leave the date of the very thing that was removed."""
    store.log_cooked(db, recipe_id, when="2026-08-01T18:00:00")
    store.log_cooked(db, recipe_id, when="2026-09-01T18:00:00")
    assert store.undo_cooked(db, recipe_id)["last_cooked_at"] == "2026-08-01T18:00:00"


def test_undoing_the_last_one_leaves_it_on_neither_list(db, recipe_id):
    """A recipe made zero times is not one you have made. It does not go to
    "want to try" either: that was not what it was, and guessing would put it
    on a list nobody asked for."""
    store.log_cooked(db, recipe_id)
    recipe = store.undo_cooked(db, recipe_id)
    assert recipe["times_cooked"] == 0
    assert recipe["last_cooked_at"] is None
    assert recipe["status"] is None


def test_undo_leaves_a_want_to_try_alone(db, recipe_id):
    """Undoing a cooking says nothing about the other list."""
    store.log_cooked(db, recipe_id)
    store.undo_cooked(db, recipe_id)
    store.set_status(db, recipe_id, store.TODO)
    store.undo_cooked(db, recipe_id)
    assert store.get_recipe(db, recipe_id)["status"] == "todo"


def test_undoing_what_was_never_made_changes_nothing(db, recipe_id):
    recipe = store.undo_cooked(db, recipe_id)
    assert recipe["times_cooked"] == 0 and recipe["status"] is None


def test_undoing_a_missing_recipe_reports_nothing(db):
    assert store.undo_cooked(db, 999) is None


def test_the_count_and_the_log_cannot_drift(db, recipe_id):
    """times_cooked is a cache of the log, and one function writes it."""
    for _ in range(4):
        store.log_cooked(db, recipe_id)
    store.undo_cooked(db, recipe_id)
    logged = db.execute("SELECT COUNT(*) FROM cook_log WHERE recipe_id = ?",
                        (recipe_id,)).fetchone()[0]
    assert store.get_recipe(db, recipe_id)["times_cooked"] == logged == 3


# --- ratings ------------------------------------------------------------------


@pytest.mark.parametrize("rating", [1, 2, 3, 4, 5])
def test_a_rating_in_range_is_kept(db, recipe_id, rating):
    assert store.set_rating(db, recipe_id, rating)["rating"] == rating


@pytest.mark.parametrize("rating", [0, 6, -1, 100])
def test_a_rating_out_of_range_is_refused(db, recipe_id, rating):
    with pytest.raises(ValueError, match="1-5"):
        store.set_rating(db, recipe_id, rating)


def test_a_rating_can_be_cleared(db, recipe_id):
    store.set_rating(db, recipe_id, 4)
    assert store.set_rating(db, recipe_id, None)["rating"] is None


def test_rating_is_independent_of_having_cooked_it(db, recipe_id):
    """A dish you have made but not judged is normal, and so is knowing you
    will dislike something before you make it."""
    store.set_rating(db, recipe_id, 5)
    assert store.get_recipe(db, recipe_id)["status"] is None
    store.log_cooked(db, recipe_id)
    assert store.get_recipe(db, recipe_id)["rating"] == 5


def test_rating_something_that_is_not_there(db):
    assert store.set_rating(db, 999, 3) is None


# --- filtering ----------------------------------------------------------------


def _add(db, name, status=None):
    store.save_recipe(db, {"name": name, "category": "Family", "ingredients": []})
    recipe_id = db.execute("SELECT id FROM recipes WHERE name = ?", (name,)).fetchone()["id"]
    if status:
        store.set_status(db, recipe_id, status)
    return recipe_id


def test_the_lists_can_be_browsed_separately(db):
    _add(db, "To try one", store.TODO)
    _add(db, "To try two", store.TODO)
    _add(db, "Made it", store.COOKED)
    _add(db, "Neither")

    assert len(store.list_recipes(db, status="todo")) == 2
    assert len(store.list_recipes(db, status="cooked")) == 1
    assert len(store.list_recipes(db)) == 4


def test_an_unknown_status_filter_does_not_silently_empty_the_page(db):
    """It shows everything, which is what "All" means. Returning nothing would
    read as a catalogue that had lost its recipes."""
    _add(db, "Chicken curry")
    assert len(store.list_recipes(db, status="nonsense")) == 1


def test_the_status_filter_stacks_with_category(db):
    store.save_recipe(db, {"name": "Bulk one", "category": "Bulk", "ingredients": []})
    bulk = db.execute("SELECT id FROM recipes WHERE name = 'Bulk one'").fetchone()["id"]
    store.set_status(db, bulk, store.TODO)
    _add(db, "Family one", store.TODO)

    assert len(store.list_recipes(db, category="Bulk", status="todo")) == 1


def test_the_counts_feed_the_filter_chips(db):
    _add(db, "One", store.TODO)
    _add(db, "Two", store.TODO)
    _add(db, "Three", store.COOKED)
    counts = store.counts(db)
    assert counts["todo"] == 2 and counts["cooked"] == 1


# --- upgrading ----------------------------------------------------------------


def test_an_existing_catalogue_gains_the_columns(db):
    """Recipes imported before this release are on neither list and unrated,
    which is true of them — not zero stars, which would be a judgement nobody
    made."""
    db.execute("UPDATE recipes SET status = NULL")
    for column in ("status", "rating", "times_cooked", "last_cooked_at"):
        db.execute(f"UPDATE recipes SET {column} = NULL WHERE 0")
    store.save_recipe(db, {"name": "Old one", "category": "Family", "ingredients": []})
    schema.create(db)

    recipe = store.list_recipes(db)[0]
    assert recipe["status"] is None
    assert recipe["rating"] is None
    assert recipe["times_cooked"] == 0


def test_the_migration_runs_on_a_table_without_the_columns(db):
    db.execute("DROP TABLE recipes")
    db.execute("""CREATE TABLE recipes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        category TEXT NOT NULL, servings INTEGER, method TEXT, notes TEXT,
        protein_g REAL, kcal REAL, minutes INTEGER,
        source TEXT NOT NULL DEFAULT 'import',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(name, category))""")
    db.execute("INSERT INTO recipes (name, category, created_at, updated_at) "
               "VALUES ('Chicken curry', 'Family', '2026-08-01', '2026-08-01')")

    schema.create(db)

    columns = {row[1] for row in db.execute("PRAGMA table_info(recipes)")}
    assert {"status", "rating", "times_cooked", "last_cooked_at"} <= columns
    recipe_id = db.execute("SELECT id FROM recipes").fetchone()["id"]
    assert store.log_cooked(db, recipe_id)["times_cooked"] == 1


def test_an_existing_count_becomes_a_history_it_can_undo(db, recipe_id):
    """A database from before the log knows "made 7 times, last on 5 Sep" and
    nothing else. Without a backfill the count would be there and undo would
    have nothing to remove."""
    db.execute("UPDATE recipes SET times_cooked = 7, last_cooked_at = ?, status = 'cooked' "
               "WHERE id = ?", ("2026-09-05T18:00:00", recipe_id))
    db.execute("DELETE FROM cook_log")

    schema.create(db)

    assert store.get_recipe(db, recipe_id)["times_cooked"] == 7
    recipe = store.undo_cooked(db, recipe_id)
    assert recipe["times_cooked"] == 6
    # The six earlier dates were never recorded, so the honest answer is that
    # it was made six times and the last one is unknown — not a date invented
    # here, and not the day the cooking just removed happened on.
    assert recipe["last_cooked_at"] is None


def test_the_backfill_leaves_a_log_it_has_already_written_alone(db, recipe_id):
    """It runs on every start, the way the dedupe_key backfill does."""
    store.log_cooked(db, recipe_id)
    schema.create(db)
    schema.create(db)
    assert store.get_recipe(db, recipe_id)["times_cooked"] == 1


# --- through the endpoints ----------------------------------------------------


def _pack(name, category="Family"):
    """A pack the importer actually accepts. A recipe with no ingredients is
    rejected, and a test that imports nothing passes for the wrong reason."""
    import json
    return json.dumps({"recipes": [{
        "name": name, "category": category, "servings": 4,
        "ingredients": [{"name": "chicken", "shop_name": "kylling",
                         "amount": 600, "unit": "g"}]}]})


def _make(client, name="Chicken curry"):
    added = client.post("/api/import", json={"text": _pack(name)}).get_json()["added"]
    assert added == 1, "the pack must actually import or the test proves nothing"
    return client.get("/api/recipes").get_json()[0]["id"]


def test_the_whole_cycle_through_the_api(client):
    recipe_id = _make(client)

    on_list = client.post(f"/api/recipes/{recipe_id}/status",
                          json={"status": "todo"}).get_json()
    assert on_list["status"] == "todo"

    cooked = client.post(f"/api/recipes/{recipe_id}/cooked").get_json()
    assert cooked["status"] == "cooked" and cooked["times_cooked"] == 1

    rated = client.post(f"/api/recipes/{recipe_id}/rating", json={"rating": 4}).get_json()
    assert rated["rating"] == 4

    assert client.get("/api/summary").get_json()["counts"]["cooked"] == 1
    assert len(client.get("/api/recipes?status=cooked").get_json()) == 1


def test_undo_through_the_api(client):
    recipe_id = _make(client)
    for _ in range(3):
        client.post(f"/api/recipes/{recipe_id}/cooked")

    back = client.delete(f"/api/recipes/{recipe_id}/cooked").get_json()
    assert back["times_cooked"] == 2 and back["status"] == "cooked"

    for _ in range(2):
        back = client.delete(f"/api/recipes/{recipe_id}/cooked").get_json()
    assert back["times_cooked"] == 0 and back["status"] is None
    assert client.get("/api/summary").get_json()["counts"]["cooked"] == 0


def test_undoing_a_missing_recipe_is_a_404(client):
    assert client.delete("/api/recipes/999/cooked").status_code == 404


def test_a_bad_rating_is_a_400(client):
    recipe_id = _make(client)
    assert client.post(f"/api/recipes/{recipe_id}/rating", json={"rating": 9}).status_code == 400


def test_a_bad_status_is_a_400(client):
    recipe_id = _make(client)
    assert client.post(f"/api/recipes/{recipe_id}/status",
                       json={"status": "maybe"}).status_code == 400


@pytest.mark.parametrize("path", ["status", "cooked", "rating"])
def test_acting_on_a_missing_recipe_is_a_404(client, path):
    response = client.post(f"/api/recipes/999/{path}", json={"status": "todo", "rating": 3})
    assert response.status_code == 404


# --- the page -----------------------------------------------------------------


def _static(name):
    import os
    sub = "templates" if name.endswith(".html") else "static"
    with open(os.path.join(os.path.dirname(__file__), "..", sub, name),
              encoding="utf-8") as handle:
        return handle.read()


def test_the_list_filter_is_its_own_row():
    """Crossing it into the category chips would make "Family" and "Want to
    try" look mutually exclusive."""
    html = _static("index.html")
    assert 'id="status-filter"' in html
    assert html.index('id="category-filter"') < html.index('id="status-filter"')


def test_the_marks_do_not_compete_with_the_category_pill():
    """The pill already sits on the right of every row; a second one turns the
    list into a legend."""
    js = _static("app.js")
    fn = js[js.index("function statusMark("):js.index("function cookedLine(")]
    assert "mark-todo" in fn and "mark-cooked" in fn
    assert "pill" not in fn


def test_pressing_the_current_star_clears_the_rating():
    js = _static("app.js")
    assert "value === 0 || value === state.openRating ? null : value" in js


def test_marking_a_recipe_refreshes_the_list_underneath():
    """The row you just marked is visible behind the sheet, and leaving it
    stale is how you press the button twice."""
    js = _static("app.js")
    # Sliced to the next top-level listener, not to the first "});" — that one
    # closes a fetchJSON call several lines in, and cutting there reads as a
    # missing line rather than a bad boundary.
    handler = js[js.index('const rate = event.target.closest("[data-rate]")'):]
    handler = handler[:handler.index('el("prompt-kind")')]
    assert "loadRecipes()" in handler and "loadSummary()" in handler
