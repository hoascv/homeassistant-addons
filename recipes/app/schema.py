"""The database's shape, and nothing else.

Kept apart from the queries so the tables can be read in one screen without
scrolling past the code that uses them — and so a migration is an edit to one
file rather than an archaeology exercise.
"""
import sqlite3

def dedupe_key(name, category):
    """The form two recipe names are compared in.

    `UNIQUE(name, category)` catches an exact re-import, which is the case it
    was written for, and nothing else. Every realistic variation slipped past
    it: a pack pasted from an assistant says "Chicken Curry" one time and
    "Chicken curry" the next, and both sat in the catalogue as separate dishes.

    Case-folded rather than lowercased — casefold is the one that handles the
    non-ASCII a Danish catalogue is full of, so "GRØD" and "grød" agree. Runs
    of whitespace collapse to one, because a double space is a typo and not a
    different recipe.

    The display name is stored exactly as written. This is only the key they
    are matched on.
    """
    return (" ".join(str(name or "").split()).casefold(),
            " ".join(str(category or "").split()).casefold())


def key_text(name, category):
    """`dedupe_key` as one string, for the stored column. Tab-separated so a
    name ending in a space cannot collide with a category beginning with one."""
    name_key, category_key = dedupe_key(name, category)
    return f"{name_key}\t{category_key}"


TABLES = (
    """
    CREATE TABLE IF NOT EXISTS recipes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        -- Free text, not an enum. The two shipped categories are only the two
        -- this seeds with; a household wanting "Hurtig" or "Gæster" should not
        -- need a migration to get one.
        category TEXT NOT NULL,
        servings INTEGER,
        method TEXT,
        notes TEXT,
        -- Optional, and blank means unknown rather than zero. Protein is here
        -- because it is the figure that decides one bulk meal over another;
        -- the rest of the macros are not, because they would be invented.
        protein_g REAL,
        kcal REAL,
        minutes INTEGER,
        -- Where it came from: 'seed' for what ships, 'import' for a pasted
        -- pack. Lets the seeds be replaced on upgrade without touching
        -- anything the user brought in themselves.
        source TEXT NOT NULL DEFAULT 'import',
        -- Name and category, case-folded with whitespace collapsed. What a
        -- save matches on, because UNIQUE(name, category) below only ever
        -- caught an exact re-import: a pack pasted twice from an assistant
        -- says "Chicken Curry" one time and "Chicken curry" the next, and
        -- both sat here as separate dishes. See store.dedupe_key.
        --
        -- Not UNIQUE itself. A catalogue that already contains duplicates must
        -- still open, and merging them is the keeper's decision rather than
        -- something an upgrade does quietly to rows it never showed them.
        dedupe_key TEXT,
        -- NULL, 'todo' or 'cooked'. Mutually exclusive on purpose: a recipe is
        -- either one you mean to try or one you have made, and a thing that is
        -- both is really just the second.
        status TEXT,
        rating INTEGER,  -- 1-5, or NULL for not rated. Kept apart from status:
                         -- a dish you have cooked but not judged is normal.
        -- Cooking is an event, not a flag. Counting it is what makes this a
        -- record of what the household actually eats rather than a checkbox —
        -- "we make this every other week" is the useful fact, and a boolean
        -- cannot say it.
        times_cooked INTEGER NOT NULL DEFAULT 0,
        last_cooked_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(name, category)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ingredients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
        position INTEGER NOT NULL DEFAULT 0,
        -- What the recipe calls it, in English, for reading while cooking.
        name TEXT NOT NULL,
        -- What the shelf label calls it, in Danish, for finding it in the shop.
        -- The shopping list is built from this one; the recipe is read from the
        -- other. Falls back to `name` when a pack gave only one, so a recipe is
        -- never lost over a missing translation.
        shop_name TEXT,
        -- Null is legitimate: "smør til stegning" has no quantity, and storing
        -- a zero would put "0 g smør" on a shopping list.
        amount REAL,
        unit TEXT,
        optional INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS plan (
        recipe_id INTEGER PRIMARY KEY REFERENCES recipes(id) ON DELETE CASCADE,
        servings INTEGER NOT NULL,
        added_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ticks (
        -- The shopping list is derived, so a tick cannot reference a row id.
        -- It keys on the merged line's own identity, which survives the list
        -- being rebuilt after a recipe is added.
        item_key TEXT PRIMARY KEY,
        ticked_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cook_log (
        -- One row per time it was made. recipes.times_cooked and
        -- recipes.last_cooked_at are caches of COUNT and MAX over this table,
        -- kept because the list view reads them on every row.
        --
        -- The log exists so that undoing a mis-press has something to undo:
        -- with only a count and a date, removing the most recent cooking
        -- leaves the date of the very thing that was removed sitting there as
        -- "last on".
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
        -- Nullable, and null means "this happened, the date was not recorded".
        -- Only backfilled rows have it: a database from before the log knew
        -- one date for seven cookings, and inventing the other six would put
        -- dates in the record that nobody ever entered.
        cooked_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_state (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """,
)

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_ingredients_recipe ON ingredients(recipe_id, position)",
    "CREATE INDEX IF NOT EXISTS idx_recipes_category ON recipes(category, name)",
    "CREATE INDEX IF NOT EXISTS idx_recipes_source ON recipes(source)",
    "CREATE INDEX IF NOT EXISTS idx_recipes_dedupe ON recipes(dedupe_key)",
    "CREATE INDEX IF NOT EXISTS idx_recipes_status ON recipes(status, name)",
    "CREATE INDEX IF NOT EXISTS idx_cook_log_recipe ON cook_log(recipe_id, id)",
)


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # Off by default in SQLite, and the ingredients/plan cascades are the whole
    # reason deleting a recipe does not leave orphans behind.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create(conn):
    for statement in TABLES:
        conn.execute(statement)
    _migrate(conn)
    for statement in INDEXES:
        conn.execute(statement)
    conn.commit()


