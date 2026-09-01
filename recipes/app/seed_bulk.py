"""Base recipes for the bulk category, and the healthy snacks.

Protein per serving is given for every one of these, because that is the figure
deciding one bulk meal over another — it is the reason the category exists.
kcal is given where the dish is simple enough for the number to be honest, and
left off where it is not.

English recipe names, Danish shelf labels — see seed_family for why.
"""
from seed_family import _i

MEALS = [
    {
        "name": "Chicken, rice and broccoli",
        "servings": 4,
        "minutes": 30,
        "protein_g": 52,
        "kcal": 640,
        "notes": "The boring one that works. Cook the rice in bulk on Sunday.",
        "ingredients": [
            _i("chicken breast", "kyllingebryst", 800, "g"),
            _i("rice", "ris", 400, "g"),
            _i("broccoli", "broccoli", 500, "g"),
            _i("soy sauce", "sojasauce", 3, "spsk"),
            _i("garlic", "hvidløg", 3, "fed"),
            _i("rapeseed oil", "rapsolie", 1, "spsk"),
        ],
        "method": (
            "1. Cook the rice.\n"
            "2. Fry the diced chicken over high heat until cooked through.\n"
            "3. Add the garlic and soy for the last two minutes.\n"
            "4. Steam the broccoli 4 minutes, so it keeps some bite."
        ),
    },
    {
        "name": "Beef with potatoes and green beans",
        "servings": 4,
        "minutes": 35,
        "protein_g": 48,
        "kcal": 700,
        "notes": "Higher fat than the chicken, and the one to cook when the appetite is not there.",
        "ingredients": [
            _i("minced beef, 8-12%", "hakket oksekød 8-12%", 700, "g"),
            _i("potatoes", "kartofler", 900, "g"),
            _i("green beans", "grønne bønner", 400, "g"),
            _i("onion", "løg", 1, "stk"),
            _i("beef stock", "oksebouillon", 2, "dl"),
            _i("olive oil", "olivenolie", 2, "spsk"),
        ],
        "method": (
            "1. Boil the potatoes in salted water.\n"
            "2. Brown the mince well and soften the onion with it.\n"
            "3. Add the stock and reduce for 5 minutes.\n"
            "4. Steam the beans and serve it all together."
        ),
    },
    {
        "name": "Salmon with potatoes and skyr dressing",
        "servings": 4,
        "minutes": 30,
        "protein_g": 45,
        "kcal": 620,
        "notes": "Protein from two directions, and the dressing takes a minute.",
        "ingredients": [
            _i("salmon fillet", "laksefilet", 700, "g"),
            _i("potatoes", "kartofler", 800, "g"),
            _i("plain skyr", "skyr naturel", 300, "g"),
            _i("lemon", "citron", 1, "stk"),
            _i("dill", "dild", 1, "bundt"),
            _i("cucumber", "agurk", 1, "stk"),
        ],
        "method": (
            "1. Bake the salmon at 200°C for 12-14 minutes.\n"
            "2. Boil the potatoes.\n"
            "3. Mix the skyr with lemon juice, chopped dill and grated cucumber. Season."
        ),
    },
    {
        "name": "Egg cake with potatoes and bacon",
        "servings": 4,
        "minutes": 25,
        "protein_g": 34,
        "notes": "Cheap, fast, and ten eggs go further than you think.",
        "ingredients": [
            _i("eggs", "æg", 10, "stk"),
            _i("diced bacon", "bacon i tern", 200, "g"),
            _i("potatoes", "kartofler", 500, "g"),
            _i("chives", "purløg", 1, "bundt"),
            _i("milk", "mælk", 1, "dl"),
            _i("rye bread", "rugbrød", 4, "skive"),
        ],
        "method": (
            "1. Boil the potatoes and slice them.\n"
            "2. Fry the bacon crisp in a large pan, then the potatoes in the fat.\n"
            "3. Beat the eggs with the milk, pour over, and set over low heat.\n"
            "4. Scatter with chives and serve with rye bread."
        ),
    },
]

SNACKS = [
    {
        "name": "Skyr with oats and berries",
        "servings": 1,
        "minutes": 3,
        "protein_g": 28,
        "kcal": 330,
        "notes": "Make it the night before and it is better.",
        "ingredients": [
            _i("plain skyr", "skyr naturel", 250, "g"),
            _i("rolled oats", "havregryn", 40, "g"),
            _i("frozen berries", "frosne bær", 100, "g"),
            _i("honey", "honning", 1, "tsk", optional=True),
        ],
        "method": "1. Stir it all together. Leave it in the fridge overnight if you can.",
    },
    {
        "name": "Peanut butter oat balls",
        "servings": 12,
        "minutes": 10,
        "protein_g": 6,
        "kcal": 140,
        "notes": "Twelve at a time, keeps a week in the fridge. Figures are per ball.",
        "ingredients": [
            _i("rolled oats", "havregryn", 200, "g"),
            _i("peanut butter", "peanutbutter", 150, "g"),
            _i("honey", "honning", 3, "spsk"),
            _i("cocoa powder", "kakao", 2, "spsk"),
            _i("protein powder", "proteinpulver", 30, "g", optional=True),
        ],
        "method": (
            "1. Mix everything to a firm paste.\n"
            "2. Roll into balls and chill for half an hour."
        ),
    },
    {
        "name": "Rye bread with cottage cheese and egg",
        "servings": 1,
        "minutes": 8,
        "protein_g": 26,
        "kcal": 380,
        "notes": "The cheapest 25 g of protein in the house.",
        "ingredients": [
            _i("rye bread", "rugbrød", 2, "skive"),
            _i("cottage cheese", "hytteost", 150, "g"),
            _i("eggs", "æg", 2, "stk"),
            _i("chives", "purløg", 1, "bundt", optional=True),
        ],
        "method": (
            "1. Boil the eggs for 7 minutes.\n"
            "2. Spread the cottage cheese on the rye, lay the eggs over, scatter with chives."
        ),
    },
    {
        "name": "Banana protein smoothie",
        "servings": 1,
        "minutes": 3,
        "protein_g": 32,
        "kcal": 400,
        "notes": "For the days when eating is the problem rather than the appetite.",
        "ingredients": [
            _i("milk", "mælk", 3, "dl"),
            _i("banana", "banan", 1, "stk"),
            _i("protein powder", "proteinpulver", 30, "g"),
            _i("rolled oats", "havregryn", 30, "g"),
            _i("peanut butter", "peanutbutter", 1, "spsk", optional=True),
        ],
        "method": "1. Blend it all for a minute.",
    },
    {
        "name": "Roasted chickpeas",
        "servings": 4,
        "minutes": 30,
        "protein_g": 9,
        "kcal": 180,
        "notes": "Crisps that are not crisps. Eat them the day they are made.",
        "ingredients": [
            _i("chickpeas", "kikærter", 2, "dåse"),
            _i("olive oil", "olivenolie", 2, "spsk"),
            _i("paprika", "paprika", 2, "tsk"),
            _i("ground cumin", "spidskommen", 1, "tsk", optional=True),
        ],
        "method": (
            "1. Rinse and dry the chickpeas thoroughly — dry is what makes them crisp.\n"
            "2. Toss in the oil and spices.\n"
            "3. Roast at 200°C for 25 minutes, shaking the tray halfway."
        ),
    },
]
