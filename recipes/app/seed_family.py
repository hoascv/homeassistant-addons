"""Base recipes for the family category.

Data, not code.

Every ingredient carries two names: `name` is what the recipe says, in English,
because that is what you read while cooking. `shop_name` is the Danish shelf
label, because that is what you are looking for in Netto with a trolley. The
shopping list is built from the second and the recipe from the first.
"""


def _i(name, shop_name, amount=None, unit="", optional=False):
    """One ingredient. A helper because writing the dict out fifty times
    invites a typo in a key nobody would notice until the list was wrong."""
    return {"name": name, "shop_name": shop_name, "amount": amount,
            "unit": unit, "optional": optional}


RECIPES = [
    {
        "name": "Chicken curry",
        "servings": 4,
        "minutes": 35,
        "protein_g": 41,
        "notes": "The one everyone eats. Mild enough for children, and the sauce does the work.",
        "ingredients": [
            _i("chicken breast", "kyllingebryst", 600, "g"),
            _i("onions", "løg", 2, "stk"),
            _i("apple", "æble", 1, "stk"),
            _i("curry powder", "karry", 2, "spsk"),
            _i("coconut milk", "kokosmælk", 400, "ml"),
            _i("chicken stock", "hønsebouillon", 2, "dl"),
            _i("rice", "ris", 300, "g"),
            _i("rapeseed oil", "rapsolie", 1, "spsk"),
        ],
        "method": (
            "1. Cut the chicken into bite-sized pieces and brown it in the oil.\n"
            "2. Lift the chicken out. Soften the onion and apple in the same pan.\n"
            "3. Add the curry powder and let it fry for half a minute.\n"
            "4. Pour in the coconut milk and stock, and reduce for 10 minutes.\n"
            "5. Return the chicken and heat through. Serve with rice."
        ),
    },
    {
        "name": "Spaghetti bolognese",
        "servings": 4,
        "minutes": 40,
        "protein_g": 38,
        "notes": "Doubles well and freezes. Grate a carrot in and nobody notices.",
        "ingredients": [
            _i("minced beef, 4-7%", "hakket oksekød 4-7%", 500, "g"),
            _i("onion", "løg", 1, "stk"),
            _i("garlic", "hvidløg", 2, "fed"),
            _i("carrots", "gulerødder", 2, "stk"),
            _i("chopped tomatoes", "flåede tomater", 2, "dåse"),
            _i("tomato purée", "tomatpuré", 1, "spsk"),
            _i("dried oregano", "oregano", 1, "tsk"),
            _i("spaghetti", "spaghetti", 400, "g"),
            _i("parmesan", "parmesan", 50, "g", optional=True),
        ],
        "method": (
            "1. Coarsely grate the carrots; finely chop the onion and garlic.\n"
            "2. Brown the mince thoroughly. Add the vegetables and soften.\n"
            "3. Stir in the tomato purée and fry it for a minute.\n"
            "4. Add the tomatoes and oregano. Simmer at least 20 minutes.\n"
            "5. Cook the spaghetti meanwhile. Serve with grated parmesan."
        ),
    },
    {
        "name": "Danish meatballs with potatoes",
        "servings": 4,
        "minutes": 45,
        "protein_g": 36,
        "notes": "Leftovers are the point — cold frikadeller on rye is lunch sorted.",
        "ingredients": [
            _i("minced pork", "hakket svinekød", 500, "g"),
            _i("onion", "løg", 1, "stk"),
            _i("egg", "æg", 1, "stk"),
            _i("rolled oats", "havregryn", 3, "spsk"),
            _i("milk", "mælk", 1, "dl"),
            _i("potatoes", "kartofler", 1, "kg"),
            _i("butter, for frying", "smør"),
        ],
        "method": (
            "1. Grate the onion finely and mix it into the meat with the egg, oats and milk.\n"
            "2. Season well and rest the mixture 15 minutes in the fridge.\n"
            "3. Boil the potatoes.\n"
            "4. Shape meatballs with a spoon and fry in butter, 4-5 minutes a side."
        ),
    },
    {
        "name": "Tray-baked salmon with root vegetables",
        "servings": 4,
        "minutes": 30,
        "protein_g": 39,
        "notes": "One tray, one oven, nothing to watch. Weeknight fish without the faff.",
        "ingredients": [
            _i("salmon fillet", "laksefilet", 600, "g"),
            _i("potatoes", "kartofler", 600, "g"),
            _i("carrots", "gulerødder", 4, "stk"),
            _i("red onion", "rødløg", 1, "stk"),
            _i("lemon", "citron", 1, "stk"),
            _i("olive oil", "olivenolie", 3, "spsk"),
        ],
        "method": (
            "1. Oven to 200°C.\n"
            "2. Cut the vegetables into wedges, toss in oil and salt, roast 20 minutes.\n"
            "3. Lay the salmon on top with lemon slices and bake 12-14 minutes more."
        ),
    },
    {
        "name": "Pancakes for dinner",
        "servings": 4,
        "minutes": 25,
        "notes": "Friday food. Make the batter an hour ahead if you can.",
        "ingredients": [
            _i("plain flour", "hvedemel", 250, "g"),
            _i("milk", "mælk", 5, "dl"),
            _i("eggs", "æg", 3, "stk"),
            _i("butter", "smør", 50, "g"),
            _i("sugar", "sukker", 1, "spsk"),
        ],
        "method": (
            "1. Whisk the flour, milk, eggs and sugar to a smooth batter.\n"
            "2. Rest it at least 30 minutes if there is time.\n"
            "3. Fry thin pancakes in butter over medium heat."
        ),
    },
    {
        "name": "Vegetable soup with rye bread",
        "servings": 4,
        "minutes": 30,
        "notes": "What the bottom of the fridge becomes on a Wednesday.",
        "ingredients": [
            _i("carrots", "gulerødder", 4, "stk"),
            _i("leek", "porre", 1, "stk"),
            _i("potatoes", "kartofler", 400, "g"),
            _i("celeriac", "knoldselleri", 200, "g"),
            _i("vegetable stock", "grøntsagsbouillon", 1, "l"),
            _i("cream", "fløde", 1, "dl", optional=True),
            _i("rye bread", "rugbrød", 4, "skive"),
        ],
        "method": (
            "1. Cut all the vegetables into rough cubes.\n"
            "2. Sweat them briefly, pour over the stock, and simmer until tender, ~20 minutes.\n"
            "3. Blend smooth. Season, and stir in the cream if you want it richer."
        ),
    },
]
