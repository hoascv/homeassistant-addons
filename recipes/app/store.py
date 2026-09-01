"""Reading and writing recipes.

The only module that speaks SQL. Everything it returns is a plain dict, so the
domain modules never learn what a Row is and can be tested with literals.
"""
import datetime

import schema


def _now():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def init_db(path):
    conn = schema.connect(path)
    try:
        schema.create(conn)
    finally:
        conn.close()


# --- reading ------------------------------------------------------------------


def _ingredients(conn, recipe_id):
    rows = conn.execute(
        "SELECT name, shop_name, amount, unit, optional FROM ingredients "
        "WHERE recipe_id = ? ORDER BY position, id",
        (recipe_id,),
    )
    return [
        {"name": r["name"], "shop_name": r["shop_name"] or r["name"],
         "amount": r["amount"], "unit": r["unit"] or "",
         "optional": bool(r["optional"])}
        for r in rows
    ]


def _as_recipe(conn, row, with_ingredients=True):
    recipe = {
        "id": row["id"], "name": row["name"], "category": row["category"],
        "servings": row["servings"], "method": row["method"], "notes": row["notes"],
        "protein_g": row["protein_g"], "kcal": row["kcal"], "minutes": row["minutes"],
        "source": row["source"],
        # Survives a re-import: save_recipe updates updated_at and leaves
        # created_at alone, so "added" keeps meaning the first time it arrived
        # rather than the last time a pack was pasted over it.
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }
    if with_ingredients:
        recipe["ingredients"] = _ingredients(conn, row["id"])
    return recipe


def get_recipe(conn, recipe_id):
    row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    return _as_recipe(conn, row) if row else None


def list_recipes(conn, category=None, query=None, with_ingredients=False):
    """Recipes, optionally filtered. Ingredients are left off by default —
    a browse listing does not need them, and fetching them per row turns one
    query into a hundred."""
    sql = "SELECT * FROM recipes"
    where, params = [], []
    if category:
        where.append("category = ?")
        params.append(category)
    if query:
        where.append("(LOWER(name) LIKE ? OR LOWER(COALESCE(notes, '')) LIKE ?)")
        params += [f"%{query.lower()}%"] * 2
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY category, name"
    return [_as_recipe(conn, r, with_ingredients) for r in conn.execute(sql, params)]


def categories_in_use(conn):
    return [r["category"] for r in conn.execute(
        "SELECT DISTINCT category FROM recipes ORDER BY category")]


def counts(conn):
    return {
        "recipes": conn.execute("SELECT COUNT(*) AS n FROM recipes").fetchone()["n"],
        "ingredients": conn.execute("SELECT COUNT(*) AS n FROM ingredients").fetchone()["n"],
        "planned": conn.execute("SELECT COUNT(*) AS n FROM plan").fetchone()["n"],
    }


# --- writing ------------------------------------------------------------------


def save_recipe(conn, recipe, source="import"):
    """Insert or replace a recipe by (name, category).

    Replacing rather than duplicating: importing the same pack twice is a
    normal thing to do — a paste that half-worked is retried — and it should
    leave one copy, not two. The ingredients go with it, so an edited recipe
    does not keep the ones it no longer has.
    """
    name = (recipe.get("name") or "").strip()
    category = (recipe.get("category") or "").strip()
    if not name:
        raise ValueError("a recipe needs a name")
    if not category:
        raise ValueError("a recipe needs a category")

    now = _now()
    key = schema.key_text(name, category)
    # Matched on the normalised key, not the literal text. Where an older
    # database already holds several rows that normalise the same way, the most
    # recently touched one wins, so repeated imports converge on a single
    # recipe instead of adding to the pile.
    existing = conn.execute(
        "SELECT id, created_at FROM recipes WHERE dedupe_key = ? "
        "ORDER BY updated_at DESC, id DESC LIMIT 1",
        (key,),
    ).fetchone()

    values = (
        name, category, recipe.get("servings"), recipe.get("method"),
        recipe.get("notes"), recipe.get("protein_g"), recipe.get("kcal"),
        recipe.get("minutes"), source,
    )
    if existing:
        recipe_id = existing["id"]
        # Name and category are deliberately left as they are. Reaching here
        # means they already matched apart from case or spacing, so there is
        # nothing to learn from the incoming spelling — and taking it would let
        # one pack written in lower case rename the user's "Family" category to
        # "family" everywhere it appears.
        conn.execute(
            "UPDATE recipes SET servings=?, method=?, notes=?, "
            "protein_g=?, kcal=?, minutes=?, source=?, dedupe_key=?, updated_at=? WHERE id=?",
            values[2:] + (key, now, recipe_id),
        )
        conn.execute("DELETE FROM ingredients WHERE recipe_id = ?", (recipe_id,))
    else:
        cursor = conn.execute(
            "INSERT INTO recipes (name, category, servings, method, notes, protein_g, "
            "kcal, minutes, source, dedupe_key, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values + (key, now, now),
        )
        recipe_id = cursor.lastrowid

    for position, ingredient in enumerate(recipe.get("ingredients") or []):
        conn.execute(
            "INSERT INTO ingredients "
            "(recipe_id, position, name, shop_name, amount, unit, optional) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (recipe_id, position, ingredient["name"],
             ingredient.get("shop_name") or ingredient["name"],
             ingredient.get("amount"), ingredient.get("unit") or "",
             1 if ingredient.get("optional") else 0),
        )
    return recipe_id


def duplicate_groups(conn):
    """Recipes that normalise to the same name and category, grouped.

    Only ever reported, never merged on the user's behalf. Two recipes that
    look alike may genuinely differ — one edited with the ingredients actually
    used, one straight from a pack — and deleting the wrong one loses work that
    only exists here. The screen shows what it found and the keeper chooses.
    """
    rows = conn.execute(
        "SELECT dedupe_key FROM recipes GROUP BY dedupe_key HAVING COUNT(*) > 1"
    ).fetchall()

    groups = []
    for row in rows:
        members = conn.execute(
            "SELECT r.id, r.name, r.category, r.source, r.created_at, r.updated_at, "
            "(SELECT COUNT(*) FROM ingredients WHERE recipe_id = r.id) AS ingredient_count "
            "FROM recipes r WHERE r.dedupe_key = ? "
            "ORDER BY r.updated_at DESC, r.id DESC",
            (row["dedupe_key"],),
        ).fetchall()
        groups.append({
            # The first is the one "keep newest" would keep, so the screen can
            # mark it without repeating the ordering rule in JavaScript.
            "name": members[0]["name"],
            "category": members[0]["category"],
            "recipes": [dict(member) for member in members],
        })
    groups.sort(key=lambda group: (group["category"].casefold(), group["name"].casefold()))
    return groups


def delete_recipe(conn, recipe_id):
    conn.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))


def save_many(conn, recipes, source="import"):
    """Store a batch, reporting what landed and what could not.

    One bad recipe does not lose the rest: the pack came from an assistant and
    arriving nine-tenths intact is worth much more than a rejection, since the
    user's only remedy would be to go and ask again.
    """
    added, warnings = 0, []
    for recipe in recipes:
        try:
            save_recipe(conn, recipe, source=source)
            added += 1
        except (ValueError, KeyError) as exc:
            warnings.append(f"skipped {recipe.get('name') or 'an unnamed recipe'}: {exc}")
    return added, warnings


# --- app state ----------------------------------------------------------------


def get_state(conn, key, default=None):
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(conn, key, value):
    conn.execute(
        "INSERT INTO app_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
