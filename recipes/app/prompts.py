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


# Enough to steer a batch, few enough that the model can honour them all. Past
# a dozen the request stops being "build around these" and becomes a list the
# model quietly picks from, which reads as it ignoring half of what was asked.
MAX_KEYWORDS = 12
MAX_KEYWORD_LENGTH = 40


def parse_keywords(text):
    """Free text into a clean list of keywords.

    Split on commas and newlines only, never spaces: "minced beef" and "sweet
    potato" are single ingredients, and splitting them would ask for two things
    that are not ingredients at all.

    Deduplicated case-insensitively, keeping the first spelling — someone
    typing "Chicken, chicken breast, chicken" means two things, not three.
    """
    if not text:
        return []
    pieces = str(text).replace("\n", ",").split(",")
    out, seen = [], set()
    for piece in pieces:
        word = " ".join(piece.split())[:MAX_KEYWORD_LENGTH]
        if not word or word.casefold() in seen:
            continue
        seen.add(word.casefold())
        out.append(word)
        if len(out) == MAX_KEYWORDS:
            break
    return out


def _keywords_line(keywords):
    """How the keywords are asked for.

    Two sentences rather than one, because they carry different instructions
    and a model given only the first tends to put every ingredient in every
    dish: each recipe leans on at least one, and the batch between them covers
    the lot. That is what "give me chicken and broccoli recipes" means to a
    person and it is worth saying explicitly.
    """
    if not keywords:
        return None
    listed = ", ".join(keywords)
    if len(keywords) == 1:
        return (f"Build them around **{listed}** — it should be the main "
                "ingredient in each one, not a garnish.")
    return (f"Build them around these: **{listed}**. Each recipe should lean on "
            "at least one of them as a main ingredient rather than a garnish, "
            "and between them the batch should cover all of them.")


def _counts_line(count):
    return f"Give me {count} recipe{'s' if count != 1 else ''}."


def new_recipes_prompt(category, count=5, keywords=(), avoid=()):
    """Ask for a fresh batch in one category.

    `keywords` are the main ingredients to build around — the thing you
    actually have in the fridge, or the protein you want a week of.
    """
    parts = [
        f"You are helping me fill a home recipe catalog. {_counts_line(count)}",
        f"They go in the category **{category}**.",
    ]
    # Before the supermarket line, not after: it is the strongest constraint in
    # the prompt and the one most worth reading first.
    keyword_line = _keywords_line(keywords)
    if keyword_line:
        parts.append(keyword_line)
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


def snacks_prompt(category, count=8, keywords=()):
    """Snacks are asked for differently: small, no real cooking, and the
    quantities are per portion rather than per meal."""
    parts = [
        f"You are helping me fill a home recipe catalog. {_counts_line(count)}",
        f"They go in the category **{category}**, and they should be **healthy "
        "snacks** rather than meals — no more than a few minutes of work, no "
        "oven, and things that survive being made in advance.",
    ]
    keyword_line = _keywords_line(keywords)
    if keyword_line:
        parts.append(keyword_line)
    parts += [
        "Ingredients from a normal Danish supermarket, named as the shelf "
        "labels name them.",
        "Return exactly this shape:\n\n```json\n" + _SCHEMA % {"category": category} + "\n```",
        _RULES,
    ]
    return "\n\n".join(parts)


def more_like_prompt(recipe_name, category, count=3):
    """Ask for neighbours of one that worked."""
    return "\n\n".join([
        f"I make **{recipe_name}** and would like more like it. {_counts_line(count)}",
        f"They go in the category **{category}**. Same sort of effort and the "
        "same sort of ingredients — a Danish supermarket, a weeknight.",
        "Return exactly this shape:\n\n```json\n" + _SCHEMA % {"category": category} + "\n```",
        _RULES,
    ])
