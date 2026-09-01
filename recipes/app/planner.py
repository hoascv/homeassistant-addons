"""The plan — which recipes are on this week's list — and its tick state.

Separate from store.py because it answers a different question. store.py owns
the catalog, which is durable; this owns a working set that is emptied and
refilled every shop, and the two have no reason to change together.
"""
import datetime
import hashlib

import shopping
import store


def _now():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def item_key(name, unit):
    """A stable identity for a merged shopping line.

    The list is derived, so a tick cannot reference a row id — the row does not
    exist. Hashing name and unit means a tick survives the list being rebuilt
    when another recipe is added, which is the whole point: ticking off six
    things and then remembering the bread should not clear the six.
    """
    raw = f"{name.strip().lower()}|{(unit or '').strip().lower()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# --- the plan -----------------------------------------------------------------


def add(conn, recipe_id, servings):
    conn.execute(
        "INSERT INTO plan (recipe_id, servings, added_at) VALUES (?, ?, ?) "
        "ON CONFLICT(recipe_id) DO UPDATE SET servings = excluded.servings",
        (recipe_id, max(1, int(servings)), _now()),
    )


def remove(conn, recipe_id):
    conn.execute("DELETE FROM plan WHERE recipe_id = ?", (recipe_id,))


def clear(conn):
    """Empty the plan and its ticks together.

    Leaving the ticks behind would mean next week's list opened with items
    already crossed off from a shop that happened days ago.
    """
    conn.execute("DELETE FROM plan")
    conn.execute("DELETE FROM ticks")


def entries(conn):
    """What is planned, as shopping.build_list wants it."""
    rows = conn.execute(
        "SELECT p.recipe_id, p.servings FROM plan p "
        "JOIN recipes r ON r.id = p.recipe_id ORDER BY r.category, r.name"
    ).fetchall()
    out = []
    for row in rows:
        recipe = store.get_recipe(conn, row["recipe_id"])
        if recipe:
            out.append({"recipe": recipe, "servings": row["servings"]})
    return out


# --- ticks --------------------------------------------------------------------


def set_tick(conn, key, ticked):
    if ticked:
        conn.execute(
            "INSERT INTO ticks (item_key, ticked_at) VALUES (?, ?) "
            "ON CONFLICT(item_key) DO NOTHING",
            (key, _now()),
        )
    else:
        conn.execute("DELETE FROM ticks WHERE item_key = ?", (key,))


def ticked_keys(conn):
    return {r["item_key"] for r in conn.execute("SELECT item_key FROM ticks")}


# --- the list -----------------------------------------------------------------


def shopping_list(conn, staples=()):
    """The current plan as a supermarket list, with ticks applied.

    The list is built fresh every time rather than stored. A stored list would
    drift from the recipes behind it the moment one was edited, and the whole
    computation is a few hundred dict operations.
    """
    result = shopping.build_list(entries(conn), staples=staples)
    ticked = ticked_keys(conn)

    remaining = 0
    for section in result["sections"]:
        for item in section["items"]:
            item["key"] = item_key(item["name"], item["unit"])
            item["ticked"] = item["key"] in ticked
            if not item["ticked"]:
                remaining += 1
    result["ticked_items"] = result["total_items"] - remaining
    result["remaining_items"] = remaining
    return result
