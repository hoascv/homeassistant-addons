"""Turning a set of chosen recipes into one supermarket list.

Pure: this takes plain dicts and returns plain dicts. It never opens a database
and never imports Flask, which is the point — every interesting decision a
shopping list makes (scale, merge, group, flag) lives here, and all of it is
testable by calling one function with a literal.

The whole job is three steps:

    scale   each recipe's ingredients to the servings actually wanted
    merge   lines that are the same purchase
    group   by supermarket section, in walking order

Everything here works from `shop_name` — the Danish shelf label — not from the
English name the recipe is read in. Two recipes calling for "minced beef" and
"beef mince" are one purchase because both point at *hakket oksekød*, and the
list has to say what is written on the shelf or it does not help in the shop.
"""
import aisles
import units


def _scaled_lines(entry):
    """One planned recipe's ingredients, scaled to the servings wanted.

    A recipe written for 4 that you want for 6 needs its quantities multiplied,
    and a recipe with no stated servings is taken at face value rather than
    scaled by a guess.
    """
    recipe = entry["recipe"]
    base = recipe.get("servings") or 0
    wanted = entry.get("servings") or base or 0
    factor = (wanted / base) if base and wanted else 1.0

    for ingredient in recipe.get("ingredients", []):
        yield {
            # The Danish shelf label is the identity here. The English name
            # rides along only so a list can say which recipe wanted it.
            "name": ingredient.get("shop_name") or ingredient["name"],
            "recipe_name": ingredient["name"],
            "amount": units.scale(ingredient.get("amount"), factor),
            "unit": ingredient.get("unit") or "",
            "optional": bool(ingredient.get("optional")),
            "recipe": recipe.get("name"),
        }


def _merge(lines):
    """Combine lines that are the same purchase.

    Keyed on name and *dimension*, so 500 g and 1 kg of the same thing become
    one line while 3 fed and 1 bundt stay two. An amount of None — "smør til
    stegning" — makes the whole merged line amountless rather than inventing a
    total from the halves that did have numbers.
    """
    merged = {}
    for line in lines:
        key = units.merge_key(line["name"], line["unit"])
        base, dim = units.to_base(line["amount"], line["unit"])

        item = merged.get(key)
        if item is None:
            merged[key] = {
                "name": line["name"],
                "base": base,
                "dimension": dim,
                "unit": line["unit"],
                "amount_known": base is not None,
                "optional": line["optional"],
                "recipes": [line["recipe"]],
                "as_written": [line["recipe_name"]],
            }
            continue

        if base is None or not item["amount_known"]:
            item["amount_known"] = False
            item["base"] = None
        else:
            item["base"] += base
        # Optional only survives while every contributor said so: an ingredient
        # one recipe can do without and another cannot is not optional.
        item["optional"] = item["optional"] and line["optional"]
        if line["recipe"] not in item["recipes"]:
            item["recipes"].append(line["recipe"])
        if line["recipe_name"] not in item["as_written"]:
            item["as_written"].append(line["recipe_name"])
    return list(merged.values())


def _present(item, staples):
    """A merged line as the page shows it."""
    amount, unit = units.describe(item["base"], item["dimension"], item["unit"])
    name = item["name"]
    return {
        "name": name,
        "amount": amount,
        "amount_text": units.format_amount(amount),
        "unit": unit,
        # Shown but greyed: a staple you have run out of is still something you
        # need, and hiding it means noticing in the shop rather than at home.
        "staple": name.strip().lower() in staples,
        "optional": item["optional"],
        "recipes": item["recipes"],
        # What the recipes called it, so a Danish label you do not recognise
        # can still be tied back to the English line that asked for it.
        "as_written": item["as_written"],
        "section": aisles.section_for(name),
    }


def build_list(entries, staples=()):
    """The supermarket list for a set of planned recipes.

    `entries` is [{"recipe": {...}, "servings": n}]; `staples` is the names
    always in the cupboard. Returns sections in walking order, each with its
    items sorted by name, plus the counts a header can show.
    """
    staple_set = {s.strip().lower() for s in staples if s and s.strip()}

    lines = []
    for entry in entries:
        lines.extend(_scaled_lines(entry))
    items = [_present(item, staple_set) for item in _merge(lines)]

    by_section = {}
    for item in items:
        by_section.setdefault(item["section"], []).append(item)

    sections = [
        {"section": name, "items": sorted(rows, key=lambda i: i["name"].lower())}
        for name, rows in sorted(by_section.items(), key=lambda kv: aisles.sort_key(kv[0]))
    ]
    return {
        "sections": sections,
        "total_items": len(items),
        # Stated separately so a header can say "18 items, 4 of them staples"
        # rather than a bare count that quietly includes things you own.
        "staple_items": sum(1 for i in items if i["staple"]),
        "recipes": len(entries),
    }
