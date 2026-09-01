"""The database's shape, and nothing else.

Kept apart from the queries so the tables can be read in one screen without
scrolling past the code that uses them — and so a migration is an edit to one
file rather than an archaeology exercise.
"""
import sqlite3

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
)


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # Off by default in SQLite, and the ingredients/plan cascades are the whole
    # reason deleting a recipe does not leave orphans behind.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create(conn):
    for statement in TABLES + INDEXES:
        conn.execute(statement)
    conn.commit()
