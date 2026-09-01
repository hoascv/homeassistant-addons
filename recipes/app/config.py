"""Reading the add-on's options.

Its own module so that everything else can be handed plain values rather than
reaching for a file. That is what lets shopping.py and prompts.py be pure: they
take a list of staples or a category name, not a config object they would have
to stub.
"""
import json

OPTIONS_PATH = "/data/options.json"

DEFAULTS = {
    "categories": "Family, Bulk",
    "default_servings": 4,
    "staples": "salt, peber, olivenolie, rapsolie, hvedemel, sukker, bagepulver, eddike",
    "restrict_to_user_ids": "",
}


def _read(path=None):
    try:
        with open(path or OPTIONS_PATH) as handle:
            loaded = json.load(handle)
    except (OSError, ValueError):
        # First boot, and every dev run outside Supervisor. Defaults rather
        # than a crash: an add-on that will not start is worse than one
        # running on the values it shipped with.
        return dict(DEFAULTS)
    return {**DEFAULTS, **(loaded if isinstance(loaded, dict) else {})}


def split_list(raw):
    """A comma or newline separated setting, cleaned.

    Order is kept, blanks dropped, duplicates removed case-insensitively — a
    trailing comma is the normal state of a half-edited setting and should not
    produce an empty entry.
    """
    if not raw:
        return []
    out, seen = [], set()
    for part in str(raw).replace("\n", ",").split(","):
        item = part.strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out


def load(path=None):
    raw = _read(path)
    categories = split_list(raw.get("categories")) or split_list(DEFAULTS["categories"])
    try:
        servings = max(1, min(12, int(raw.get("default_servings", 4))))
    except (TypeError, ValueError):
        servings = 4
    return {
        "categories": categories,
        # The two the seeds go into. First is the family one, second the bulk
        # one; with only one configured both land there rather than the seeding
        # failing over a setting.
        "family_category": categories[0],
        "bulk_category": categories[1] if len(categories) > 1 else categories[0],
        "default_servings": servings,
        "staples": split_list(raw.get("staples")),
        "allowed_user_ids": set(split_list(raw.get("restrict_to_user_ids"))),
    }
