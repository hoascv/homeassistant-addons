"""The prompt you take to an LLM.

This add-on never goes online. It writes a prompt, you run it wherever you have
a connection, and you paste the reply back — the same arrangement the Knowledge
add-on uses, and for the same reason: the useful part of an assistant is the
text it produces, not a live connection from a box in your hall.

The prompt is built here, as a pure function of the request, so what is asked
for can be read and changed in one place rather than being spread through the
page that renders it.
"""

_SCHEMA = """{
  "recipes": [
    {
      "name": "Chicken curry",
      "category": "%(category)s",
      "servings": 4,
      "minutes": 35,
      "protein_g": 42,
      "kcal": 610,
      "notes": "One line on why this one is worth making.",
      "ingredients": [
        {"name": "chicken breast", "shop_name": "kyllingebryst", "amount": 600, "unit": "g"},
        {"name": "onions", "shop_name": "løg", "amount": 2, "unit": "stk"},
        {"name": "coconut milk", "shop_name": "kokosmælk", "amount": 400, "unit": "ml"},
        {"name": "fresh coriander", "shop_name": "frisk koriander", "amount": 1,
         "unit": "bundt", "optional": true}
      ],
      "method": "1. ...\\n2. ...\\n3. ..."
    }
  ]
}"""

_RULES = """Rules that matter for this app:

- Reply with **JSON only**, matching the shape above. Fences are fine.
- **The recipe is in English.** `name`, `notes` and `method` are English, because
  that is what gets read while cooking.
- **Every ingredient needs both names.** `name` is English, for the recipe.
  `shop_name` is the **Danish supermarket shelf label**, for the shopping list —
  what is actually written on the shelf: "hakket oksekød 4-7%", "skyr naturel",
  "rugbrød", "havregryn", "flåede tomater". Not a brand, and not a translation
  that no Danish shop would use.
- Units must be one of: g, kg, ml, dl, l, stk, fed, bundt, tsk, spsk, pakke,
  dåse, pose, skive, knsp. Leave `amount` out entirely when a quantity makes no
  sense ("smør til stegning") rather than inventing one.
- `servings` is how many the quantities feed. The app scales from it, so it has
  to match the amounts given.
- `protein_g` and `kcal` are **per serving** and optional. Leave them out rather
  than estimating — a number that is guessed is worse than a blank, because it
  looks like it was measured.
- Mark an ingredient `"optional": true` only if the dish genuinely works
  without it.
- `method` is numbered steps, one per line."""


def _counts_line(count):
    return f"Give me {count} recipe{'s' if count != 1 else ''}."


def new_recipes_prompt(category, count=5, theme=None, avoid=()):
    """Ask for a fresh batch in one category."""
    parts = [
        f"You are helping me fill a home recipe catalog. {_counts_line(count)}",
        f"They go in the category **{category}**.",
    ]
    if theme:
        parts.append(f"Theme: {theme}.")
    parts.append(
        "They should be things a household in Denmark can cook on a weeknight "
        "from ingredients a normal Danish supermarket (Netto, Føtex, Rema 1000) "
        "actually stocks."
    )
    if avoid:
        listed = ", ".join(sorted(avoid)[:40])
        parts.append(
            "I already have these, so pick different ones: " + listed + "."
        )
    parts.append("Return exactly this shape:\n\n```json\n"
                 + _SCHEMA % {"category": category} + "\n```")
    parts.append(_RULES)
    return "\n\n".join(parts)


def snacks_prompt(category, count=8):
    """Snacks are asked for differently: small, no real cooking, and the
    quantities are per portion rather than per meal."""
    return "\n\n".join([
        f"You are helping me fill a home recipe catalog. {_counts_line(count)}",
        f"They go in the category **{category}**, and they should be **healthy "
        "snacks** rather than meals — no more than a few minutes of work, no "
        "oven, and things that survive being made in advance.",
        "Ingredients from a normal Danish supermarket, named as the shelf "
        "labels name them.",
        "Return exactly this shape:\n\n```json\n" + _SCHEMA % {"category": category} + "\n```",
        _RULES,
    ])


def more_like_prompt(recipe_name, category, count=3):
    """Ask for neighbours of one that worked."""
    return "\n\n".join([
        f"I make **{recipe_name}** and would like more like it. {_counts_line(count)}",
        f"They go in the category **{category}**. Same sort of effort and the "
        "same sort of ingredients — a Danish supermarket, a weeknight.",
        "Return exactly this shape:\n\n```json\n" + _SCHEMA % {"category": category} + "\n```",
        _RULES,
    ])
