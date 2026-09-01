"""Putting the base recipes in, and keeping out of the way afterwards.

The add-on ships with recipes so it is useful before anyone has been near an
assistant. The rule that makes that safe: **seeds are only ever written once,
and never overwrite anything the user touched.**

Re-seeding on every start would undo an edit the moment the add-on restarted,
which is a worse failure than shipping nothing — you would lose work and not
know why. So it happens once, recorded in app_state, and a user who deletes a
seeded recipe has deleted it for good rather than fighting it back every reboot.
"""
import seed_bulk
import seed_family
import store

_SEEDED_FLAG = "seeded_version"

# Bumping this re-seeds *only what is missing* on the next start, so a release
# can add recipes without resurrecting ones the user deliberately removed.
SEED_VERSION = "1"


def _tagged(recipes, category):
    for recipe in recipes:
        yield {**recipe, "category": category}


def base_recipes(family_category, bulk_category):
    """Every shipped recipe, tagged with the configured category names.

    The categories are configurable, so the seeds cannot hard-code them —
    a household that renamed "Bulk" to "Træning" should still get the recipes.
    """
    return [
        *_tagged(seed_family.RECIPES, family_category),
        *_tagged(seed_bulk.MEALS, bulk_category),
        *_tagged(seed_bulk.SNACKS, bulk_category),
    ]


def seed_if_needed(conn, family_category, bulk_category):
    """Write the base recipes on first run. Returns how many were added.

    Existing names are left alone rather than replaced: on a bumped
    SEED_VERSION this adds what is new and touches nothing else.
    """
    if store.get_state(conn, _SEEDED_FLAG) == SEED_VERSION:
        return 0

    existing = {
        (r["name"].strip().lower(), r["category"].strip().lower())
        for r in store.list_recipes(conn)
    }
    added = 0
    for recipe in base_recipes(family_category, bulk_category):
        key = (recipe["name"].strip().lower(), recipe["category"].strip().lower())
        if key in existing:
            continue
        store.save_recipe(conn, recipe, source="seed")
        added += 1

    store.set_state(conn, _SEEDED_FLAG, SEED_VERSION)
    conn.commit()
    return added
