"""Reading the reply an assistant gave back.

The only ingestion path, so it is the one place that has to be genuinely
forgiving. What arrives here was produced by whatever assistant the user had to
hand, pasted through whatever clipboard: wrapped in fences, prefixed with
"Here's your recipes!", with half-remembered field names and a stray trailing
comma.

The governing choice is the same one the Knowledge add-on made: **report what
had to be dropped rather than failing the whole paste**. A pack that lands four
of five recipes and says so is worth far more than a parse error, because the
user's only other remedy is to go and ask an assistant again.

Pure — no database, no Flask. It turns text into dicts and a list of warnings.
"""
import json
import re

import units

# First alias present wins. Assistants reliably produce the right shape and
# unreliably produce the right names for it.
_ALIASES = {
    "recipes": ("recipes", "items", "data", "results", "opskrifter"),
    "name": ("name", "title", "navn", "recipe"),
    "category": ("category", "kategori", "type", "group"),
    "servings": ("servings", "serves", "portioner", "portions", "yield"),
    "ingredients": ("ingredients", "ingredienser", "items"),
    "method": ("method", "steps", "instructions", "fremgangsmåde", "directions"),
    "notes": ("notes", "note", "description", "beskrivelse", "summary"),
    "amount": ("amount", "quantity", "qty", "mængde", "value"),
    "unit": ("unit", "units", "enhed", "measure"),
    "shop_name": ("shop_name", "danish", "da", "dansk", "shop", "label", "indkøbsnavn"),
    "minutes": ("minutes", "time", "tid", "prep_minutes", "total_minutes"),
    "protein_g": ("protein_g", "protein", "protein_grams"),
    "kcal": ("kcal", "calories", "energy", "kalorier"),
}


class PackError(ValueError):
    """The paste could not be read as a pack at all."""


def _get(obj, field, default=None):
    if not isinstance(obj, dict):
        return default
    for alias in _ALIASES[field]:
        if alias in obj and obj[alias] not in (None, ""):
            return obj[alias]
    return default


def _text(value, limit=8000):
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        value = "\n".join(str(v).strip() for v in value if v is not None)
    return str(value).strip()[:limit] or None


def _number(value):
    """A number, or None. Strips units an assistant left glued on ("600 g")."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:[.,]\d+)?", str(value))
    return float(match.group(0).replace(",", ".")) if match else None


def extract_json(text):
    """The first complete JSON object in `text`.

    Tries the cheap readings first, then scans for a balanced object — which is
    what survives an assistant writing two paragraphs either side of the pack,
    and that is the common case rather than the exception.
    """
    if not text or not text.strip():
        raise PackError("nothing was pasted")
    stripped = text.strip()

    for candidate in (stripped, _fenced(stripped)):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except ValueError:
            pass

    start = stripped.find("{")
    while start != -1:
        depth, in_string, escaped = 0, False, False
        for index in range(start, len(stripped)):
            char = stripped[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(stripped[start:index + 1])
                        if isinstance(parsed, dict):
                            return parsed
                    except ValueError:
                        break
        start = stripped.find("{", start + 1)
    raise PackError("no JSON object found in what was pasted")


def _fenced(text):
    match = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    return match.group(1).strip() if match else None


def _ingredient(raw, warnings, where):
    """One ingredient line, or None if there is nothing usable in it."""
    if isinstance(raw, str):
        name, shop_name, amount, unit = _text(raw, 200), None, None, ""
    else:
        name = _text(_get(raw, "name"), 200)
        shop_name = _text(_get(raw, "shop_name"), 200)
        amount = _number(_get(raw, "amount"))
        unit = units.normalise_unit(_get(raw, "unit") or "")

    if not name:
        warnings.append(f"{where}: skipped an ingredient with no name")
        return None
    if unit and units.dimension(unit) is None:
        # Kept, not dropped: an unfamiliar unit still names something to buy,
        # and it simply will not be merged with anything.
        warnings.append(f"{where}: kept {name!r} with an unrecognised unit {unit!r}")
    if not shop_name:
        # No Danish label given. The English name stands in rather than the
        # ingredient being dropped — a shopping list with one untranslated line
        # is still a shopping list, and the alternative is losing the recipe.
        warnings.append(f"{where}: no Danish name for {name!r}, using the English one")
        shop_name = name
    return {
        "name": name,
        "shop_name": shop_name,
        "amount": amount,
        "unit": unit,
        "optional": bool(raw.get("optional")) if isinstance(raw, dict) else False,
    }


def normalise(raw, default_category=None):
    """Turn a parsed pack into recipes this app can store.

    Never raises for content problems — anything unusable is dropped and
    described. It raises only when there is no usable pack at all, which is the
    one case where the user must go back to the assistant.
    """
    if not isinstance(raw, dict):
        raise PackError("the pasted JSON is not an object")

    entries = _get(raw, "recipes")
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        # A bare single recipe, which assistants produce when asked for one.
        entries = [raw] if _get(raw, "name") else []

    warnings, recipes = [], []
    for index, entry in enumerate(entries, start=1):
        name = _text(_get(entry, "name"), 200)
        if not name:
            warnings.append(f"skipped recipe {index} (no name)")
            continue

        category = _text(_get(entry, "category"), 80) or default_category
        if not category:
            warnings.append(f"skipped {name!r} (no category, and none chosen)")
            continue

        ingredients = []
        for raw_ingredient in _get(entry, "ingredients") or []:
            parsed = _ingredient(raw_ingredient, warnings, name)
            if parsed:
                ingredients.append(parsed)

        if not ingredients:
            warnings.append(f"skipped {name!r} (no usable ingredients)")
            continue

        recipes.append({
            "name": name,
            "category": category,
            "servings": int(_number(_get(entry, "servings")) or 0) or None,
            "minutes": int(_number(_get(entry, "minutes")) or 0) or None,
            "protein_g": _number(_get(entry, "protein_g")),
            "kcal": _number(_get(entry, "kcal")),
            "notes": _text(_get(entry, "notes"), 1000),
            "method": _text(_get(entry, "method")),
            "ingredients": ingredients,
        })

    if not recipes:
        raise PackError(
            "no usable recipes in that paste"
            + (f" — {warnings[0]}" if warnings else "")
        )
    return {"recipes": recipes, "warnings": warnings}
