# Changelog

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
