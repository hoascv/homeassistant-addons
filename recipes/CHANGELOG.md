# Changelog

## 1.4.0

- **Want to try / Cooked lists, and star ratings.** Open a recipe and the bar
  under the title has five stars, a **Want to try** toggle and a **Made it**
  button. A second row of chips above the recipe list filters by list, with the
  count in each.
- **Made it counts rather than flags.** "We make this every other week" is the
  useful fact about a household's cooking and a boolean cannot say it, so the
  sheet reads *Made 6 times, last on 26 August* and the row shows `made 6×`.
  Toggling the lists never touches that count — that is why they are separate
  actions rather than one status that also counted.
- The two lists are mutually exclusive: a recipe is either one you mean to try
  or one you have made, and a thing that is both is really just the second.
- Rating stays independent of them. A dish you have cooked but not judged is
  normal, and so is knowing you will dislike something before you make it.
- Both buttons and the stars are toggles — pressing the star a recipe already
  has clears it. A separate "remove" would be a third control for what the
  first one obviously means.
- In the list, ◷ and ✓ mark the name rather than adding a second pill; the
  category pill is already on the right of every row.
- Existing recipes upgrade to "on neither list, never made, unrated", which is
  true of them — not zero stars, which would be a judgement nobody made.

## 1.3.0

- **Main ingredients box on the Load recipes sheet.** Type
  `chicken, broccoli, sweet potato` and the prompt asks for recipes built
  around them, rather than whatever the assistant felt like.
- The instruction is two sentences on purpose: each recipe leans on at least
  one as a main ingredient, and the batch between them covers all of them. A
  model given only the first puts every ingredient into every dish.
- Separated on **commas and newlines, never spaces** — `minced beef` and
  `sweet potato` are single ingredients. Chips under the box show what was
  actually parsed, so a forgotten comma is visible as one chip where two were
  meant before the prompt goes anywhere.
- Repeats are dropped keeping the first spelling, and the list is capped at 12:
  past that the request stops being "build around these" and becomes a list the
  model quietly picks from.
- Works for Healthy snacks as well, and stacks with the existing "I already
  have these" list.
- Replaces the `theme` parameter, which no page ever set and which rendered as
  "Theme: chicken." — a hint rather than an instruction.

## 1.2.0

- **Recipes are matched on name and category case-folded, with whitespace
  collapsed.** The only check before was an exact string match, which caught an
  exact re-import and nothing else — and an assistant spells the same dish
  differently between one paste and the next. One recipe entered eight ways
  produced six rows; it now produces one.
- Danish names fold correctly (`GRØD` and `grød` are the same dish), which
  `lower()` would not have managed reliably.
- A re-import updates the content but no longer renames what is there. The
  names already matched apart from case, so the incoming spelling teaches
  nothing — and taking it let one lower-case pack rename the `Family` category
  to `family` everywhere it appeared.
- The same dish in a different category is still two recipes. A family portion
  and a bulk portion differ in servings, which is why category is in the key.
- **A "Possible duplicates" panel** lists copies already in the catalogue, with
  ingredient counts, dates and source so you can tell them apart. Nothing is
  merged for you: two copies that look alike may genuinely differ, and deleting
  the wrong one loses work that exists nowhere else. The nudge above the recipe
  list appears only when there is something to act on.
- Upgrading backfills the new key in Python rather than SQL — SQLite's `trim()`
  removes padding but cannot collapse an internal double space, and a weaker
  key would leave `Chicken  curry` neither reported nor matched.

## 1.1.0

- **Recipes say when they were added.** The date was already being recorded —
  it simply never left the database. It now shows under the title on a recipe.
- **A seeded recipe says "Shipped with the add-on"** rather than quoting a date.
  Its `created_at` is when the add-on first started, so all fifteen carry the
  same timestamp: true, and misleading. You did not add them, they came in the
  box, and a date there only invites you to wonder what you were cooking that
  day.
- Re-importing a pack moves `updated_at` and leaves `created_at` alone, so
  "added" keeps meaning the first time a recipe arrived rather than the last
  time something was pasted over it. A recipe that has been re-imported shows
  both dates; one that has not shows a single date rather than the same one
  twice.

## 1.0.1

- **The Copy button did not copy.** `navigator.clipboard` requires a secure
  context, and Home Assistant ingress is served over plain http — so on most
  installs the modern API is simply absent and the button fell straight to its
  "could not copy" message, leaving several hundred words to be selected by
  hand on a phone.
- It now falls back to `document.execCommand("copy")`, which is deprecated and
  is the only thing that works outside a secure context. The modern API is still
  preferred where it is available.
- The fallback needs a genuinely selectable element, so the scratch textarea is
  positioned off-screen rather than hidden — `display: none` cannot be selected
  and would have copied nothing. iOS additionally ignores `.select()` on a
  readonly field, so an explicit range is set.
- If both refuse, the prompt is opened **and selected**, leaving one keypress
  to go rather than a drag-select of six paragraphs.

## 1.0.0

First release.

- **A recipe and healthy-snack catalog that builds a Danish supermarket list.**
  Recipes are in English, because that is what gets read while cooking. Every
  ingredient also carries the Danish shelf label, and the shopping list is built
  from that one — you are standing in Netto looking for *hakket oksekød*, not
  for "minced beef".
- Two names has a useful consequence: two recipes calling for "minced beef" and
  "beef mince" become **one line**, because both point at the same shelf label.
  The list still shows what the recipes called it underneath.
- **The list is scaled, merged and grouped.** Scaled to the servings you want;
  merged where two recipes need the same purchase; grouped into the sections a
  Danish shop is laid out in, so it can be walked once.
- Quantities only merge within a dimension. 500 g and 1 kg of mince is 1,5 kg,
  but 3 fed and 100 g of garlic stay two lines — nobody buys 112 g of garlic —
  and an unrecognised unit merges with nothing at all.
- Things you buy whole **round up**: 1,5 løg becomes 2. Half an onion spare
  beats half an onion short.
- Spoon measures stay spoons. "22,5 ml tomatpuré" is arithmetically right and
  useless in a shop.
- **Staples are dimmed, not hidden.** One you have run out of is still something
  you need, and hiding it means noticing in the shop rather than at home.
- **Fifteen base recipes ship with it** — six Family, four Bulk meals, five
  snacks — written once on first start and never rewritten, so an edit survives
  a restart and a deleted recipe stays deleted.
- **More are loaded without the add-on going online.** It writes a prompt, you
  run it on any assistant, you paste the reply back. Anything unusable is
  dropped and listed rather than failing the whole paste.
- `protein_g` and `kcal` are optional and shown only when a recipe has them.
  The prompt tells assistants to leave them out rather than estimate: a guessed
  number is worse than a blank when it looks like it was measured.
- Ingress only, with `restrict_to_user_ids` to narrow it further.
