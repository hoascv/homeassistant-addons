# Recipes

A recipe and healthy-snack catalog that builds a **Danish supermarket list**.

The recipes are in **English**, because that is what you read while cooking.
Every ingredient also carries the **Danish shelf label**, and the shopping list
is built from that one — because you are standing in Netto looking for
*hakket oksekød*, not for "minced beef".

The add-on **never goes online**. It ships with base recipes, and more are
loaded the way the Knowledge add-on loads its material: it writes you a prompt,
you run it on any assistant you like, and you paste the reply back.

## What it does

- **A catalog**, in categories you choose. It ships seeded for **Family** and
  **Bulk**, the second including healthy snacks.
- **A shopping list** built from the recipes you pick: scaled to the servings
  you want, merged where two recipes need the same thing, and grouped in the
  order a Danish supermarket is laid out.
- **Tick items off as you shop.** The ticks survive adding another recipe.

## The two names

Every ingredient has both:

| | |
|---|---|
| `name` | English — *minced beef, 4-7%* — shown in the recipe |
| `shop_name` | Danish — *hakket oksekød 4-7%* — shown on the shopping list |

The list is built entirely from the Danish one, which has a useful consequence:
two recipes calling for "minced beef" and "beef mince" become **one line**,
because both point at the same shelf label. The list still shows what the
recipes called it underneath, so an unfamiliar Danish word can be traced back.

If a loaded recipe gives only one name, that name is used for both and the
import says so. A list with one untranslated line is still a list; losing the
recipe over a missing translation would not be.

## The shopping list

Three things happen to every ingredient, in order.

**Scaled.** A recipe written for 4 that you want for 6 has its quantities
multiplied. A recipe with no stated servings is taken at face value rather than
scaled by a guess.

**Merged.** Lines that are the same purchase become one. 500 g and 1 kg of
*hakket oksekød* is 1,5 kg. But **3 fed** and **100 g** of *hvidløg* stay two
lines, because nobody buys 112 g of garlic — quantities only merge within the
same dimension, and an unrecognised unit merges with nothing at all.

Things you buy whole **round up**: 1,5 løg becomes 2, because half an onion
spare beats half an onion short.

**Grouped**, into the sections a shop is actually laid out in — Frugt & grønt,
Kød & fisk, Mejeri, Brød, Frost, Kolonial — so the list can be walked once.

### Staples

The `staples` option lists what is always in the cupboard, written as Danish
shelf labels. Those items are **dimmed but still shown**: one you have run out
of is still something you need, and hiding it means noticing in the shop rather
than at home. The header says how many of the items are staples, so the count
is not quietly inflated by things you own.

## Loading more recipes

**＋** → pick a category and what to ask for → **Copy the prompt**. Run it on
whatever assistant you have, paste the reply into the box, and press **Load it**.
**Check it** parses without saving, so a paste can be inspected first.

Anything unusable is dropped and listed rather than failing the whole paste — a
pack that lands four of five recipes and says so is worth more than a parse
error, because the only other remedy is asking again.

Loading a pack twice replaces rather than duplicates: a recipe is identified by
its name and category.

## Nutrition

`protein_g` and `kcal` are **per serving and optional**. They are shown when a
recipe has them and left blank when it does not — the prompt tells assistants to
leave them out rather than estimate, because a guessed number is worse than a
blank when it looks like it was measured.

Protein is given for every shipped Bulk recipe, since that is the figure the
category exists for.

## The base recipes

Fifteen ship with the add-on: six under Family, four Bulk meals and five snacks.
They are written **once**, on first start, and never rewritten — so an edit
survives a restart, and a recipe you delete stays deleted rather than coming
back every reboot.

## When a recipe was added

Each recipe shows the date it arrived, under the title.

The fifteen that ship say **"Shipped with the add-on"** instead of a date. They
all carry the timestamp of the add-on's first start, which is true and
misleading — you did not add them, and a date there only invites you to wonder
what you were cooking that day.

Re-importing a pack updates a recipe but does not change when it was added, so
that date keeps meaning the first time it arrived. A recipe that has been
re-imported shows both dates.

## Configuration

| Option | What it does |
|---|---|
| `categories` | Comma-separated. The first is where family seeds go, the second the bulk ones. Free text. |
| `default_servings` | Household size. Recipes are scaled to this when added to the list. |
| `staples` | Always-in-the-cupboard items, as **Danish** shelf labels. |
| `restrict_to_user_ids` | Limits the add-on to named Home Assistant users. |

## Access

Ingress only. There is no published port and no API token: a request without
Home Assistant's ingress header is refused. `restrict_to_user_ids` narrows it
further, per user.

The 401 that refusal produces is also what Add-on Watchdog's probe sees, and it
counts as healthy — the question a probe asks is whether the service is up, not
whether it can get in.

## Notes

- The database is `/data/recipes.db`, in every Home Assistant backup.
- Nothing leaves your instance. There is no outbound call anywhere in the
  add-on — that is the whole reason for the copy-a-prompt arrangement.
- Quantities are shown Danish-style with a comma: **1,5 kg**.

### Steering a batch

The **Main ingredients** box on the Load recipes sheet narrows what you get:
type `chicken, broccoli, sweet potato` and the prompt asks for recipes built
around them.

It says two things, because they are different instructions and a model given
only the first puts every ingredient into every dish:

> Build them around these: **chicken, broccoli, sweet potato**. Each recipe
> should lean on at least one of them as a main ingredient rather than a
> garnish, and between them the batch should cover all of them.

**Separate with commas, not spaces** — `minced beef` and `sweet potato` are
single ingredients. The chips underneath show what was actually understood, so
a forgotten comma shows up as one chip where you meant two, before you take the
prompt anywhere.

Up to 12 keywords. Past that the request stops being "build around these" and
becomes a list the model quietly picks from, which reads as it ignoring half of
what you asked.

It works for **Healthy snacks** too — a tub of skyr is as much a snack question
as a dinner one. The recipes you already have in that category are still listed
as ones to avoid, so the two narrowings stack.

## Duplicates

Recipes are matched on their **name and category, case-folded with whitespace
collapsed**. Loading a pack that contains `Chicken Curry` updates the
`Chicken curry` you already have instead of adding a second row — which is what
used to happen, because the only check was an exact string match and an
assistant spells the same dish differently between one paste and the next.

What is deliberately *not* treated as a duplicate:

- **The same dish in another category.** A family portion and a bulk portion
  are different recipes with different servings; that is why category is part
  of the match.
- **Names that merely resemble each other.** `Chicken curry` and
  `Chicken curry with rice` are two recipes, and guessing otherwise would merge
  things you meant to keep apart.

A re-import updates the content — servings, method, ingredients — but **not the
name or category**. Reaching that point means they already matched apart from
case, so the incoming spelling has nothing to teach; taking it would let one
lower-case pack rename your `Family` category to `family` everywhere.

### Copies that are already there

Nothing is merged for you. Two copies that look alike may genuinely differ —
one edited with what you actually cooked, one straight from a pack — and
deleting the wrong one loses work that exists nowhere else.

When the catalogue contains any, a line appears above the recipe list:
*"2 recipes look like duplicates — review"*. It opens a panel listing each
group with the ingredient count, when it was last changed and where it came
from, so you can tell the copies apart. The most recent is marked. **Open**
shows the full recipe; **Delete** removes that one copy and asks first.

The line only appears when there is something to act on — a permanent
"0 duplicates" is a thing you stop reading, and then miss the day it says 3.