def _migrate(conn):
    """Bring an existing database up to the current shape.

    CREATE TABLE IF NOT EXISTS does nothing to a table that already exists, so
    a column added after the first release has to arrive this way or every
    query naming it fails on an install that has been running for weeks.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(recipes)")}
    if "dedupe_key" not in columns:
        conn.execute("ALTER TABLE recipes ADD COLUMN dedupe_key TEXT")
    for column, definition in (
        ("status", "TEXT"),
        ("rating", "INTEGER"),
        ("times_cooked", "INTEGER NOT NULL DEFAULT 0"),
        ("last_cooked_at", "TEXT"),
    ):
        if column not in columns:
            conn.execute(f"ALTER TABLE recipes ADD COLUMN {column} {definition}")
    # Backfilled every start rather than once: rows written by an older version
    # while this one was not running would otherwise have no key and be
    # invisible to both the duplicate check and the save path.
    # Backfilled in Python, not SQL. SQLite's trim() removes the padding but
    # cannot collapse an internal double space, so a SQL backfill would write a
    # weaker key than key_text() produces — and "Chicken  curry" would then
    # neither be reported as a duplicate nor be matched by a later import.
    for row in conn.execute(
        "SELECT id, name, category FROM recipes WHERE dedupe_key IS NULL"
    ).fetchall():
        conn.execute("UPDATE recipes SET dedupe_key = ? WHERE id = ?",
                     (key_text(row[1], row[2]), row[0]))
    _backfill_cook_log(conn)


def _backfill_cook_log(conn):
    """Give an existing count a history, so it can be undone one at a time.

    A database from before the log knows only "made 7 times, last on 5 Sep".
    That becomes six rows with no date and one carrying the date it does know,
    because the six earlier dates were never recorded and inventing them would
    put entries in the record nobody entered. Undoing back past the newest then
    honestly reads "Made 6 times." with no date rather than naming the day the
    cooking that was just removed happened on.

    Runs on every start and only for recipes with no rows yet, the way the
    dedupe_key backfill does: a row written by an older version while this one
    was not running would otherwise keep a count the log cannot account for.
    """
    rows = conn.execute(
        "SELECT r.id, r.times_cooked, r.last_cooked_at FROM recipes r "
        "WHERE r.times_cooked > 0 "
        "  AND NOT EXISTS (SELECT 1 FROM cook_log c WHERE c.recipe_id = r.id)"
    ).fetchall()
    for recipe_id, times, last in rows:
        conn.executemany(
            "INSERT INTO cook_log (recipe_id, cooked_at) VALUES (?, ?)",
            [(recipe_id, None)] * (times - 1) + [(recipe_id, last)],
        )
