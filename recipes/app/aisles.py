"""Which part of the shop an ingredient is found in.

A supermarket list you cannot walk in order is a list you walk twice, so the
sections here are the ones a Danish supermarket is actually laid out in rather
than a food taxonomy.

The mapping is a table of substrings, not a chain of conditions: adding an
ingredient is one entry, and the rule that decides ties is stated once below
instead of being implied by the order of a hundred `if`s.
"""

# In shopping order, roughly as the shops are laid out. Anything unmatched
# falls to the last one, which is why it exists.
SECTIONS = [
    "Frugt & grønt",
    "Kød & fisk",
    "Mejeri",
    "Brød",
    "Frost",
    "Kolonial",
    "Andet",
]

FALLBACK = "Andet"

# Substring -> section. Matched against a lower-cased ingredient name.
#
# Substrings rather than exact names because a recipe writes "hakket oksekød
# 4-7%" and "økologiske gulerødder", and demanding exact names would put both
# in Andet. The cost is that a substring can match something it should not,
# which is what the longest-match rule below is for: "kokosmælk" is Kolonial
# even though "mælk" is Mejeri, because the longer key wins.
_RULES = {
    # Frugt & grønt
    "gulerod": "Frugt & grønt", "gulerødder": "Frugt & grønt",
    "løg": "Frugt & grønt", "hvidløg": "Frugt & grønt",
    "kartof": "Frugt & grønt", "tomat": "Frugt & grønt",
    "agurk": "Frugt & grønt", "salat": "Frugt & grønt",
    "spinat": "Frugt & grønt", "broccoli": "Frugt & grønt",
    "peberfrugt": "Frugt & grønt", "squash": "Frugt & grønt",
    "champignon": "Frugt & grønt", "banan": "Frugt & grønt",
    "æble": "Frugt & grønt", "citron": "Frugt & grønt", "lime": "Frugt & grønt",
    "bær": "Frugt & grønt", "avocado": "Frugt & grønt",
    "persille": "Frugt & grønt", "purløg": "Frugt & grønt",
    "ingefær": "Frugt & grønt", "porre": "Frugt & grønt",
    "blomkål": "Frugt & grønt", "bønner": "Frugt & grønt",
    "ærter": "Frugt & grønt", "majs": "Frugt & grønt",
    # Kød & fisk
    "oksekød": "Kød & fisk", "hakket okse": "Kød & fisk",
    "kylling": "Kød & fisk", "kalkun": "Kød & fisk",
    "svinekød": "Kød & fisk", "flæsk": "Kød & fisk", "bacon": "Kød & fisk",
    "laks": "Kød & fisk", "torsk": "Kød & fisk", "tun": "Kød & fisk",
    "rejer": "Kød & fisk", "pålæg": "Kød & fisk", "medister": "Kød & fisk",
    # Mejeri
    "mælk": "Mejeri", "fløde": "Mejeri", "smør": "Mejeri",
    "ost": "Mejeri", "skyr": "Mejeri", "yoghurt": "Mejeri",
    "æg": "Mejeri", "creme fraiche": "Mejeri", "kvark": "Mejeri",
    "hytteost": "Mejeri", "parmesan": "Mejeri", "feta": "Mejeri",
    # Brød
    "rugbrød": "Brød", "brød": "Brød", "tortilla": "Brød",
    "pita": "Brød", "burgerbolle": "Brød", "knækbrød": "Brød",
    # Frost
    "frost": "Frost", "frosne": "Frost", "is ": "Frost",
    # Kolonial
    "ris": "Kolonial", "pasta": "Kolonial", "spaghetti": "Kolonial",
    "havregryn": "Kolonial", "mel": "Kolonial", "sukker": "Kolonial",
    "olie": "Kolonial", "eddike": "Kolonial", "salt": "Kolonial",
    "peber": "Kolonial", "krydderi": "Kolonial", "bouillon": "Kolonial",
    "flåede tomater": "Kolonial", "tomatpuré": "Kolonial",
    "kikærter": "Kolonial", "linser": "Kolonial", "kokosmælk": "Kolonial",
    "honning": "Kolonial", "peanutbutter": "Kolonial", "nødder": "Kolonial",
    "mandler": "Kolonial", "rosiner": "Kolonial", "dadler": "Kolonial",
    "proteinpulver": "Kolonial", "kakao": "Kolonial", "bagepulver": "Kolonial",
    "soja": "Kolonial", "sennep": "Kolonial", "ketchup": "Kolonial",
    # Spices, which a recipe names individually and a shop keeps in one place.
    "oregano": "Kolonial", "paprika": "Kolonial", "spidskommen": "Kolonial",
    "karry": "Kolonial", "timian": "Kolonial", "basilikum": "Kolonial",
    "chili": "Kolonial", "muskatnød": "Kolonial", "laurbær": "Kolonial",
    "gurkemeje": "Kolonial", "rosmarin": "Kolonial",
    "chiafrø": "Kolonial", "kanel": "Kolonial", "vaniljesukker": "Kolonial",
}


def section_for(name):
    """The section an ingredient belongs to.

    The longest matching substring wins. Without that rule the answer would
    depend on dictionary order — "kokosmælk" contains both "kokosmælk" and
    "mælk", and only one of those is right.
    """
    if not name:
        return FALLBACK
    lowered = str(name).lower()
    best, best_len = FALLBACK, 0
    for needle, section in _RULES.items():
        if needle in lowered and len(needle) > best_len:
            best, best_len = section, len(needle)
    return best


def sort_key(section):
    """Position in shopping order, so a list can be walked once."""
    try:
        return SECTIONS.index(section)
    except ValueError:
        return len(SECTIONS)
