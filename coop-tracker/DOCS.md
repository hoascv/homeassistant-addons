# Coop Tracker

Log egg collection, coop cleaning, feeding, egg sales, and coop costs for
your chickens, right from your phone via the Home Assistant sidebar.

## Features

- Quick-add buttons for eggs, cleaning, feeding, sales, expenses, and
  eggs used/consumed — logging eggs can optionally count and size them
  from a photo (amd64/aarch64 only, off by default)
- A small red/green dot in the top bar showing whether the add-on can
  reach Home Assistant right now — tap it for the full connection detail
- "Container was empty" checkbox on feeding entries, with a live estimate
  of how long a container/bag of that food typically lasts
- Today / this-week egg counts, eggs on hand, last cleaning and feeding
  times
- Finances section, on the **Trends** tab: browse any month's revenue,
  costs, net and net incl. savings, plus an all-time total, and an
  estimate of what you've saved by not buying your used eggs at the
  supermarket
- Trends tab: line chart (expandable to full screen) and table of eggs
  collected/sold/used over the last 3, 6, or 12 months, plus a 3-month
  egg-collection forecast based on your flock — and how that forecast
  would have performed in past months, so you can see how well it's
  tracking
- Eggs-per-day charts on the same tab: how many eggs a day your flock is
  actually laying, day by day over the last 14/30/90 days and by month,
  worked out so that it doesn't matter whether you collect daily or every
  few days
- My Flock panel (🐔 icon): track individual chickens (name, photo, breed,
  hatch date) for an age-adjusted forecast, more accurate than flat
  per-breed counts — plus a per-chicken health history (vet visits,
  vaccinations, molting, weight checks, observations)
- Recent activity history with filtering and delete
- Backup & Restore panel (download or restore the SQLite database), plus
  a one-way CSV export of all entries for spreadsheets — comma-delimited,
  so if your spreadsheet app expects semicolons (e.g. Danish Excel), use
  its import dialog rather than double-clicking the file
- Push notification reminder if eggs haven't been collected in a
  configurable number of days, sent straight to your phone via the Home
  Assistant Companion App
- Optional Home Assistant sensors: push egg counts, last cleaning/feeding,
  monthly finances, and an "eggs overdue" binary sensor as real HA entities,
  usable on dashboards and in automations
- Mobile-first layout, no page reloads

## Installation

1. Add this repository to your Home Assistant add-on store (see the main
   README for the URL), or copy the `coop-tracker` folder to
   `/addons/coop-tracker` on your Home Assistant host.
2. Refresh the add-on store and install **Coop Tracker**.
3. Start the add-on and open it from the sidebar (ingress panel).

## Data

Entries are stored in a SQLite database at `/data/coop.db` inside the add-on,
which Home Assistant persists across restarts and updates automatically.

## Reading the data elsewhere

If you want this data in a warehouse or notebook, the add-on can tell you what
changed rather than making you reload everything.

Set **api_token** on the Configuration tab and send it as
`Authorization: Bearer <token>` — a pipeline has no Home Assistant session, so
this is how it authenticates.

To reach the add-on from outside Home Assistant, publish its port in the add-on's
Network section. **That port requires the token.** Requests arriving on it
without a valid bearer token are refused with `401`, including when no
`api_token` is configured at all — there is nothing else on that port that could
identify a caller, so "no token set" cannot mean "no check needed". Requests
through Home Assistant's ingress are unaffected: the Supervisor has already
authenticated the user, and `restrict_to_user_ids` narrows that further if you
set it.

So the token really is the only thing standing between the network and
`/api/export` and `/api/backup`, and nothing rate-limits guesses. There's no
required length or format — any text works and surrounding spaces are ignored —
but make it long and random rather than memorable. For example:

```
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

The add-on logs which state it is in at startup, so a caller getting `401` can be
told apart from a caller sending the wrong token without guessing.

- **`GET /api/export`** — every tracked table (collections, chickens, breeds,
  food types, health events, nesting boxes), plus the `max_seq` the snapshot
  corresponds to. Start here.
- **`GET /api/stats`** — row counts for **every** table, the database size and
  the same `max_seq`, without serialising a single row. `counts` holds the
  tracked tables and `other_counts` the rest (`change_log`, and anything the
  feed does not carry); `total` is the tracked subset and `total_all` the whole
  file, so a count and a size on the same line describe the same scope. For
  anything polling on a timer (the Add-on Watchdog does), this is the one to
  call: `/api/export` answers the same question in megabytes.
- **`GET /api/changes?since=<seq>&limit=<n>`** — everything after `seq`, oldest
  first. Each entry names its **actor** — `user`, `automation` or `migration` —
  so you can tell a value you entered from one the add-on wrote for you. Each
  entry carries the row's current state for an insert or update,
  and `null` for a delete. Apply them in order.

Watch `full_reload_required` — entries are pruned after 90 days, so a pipeline
that has been away longer should call `/api/export` again. Chicken photos and
the egg-vision training data aren't in the feed.

For a one-off spreadsheet dump instead, `/api/export.csv` is still there.

## Feed duration estimate

**Food type** on the Log Feeding sheet is a dropdown rather than free
text, pre-filled with whichever one you used last time — so logging the
same feed you always feed takes no typing. It comes pre-loaded with
common types (Layer feed, Grower feed, Starter feed, Pellets, Crumbles,
Mash, Scratch grains, Mixed grain, Kitchen scraps, Grit, Oyster shell),
and you can add or remove entries yourself via the **Manage list** link
next to the Food type label. It's a dropdown rather than free text so the
exact same text is always used for the same food, which is what makes
the estimate below reliable — removing a food type only affects what's
offered for *new* entries; anything already logged with it keeps
displaying and editing correctly regardless.

There's also a **Container was empty** checkbox — check it when you feed
and notice the container/bag was completely empty beforehand (i.e. this
feeding is a refill). As soon as you've logged it twice for the same food
type, the sheet shows a live estimate right where you're logging: the
average number of days between refills, and how many days it's been
since the last one. Different food types are tracked separately, so
pellets and layer feed, for example, each get their own estimate.

If an entry's food type isn't in the current list — logged before the
dropdown existed, or since removed via Manage list — editing that entry
(or logging a new one right after it) keeps showing the original text as
an extra option rather than silently swapping it for something else;
nothing already logged gets lost or renamed.

The Trends tab also has a **Feed refill cadence** table, listing every
food type you've ever logged with its all-time average days between
refills, how long ago it was last emptied, and how many times you've fed
it — independent of the 3/6/12-month range used for the egg chart above
it, since a meaningful refill average usually needs longer than that to
build up.

## Scanning a receipt

**Log Expense → 📷 Scan a receipt.** Photograph the till receipt and the amount,
date and shop name are read off it and put into the form.

> **It fills the form in. It never saves anything.** A photographed till
> receipt is creased, thermal and half in shadow, and OCR on one is wrong often
> enough that logging its guess unattended would put bad numbers in your books
> faster than typing them by hand would. Check the figure before you save.

Under the amount, any other prices it found appear as chips — the first guess
is wrong often enough to want the runner-up one tap away rather than a retaken
photograph.

It reads Danish and English, and knows the shapes a Danish till prints:
`1.234,56` is twelve hundred and thirty-four kroner, **I ALT** and **At betale**
mean the total, and **Moms**, **Kontant** and **Byttepenge** never do. That last
group is the reason a naive reading gets it wrong: VAT sits near the bottom in a
line of its own at a plausible fraction of the real figure, and the cash you
handed over is larger than what you paid.

Nothing leaves the machine — Tesseract runs inside the add-on, like the egg
photo analysis.

The button only appears where the OCR engine is installed, which is amd64 and
arm64. On armv7 there is no OpenCV either, so the whole photo pipeline is
absent and a button that could only apologise is not shown.

If it comes up empty, a straighter photo in better light usually fixes it —
and the amount box is still there to type into.

## Flock tonics

Off by default; turn on `tonic_enabled`. Shares the **🪣 Feed** tab with the
fermented feed card, and either one showing is enough to bring the tab back.

Garlic in the water, dried oregano in with the pellets, cider vinegar a couple
of days a week — ordinary husbandry, and exactly the sort of thing that gets
forgotten. Unlike a ferment nothing goes mouldy when you miss one. It does not
fail; it just quietly stops happening, and six weeks later nobody can say when
the birds last had anything.

> **Supplements, not medicine.** The evidence for most of these is thin —
> garlic and oregano have some support in poultry work, cider vinegar much less
> — and none of them treats a sick bird. A bird that is unwell needs a vet.
> The card says so too, because a tidy schedule of ticked-off tonics quietly
> implies the flock's health is handled.

### What ships

Four routines, with the amounts keepers actually use and the cautions worth
having. Each carries a **Why, and what to watch** note.

| | | |
|---|---|---|
| **Garlic in the water** | weekly | 1 crushed clove per litre, 4 hours, then fresh water |
| **Oregano and thyme** | weekly | a tbsp dried per kg of feed, or a fresh handful |
| **Cider vinegar** | fortnightly | 20 ml per litre for two or three days |
| **Fresh greens** | every 3 days | nettle tops (wilted), dandelion, kale |

Two of those cautions are worth repeating here:

- **Cider vinegar must never go in a galvanised metal drinker.** The acid
  leaches zinc out of the coating and that genuinely poisons birds. Plastic or
  ceramic only. The gut-health claims are thin; this risk is not.
- **More garlic is not better.** It is an allium, and alliums in quantity cause
  anaemia in birds. A clove per litre once a week is the usual amount and there
  is no reason to go past it.

They are seeded the first time you open the card with the feature on — never at
startup, so a keeper who never turns it on does not find four rows they did not
ask for. Delete the ones you do not want and they stay deleted.

### The card

One row per routine: when it is next due, how often, the dose, and how many
times it has been given. **Given** logs it and pushes the next one out by the
cadence. Only a routine more than three days late is coloured — "due" on a
weekly rhythm is not an emergency, and spending the card's one alarm on it
would bury the ferment row that is.

**A row folds once it is done.** Due, and it is open with its dose and its
caution showing — which is when you read them, since that is when you are
about to give it. Given, and it collapses to its name and when it is next due,
because four routines all up to date was a card full of instructions for
things nobody had to act on. Press **Given** and the row folds itself away.

Nothing is hidden for good: tap a folded row to open it, and the dose and the
**Why, and what to watch** note are where they were. **Given** and **✕** stay
on the row either way, so giving one early never needs it opened first.

**Pausing** keeps the history; deleting takes it with the routine. A routine
paused over winter comes back in spring with its record intact.

### The reminder

Once a day, at `tonic_times` (default `09:00`), naming what is due:

> Time for the flock's garlic in the water.

Once a day rather than twice, unlike the stir reminder: this is a weekly rhythm
and telling somebody twice in a day about a Sunday job is how a reminder
becomes something they swipe away. Three or more due are counted rather than
listed — a wall of names in a notification is not read.

Goes to `tonic_notify_service`, falling back to `notify_service`.

### Settings

| Option | |
|---|---|
| `tonic_enabled` | Shows the card and enables the reminder. |
| `tonic_times` | When it may fire. Default `09:00`. |
| `tonic_notify_service` | Where it goes. Blank uses `notify_service`. |

## Fermented feed

Off by default; turn on `ferment_enabled`.

Fermenting feed means soaking grain in water for a few days and letting the wild
lactobacillus work on it — the birds digest it better and waste less. It has one
hard requirement that makes it a poor fit for memory alone:

> **An unstirred batch grows mould on top and has to be thrown away.**

Stirring pushes the grain back under the water and lets the gas out. Miss it for
a day in a warm room and three days of waiting go in the compost. That is why
the reminder is the load-bearing part of this feature.

### Reading the charts

Every chart on Trends has **hover targets**: point at any date and the browser
shows the exact figure — the day, the rate, and for a forecast its range. Until
now the line showed a shape and nothing more.

The two eggs-per-day charts also draw a **flock ceiling**: a dashed line at one
egg per hen per day, labelled `5 hens`. That is a hard physical bound, and
having it on the chart turns a bare number into a proportion — four eggs a day
reads very differently once you can see where five is.

**A day above that line is ringed in red.** It is not a record harvest; it means
the spreading rule's assumption broke. Each collection is credited to the days
since the previous one, which assumes every visit empties the nest — so eggs
missed on Monday and found on Tuesday are all credited to Tuesday alone, and
five hens appear to have laid six.

The figure is left as it is rather than capped, because the number is what your
collections say and what is wrong is a fact about the collecting. If you see a
ring, the usual cause is a nest box checked in a hurry. Nothing needs fixing in
the app.

**Click any point on the day chart** to see what it rests on. Because the
figure is an attributed rate rather than a count, the answer usually names a
different day from the one you clicked:

> **20 Aug** — 6.00 eggs/day
> 6 collected on this day, the day after the previous collection.
> *That is more than 5 hens can lay in a day…*
> **Logged on this day:** 6 egg · 17:00

Where the eggs arrived in a later basket it shows that basket and its date, so
"the 17th" and "the collection on the 19th that paid for it" are both in front
of you. A day nothing covers says which kind of gap it is — before your first
log, or after the most recent one with the eggs still in the nest — rather than
opening an empty sheet.

Only the day chart drills down. A point on a monthly chart is an average of
thirty collections, so there is no single set of entries behind it to show, and
those points are deliberately not clickable.

### The tab

Fermented feed has its own tab, **🪣 Ferment**, between Home and Trends. The tab
button only appears when `ferment_enabled` is on — an empty tab you can reach
reads as something broken, where a tab that is not there reads as a feature you
have not turned on, which is the truth. Turning the option off while you are
standing on the page moves you back to Home.

One row per batch: which container, when it will be ready, and how long since it
was stirred. A batch overdue for a stir is the **only** thing on the card that
gets a colour — it is the one thing worth acting on today, and colouring
anything else would bury it.

Buttons per batch: **Stirred**, **Fed** (once it is ready), and **Binned**.
Binned asks first, and is recorded separately from fed — a batch lost to mould
is a different event, and how often it happens is worth being able to find out.

A batch has three lives, and the row says which one it is in:

| | | |
|---|---|---|
| **Fermenting** | day 0 to `ferment_days` | working; stir it |
| **Ready · day 5 of 11** | until `ferment_max_age_days` | feed from it |
| **Past it — day 12 of 11** | after that | bin it, coloured red |

The third is the one people miss. A tub that has been ready for a week looks
exactly like one that was ready this morning — nothing about it announces that
it has gone over, only the clock knows. Past the window the culture has run out
of sugar and stopped holding the spoilage organisms back, so the row loses its
**Stirred** button (there is no point stirring something you are about to throw
away) and **Binned** stops being the quiet option.

`ferment_max_age_days` defaults to 11 and is held to at least `ferment_days + 1`
— a batch that went off before it was ever ready is a state the settings can
express and a tub cannot be in.

### When it was stirred

Each batch row carries a small **5×** button — tap it for that tub's stirs,
newest first, with the gap before each one:

```
5 stirs · usually 12h apart · 1 late

  Thu, Sep 3 09:15    7h later
  Thu, Sep 3 02:15   19h later      ← amber
  Wed, Sep 2 07:15   12h later
  Tue, Sep 1 19:15   11h later
  Tue, Sep 1 08:15   mixed
```

**The gaps are the point.** A column of times says you stirred it; the interval
between them says whether the rhythm held — and a long one is exactly where a
batch came closest to going in the compost. Anything past `ferment_stir_hours`
is the only thing coloured, because that is what you are scanning for.

The first entry of every batch reads **mixed** rather than a gap: that stir is
the moment you made it up, and there is no earlier one for it to be late after.

The summary line answers "am I keeping up with this" as a proportion, and the
typical gap is a median — one forgotten weekend should not make a well-kept
batch look erratic.

Stirs outlive the batch they belong to. A tub that was fed or binned keeps its
record, which is most of why it is worth having.

### The stir reminder

A push notification naming the containers:

> Stir the fermenting feed in Tub 1 — it has been 14h.

**One notification, not three.** When there is more than one thing to say the
lines are combined, worst-to-get-wrong first — stirring stops mould, binning
stops somebody feeding spoiled grain, and feeding will still be true in an hour:

> Stir the fermenting feed in Tub 1 — it has been 14h. Bin Tub 3 — it has been
> going 12 days, past the 11-day mark. Do not feed it. Ready to feed: Tub 1,
> Tub 2. Use Tub 1 first, it is on day 8 of 11.

Three pushes arriving together at 08:00 is how you teach somebody to swipe the
whole lot away — including the stir reminder, which is the one that cannot
afford to be ignored.

It goes to **`ferment_notify_service`**, falling back to `notify_service` when
that is blank. Fermenting is a twice-a-day job and collecting eggs a once-a-day
one, so a household may want them on different phones — but the common case is
one phone, and nobody should have to fill in two options to get it. Setting only
`ferment_notify_service` works too: you can have stir alerts without turning on
the egg reminder.

It is deliberately **not** part of the daily egg reminder. Stirring is a
twice-a-day job, and a reminder that can only arrive at 18:00 is no use for the
morning one.

It fires at **times of day**, not on an interval. "Every 12 hours" lands at 3am
half the time, and a notification nobody can act on is one you learn to swipe
away — which costs you the reminders that mattered too. Default is 08:00 and
20:00; `ferment_stir_times` changes them.

One notification per window, remembered across a restart. A window where there
was nothing to say does not count as used, so a batch falling due at 20:30 still
gets its reminder at 20:30. A tub that is past its window is reported even when
everything has just been stirred — the stir clock no longer decides on its own
whether anything gets said.

### Carrying the culture forward

A batch seeded with liquid from the last one is ready in **two days instead of
three**. The new grain arrives with a working culture rather than waiting for
wild lactobacillus to find it.

The important part is which half you keep:

1. Feed the birds from the tub as usual.
2. **Drain the liquid off into a jar** and put it in the fridge.
3. **Bin or compost the wet grain.** Rinse the tub — a quick rinse, it does not
   need to be spotless.
4. Next batch: new grain, water, and 1–2 cups of the saved liquid stirred in.

Keeping the *grain* back instead is the shortcut that goes wrong. Three-day-old
wet grain is the substrate spoilage organisms have had three days to establish
on; the drained brine is the culture without it. The add-on only ever offers to
keep the liquid.

When you press **Fed** it asks whether to keep the liquid. Answer yes and a jar
appears on the card; the next **+ New batch** then offers to seed from it. It
asks rather than assumes — the jar is in the fridge and only you can see it.

**The jar is refused from a binned batch.** A batch thrown out for mould is
exactly the culture you must not carry into the next tub, and that is the one
moment somebody is most tempted to, with three days of waiting otherwise wasted.
The button is not offered there and the API rejects it.

Two things the card will warn about, and neither of them blocks anything:

- **Past 7 days in the fridge**, the culture may have gone quiet. Seeding with
  something exhausted gives you the wait you were avoiding, plus a false sense
  that you were not waiting.
- **Generation 8 or so.** Each pass is one more remove from what you started
  with, and whatever is most vigorous gradually takes over. Worth a clean batch
  now and then. The card counts the generations so you can notice; **Discard
  jar** is the way back to a fresh start.

A cold room still wins. If you set `ferment_days` to 4 because the utility room
is 12°C in February, seeding shortens the wait relative to that — it does not
override it.

### How much to make

The suggestion comes from your configured flock: **five hens over a three-day
ferment is about 675 g of dry feed** (45 g per bird per day, which is roughly a
quarter-cup of pellets — it swells with the water it takes up, so that is what
goes in, not what comes out).

It is offered, not filled in. You know your birds; the add-on does not.

A batch counts as stirred the moment you start it — you have just mixed it — so
the first reminder arrives an interval after you last touched it.

### Settings

| Option | |
|---|---|
| `ferment_enabled` | Shows the card and enables the reminder. |
| `ferment_days` | How long a batch sits before it is ready. Default 3. A seeded batch takes 2 unless this is lower. |
| `ferment_stir_hours` | How long it may go unstirred before it counts as due. Default 12. |
| `ferment_stir_times` | When reminders may fire. Default `08:00, 20:00`. |
| `ferment_max_age_days` | How long a batch stays good to feed from. Default 11. |
| `ferment_notify_service` | Where ferment reminders go. Blank uses `notify_service`. |

The reminder needs one of `ferment_notify_service` or `notify_service` set.

## Egg photo counting & sizing (experimental)

Off by default; turn on **egg_vision_enabled** in Configuration to add a
**📷 Count & size from a photo** button to the Log Eggs sheet. Take (or
choose) a photo of your eggs sitting in a registered nesting box, and the
add-on counts the eggs and estimates each one's size (Small/Medium/
Large/XL) by measuring them against that box's known inside width — no
coin needed, since the camera is handheld and a box's own edges are
already in every shot. Nothing is ever logged automatically — you always
land on a review screen first, where you can drag the box's side-wall
lines into place if they weren't found automatically, tap any egg to
cycle its size, add a missed egg, or remove a wrongly-detected one,
before the results fill in the usual count and you hit Save like normal.

**Set up a nesting box before first use.** From the ⚙️ settings sheet
(or straight from the Log Eggs photo button if no box exists yet), enter
the box's name and its inside width in centimeters — measure it, don't
guess, since this is what makes every size estimate meaningful. You can
register more than one box; the app tries to recognize which one is in
each photo automatically (once it's seen enough of each), and only asks
you to confirm or add a new one when it isn't confident. Setting up a box
also walks you through a short guided round of photos so the add-on can
learn to spot that box's edges reliably before you rely on it day to day.

**For the best results:** eggs are found by their *color* standing out
from the bedding — brown eggs on pale straw work fine, and so would
white or even green/blue eggs, as long as the egg's color differs from
whatever it's lying on (an egg almost exactly the color of its bedding
is the one genuinely hard case). Even, diffuse lighting helps — avoid a
single bright light causing glare on the shells. Keep eggs separated,
not touching each other, and frame the photo so both side walls of the
box are visible. For an angled shot into a deep box, the two wall lines
on the review screen can be tilted (drag either end of each line) to
follow the walls as they converge — egg sizes are then measured against
the local wall-to-wall distance at each egg's own position, so eggs
near the back of the box aren't undersized.

**Be aware of the limits:** size is estimated from each egg's measured
width, which is an approximation of the real, weight-based S/M/L/XL
grading, not a substitute for a kitchen scale — always glance over the
suggested sizes before saving. The tilted-wall measurement corrects for
walls converging with depth, but not for every possible camera angle —
a roughly box-aligned shot is still more accurate than a sharply
rotated one. Eggs that touch are automatically separated and counted
individually, but two eggs overlapping more than about half their width
(one largely hidden behind the other) can still be counted as one — use
the review screen's **+ Add egg** and the ✕ on any chip to correct the
count by hand. This feature also requires an **amd64** or
**aarch64** install (the same architecture requirement as the Advanced
forecast feature below) — on other architectures the button explains it
isn't available on that device.

### Training the model (optional)

Off by default; turn on **egg_vision_training_enabled** in Configuration
to have the add-on learn from your own corrections over time. When on,
each time you review and save a photo, the **photo itself, the
automatically-detected result, and your corrected result** are stored
on-device (a separate table from your chicken photos — nothing leaves
the device, and nothing is included anywhere except the backup file
described below). Setting up a nesting box always stores its guided
setup photos this way too, regardless of this setting, since registering
a box is itself a deliberate opt-in.

Open the ⚙️ settings sheet and tap **Train now** to fit up to three
models, each of which only activates once it has enough of its own data:
one that learns which detected shapes are really eggs (needs 15
confirmed eggs *and* 15 rejected shapes, so corrections in both directions
count; replaces a fixed one-size-fits-all cutoff), one that learns
your flock's actual size boundaries (needs ~25 sized examples; replaces
the standard EU-weight-band formula), and one that recognizes which
registered box a photo is of (activates as soon as two boxes have 3+
photos each — box recognition compares each photo against a fingerprint
built by a small bundled image network (~5MB, runs entirely on-device),
so it keys on the box's actual appearance, not just its overall color).
Nothing changes until you train — every install that hasn't opted in
behaves exactly as described above, and the review screen still shows
you every result before it's saved either way.

Stored photos are capped (**egg_vision_training_retention_count**,
default 200 — oldest deleted first) and never leave the device unless you
download a backup. **Clear training data** in the settings sheet deletes
every stored photo immediately (a trained model itself — a few hundred
numbers, not a photo — is kept; only the raw images are removed). Note
that enabling this materially increases the size of the **.db** file
produced by Backup & Restore, since the stored photos travel with it.

**View training photos** (in the settings sheet) opens a gallery of every
stored photo with what the model learned from each — its egg count and
sizes. From there you can **Exclude** a photo (e.g. a blurry shot or one
you corrected wrongly) to drop it from training without deleting it — it
stays greyed out in the gallery, and **Include** puts it back, or
**Delete** removes it for good. **Edit** reopens the same review screen so
you can re-correct the eggs/sizes/box edges and save the fix back onto
that photo. These changes take effect only when you train, so the gallery
shows a **Retrain now** banner after any edit — one tap applies them.

## Configuration

- **currency**: `DKK` (default), `USD`, `EUR`, `GBP`, `SEK`, `NOK`, `CHF`,
  `CAD`, `AUD`, or `JPY`. Controls the symbol and decimal formatting used
  for revenue, costs, and net figures.
- **reminder_enabled**: `false` (default). Turn on to get a push
  notification when eggs haven't been collected recently.
- **reminder_check_time**: `18:00` (default). Time of day (24h `HH:MM`,
  in your Home Assistant's local timezone) the add-on checks whether a
  reminder is due.
- **reminder_threshold_days**: `2` (default). Send the reminder once the
  last egg collection is at least this many days old.
- **notify_service**: empty by default. The Home Assistant notify service
  for your phone, e.g. `mobile_app_johns_iphone` — **without** the
  `notify.` prefix. Find the exact name via the app's Notifications panel
  (🔔 icon in the top bar), which lists every `notify.*` service Home
  Assistant knows about (this requires the Home Assistant Companion App
  to be installed on your phone first). Use the panel's "Send test
  notification" button to confirm the value works before waiting for the
  real trigger.
- **ha_sensors_enabled**: `false` (default). Turn on to push Coop Tracker's
  stats into Home Assistant as real entities (see below), so you can put
  them on a dashboard or use them in automations.
- **flock_isabrown_count** / **flock_sussex_count**: `3` / `2` by default.
  Fallback counts for the egg collection forecast (see below) — only used
  if you haven't added any individual chickens in **🐔 My Flock**, where
  tracking real birds gives a more accurate, age-adjusted forecast
  instead. Set both to `0` to turn the fallback off.
- **supermarket_egg_price**: `2.5` by default (price for a single egg, in
  whichever **currency** you've set). Used only for the Finances
  section's "Est. savings" figures (see below) — adjust it to match what
  a single egg costs at your local supermarket for a meaningful number.
- **restrict_to_user_ids**: empty by default (anyone who can open the
  add-on may use it). To limit access to specific people, list their
  Home Assistant user IDs here, comma-separated — everyone else gets an
  "access restricted" page instead. Find your own ID in the ⚙️ settings
  sheet under **Access control**, and **add it before you set this** so
  you don't lock yourself out (if you do, just clear the value again from
  the Configuration tab). Note this is on top of Home Assistant's own
  rule that only admin-group users see the add-on in their sidebar — this
  option makes that a hard block and lets you narrow it to particular
  users rather than the whole admin group.

Set these from the add-on's **Configuration** tab, then restart the
add-on for changes to take effect.

### Egg collection forecast

The Trends tab projects the next 3 months of expected egg collection,
shown as a dashed line continuing past your actual history. There's no
training step: it's recomputed from scratch every time you open the
Trends tab, so it naturally tracks your flock's real performance (a hen
going broody, molting, or a new hen coming into lay) without you doing
anything. The forecast also follows the seasons: longer days boost laying
in summer and shorter days lower it in winter, so a projection made in
autumn correctly shows the coming winter dip (and the spring recovery)
instead of running the current rate flat.

Once there's enough history, the chart also shades a range around the
forecast line showing how far off past projections have actually been —
"typically within ±N eggs," based on comparing what the forecast said in
past months against what really happened. The range stays the same width
for every forecasted month rather than widening further out, since that's
the only claim the historical comparison actually supports.

**Where the baseline comes from:** if you've added at least one chicken
in **🐔 My Flock** (see below), the forecast uses each of your active
chickens' actual ages. Otherwise it falls back to flat per-breed counts
(**flock_isabrown_count** / **flock_sussex_count**, `3` and `2` by
default) — the original method, kept for anyone who hasn't added
individual chickens. Once you've logged at least one egg, whichever
baseline is in play gets scaled by how your actual collection over the
last 30 days compares to it. The Trends tab's caption tells you which
one is active.

The same dashed line also runs back through your history: for each past
month it shows what the forecast *would have* predicted using only the
data available at the time, next to what actually happened (also broken
out in the table's "Forecast" column). Early months, with little or no
prior data to work from, will tend to be less accurate; the forecast
should track closer to actual as more collection history builds up. If
you're tracking individual chickens, this also uses each bird's actual
age *as of that past month*, not its current age.

Tap the ⛶ icon on the chart to expand it to fill the screen (tap again,
or press Esc, to go back) — turning your phone to landscape while
expanded gives noticeably more width to read a long history at a glance.

### Eggs per day

Below the main chart, a second smaller one shows how many eggs a day your
flock actually laid in each of those months, with a **Per day** column in
the table alongside it.

**You don't have to collect every day for this to be right.** Each
collection is counted across every day since the one before it — find 12
eggs after leaving it four days and that's 3 a day for those four days,
not one big day followed by three empty ones. So collecting daily and
collecting twice a week give you the same line, and you can compare
months without thinking about how often you got out to the coop in each.

Two consequences worth knowing:

- **The days since your last collection aren't counted yet.** Those eggs
  are still in the nest as far as the app knows, so the current month's
  figure covers up to your most recent collection and no further — it
  won't sag just because you haven't been out today.
- **A month you didn't log anything for is left blank, not shown as
  zero.** Same for a stretch of more than a month with no collection at
  all: rather than smear a later collection back over weeks of silence
  and draw a confident near-flat line, the chart just breaks. A gap means
  "no data", not "the hens stopped."

The dashed continuation past today is the same forecast as the chart
above, expressed per day.

Under each rate is the number of days it was actually averaged over, and
months resting on fewer than 10 days get a **hollow point** on the chart.
That's almost always the month you started logging: it only covers from
your first collection onwards, so it reads high next to a full month
rather than being comparable to one.

### Eggs per day, recently

Above the monthly chart, a **day-by-day** view of the last 14, 30 or 90
days, for how your flock is laying *right now* — the monthly chart can't
tell you that, since its current-month figure averages everything since
the 1st.

It's the same basis, just not grouped into months, so the flat stretches
are the days a single collection covers. **The line normally stops a day
or two short of today**, at your last collection: anything laid since is
still in the nest as far as the app knows, and drawing it as zero would
show the chart falling off a cliff every time you hadn't been out yet.
The caption tells you how far short it stops.

### Advanced forecast (experimental)

Below the main chart, an **Experimental: statistical forecast
(Holt-Winters)** panel offers a second opinion: a real statistical model
fitted directly on your logged history, shown alongside a shaded
confidence range, as an independent check against the forecast above —
not a replacement for it. It's off by default; turn on
**advanced_forecast_enabled** in the add-on's Configuration tab to try
it, then tap the panel to load it (it isn't fetched unless you open it).

This needs some history to work with: at least 6 months of egg
collection for a basic trend-only fit, and 24 months (two full years)
before it adds a seasonal component of its own — the panel tells you how
many months you have and how many you need. It's only available on
**amd64** and **aarch64** installs (e.g. an Intel/AMD mini-PC or a
64-bit Raspberry Pi OS); on other architectures the panel explains it
isn't available on that device rather than failing silently.

### Money, month by month

Under the egg chart on the **Trends** tab: revenue, costs and net over the same
months the range selector picks, so the tiles above and the chart below cannot
disagree about a month.

**Net is the one that matters and the one that goes negative** — the axis spans
below zero and draws the zero line, because "is the flock paying for itself
this month" is the question, and a chart clamped at zero cannot answer it. The
counted charts on the same tab still span 0-to-max, which is right for eggs:
there is no such thing as a negative collection.

Est. savings is deliberately *not* in the charted net. It is an estimate, and
folding one into a measured line makes the line say more than it knows. It
stays in the tiles, where it is labelled as an estimate.

Costs are drawn dashed as well as coloured. Revenue green against costs red is
the one pair that cannot be separated by choosing better shades — lightening
them for the dark theme drops their colour-blind separation further, not
further apart — so the line style carries the identity for a reader who cannot
use the hue, and the legend swatch is dashed to teach it.

### Money, recently

The same money as the chart above, over a window that ends **today** — 14, 30 or
90 days — sitting under the monthly chart with its own range selector.

It exists because the monthly chart cannot say anything useful about the month
you are standing in. That month starts at zero on the 1st and spends the rest of
the month catching up with the completed months beside it, so the current month
always reads as a collapse: worst on the 1st, and not honest until the 30th. A
window that simply ends today has no such edge.

**Money in is drawn above the zero line and money out below it**, one bar each,
with that bucket's **net** as a line over the top. A bucket where nothing moved
has no bar at all — which is the point of drawing bars here. Money is not a rate
the way eggs are; it moves in lumps, a sale here and a sack of feed there, so
most days are a genuine zero. A line has to join those gaps and so reads as a
plunge to the floor; a missing bar says "nothing happened" and nothing else.

That layout also does the colour-blind work. Revenue green against costs red is
the one pair that cannot be separated by choosing better shades, which is why
the monthly chart draws costs dashed — here the two sit on **opposite sides of
the zero line**, which separates them more firmly than any line style.

**At 90 days the bars become one a week.** Ninety bars is more than the chart can
show or you can scan. The weeks are trailing 7-day periods counted back from
today, not calendar weeks, so every bar — the newest included — covers exactly
seven days. A part-week bar at the right-hand edge would be the monthly chart's
problem all over again, just smaller.

Hover or tap a bucket for what moved in it and where the window stands at that
point. The **running net** is deliberately not drawn: over a long losing run it
reaches a size that would flatten the bars to nothing on a shared axis. It is in
the tooltip and in the caption under the chart, where it cannot distort what the
bars say.

Totals are what moved **inside the window**, so widening the range from 30 to 90
days changes them — it is a different question, not more of the same one.

### Estimated savings

The Finances section's **Est. savings** figures answer "what would this
have cost me at the supermarket?" — computed as eggs you've logged as
**used** (not sold, not just sitting uncollected) × your configured
**supermarket_egg_price**. Sold eggs aren't counted here, since those
already show up as revenue; this is specifically the value of eggs that
replaced a store purchase. It's shown for the current month and
all-time, right alongside Revenue/Costs/Net.

**Net incl. savings** puts the two together: net plus that estimate. It is
its own tile rather than a replacement for Net, because they answer different
questions and both are true. Net is money that actually moved; a keeper who
never sells an egg has a net that only ever falls, which says the flock is a
pure cost — accurate about the bank account and wrong about the household.
Net incl. savings says what the flock is worth once the eggs you ate are
counted as money you did not spend. It is coloured on its own sign, so a
flock in the red on sales alone can still show green once the kitchen is
counted.

The Finances section lives on the **Trends** tab. It is something you look
at rather than something you do, and the home page is for the six logging
buttons you came to press.

If you check **Given away** on a Log Used entry (for eggs you hand off
rather than eat yourself), that egg still counts against "eggs on hand"
as usual, but is left out of Est. savings — giving an egg away doesn't
reduce your own grocery bill, so it shouldn't count as money saved.

### My Flock: individual chickens and breeds

Tap the 🐔 icon to track chickens individually — name, photo, breed, and
hatch date — instead of just a flat count per breed. The moment you add
at least one active chicken, the egg forecast switches from flat
per-breed counts to summing each active chicken's own age-adjusted rate
(a chicken marked **Lost** is excluded, but stays in the list). Hatch
date is optional; without it a bird is assumed to be in its prime laying
years, the most forgiving default. A photo is optional too — tap
**Choose File** in the chicken form to add one (resized automatically,
so a normal phone photo won't bloat the database), and **Remove photo**
to take it off again. In the flock list, tap a chicken's photo to see it
full-size.

Each chicken also has a **Health history**: open the chicken from the
list and use **+ Add** under Health history to log a vet visit,
vaccination, molt start/end, weight check (in grams), or a general
observation, each with a date and optional notes. Events are shown
newest first and can be deleted with ✕. The section appears when editing
an existing chicken (a brand-new chicken has to be saved first).
Removing a chicken removes its health history with it.

Age adjustment is a simple three-stage curve, the same shape for every
breed: no eggs before about 20 weeks old, full rate through about 18
months old, and a reduced rate (80% of full) after that.

The **Breeds** list underneath (Isabrown and Sussex by default, each with
a published average eggs/year) is also yours to edit — add any breed you
keep with its own annual-eggs estimate, or remove ones you don't need.
Removing a breed doesn't touch any chicken already assigned to it — that
chicken keeps its recorded breed name, it just won't contribute to the
forecast until it's reassigned to a breed that still exists (or that
breed is re-added).

### Connection status dot

The small dot next to the top bar's icons is green when the add-on can
reach Home Assistant right now, red when it can't. It's checked once
when the page loads — tap it any time for the full detail (the same
Debug info shown in the 🔔 Notifications panel), including the specific
error if it's red.

### Home Assistant sensors

When `ha_sensors_enabled` is on, Coop Tracker pushes these entities to Home
Assistant (via the Supervisor API, no MQTT broker required):

- `sensor.coop_tracker_eggs_today`
- `sensor.coop_tracker_eggs_week`
- `sensor.coop_tracker_eggs_available`
- `sensor.coop_tracker_last_cleaning`
- `sensor.coop_tracker_last_feeding`
- `sensor.coop_tracker_revenue_month` / `_cost_month` / `_net_month`
  (formatted using the **currency** option)
- `binary_sensor.coop_tracker_eggs_overdue` — `on` once the last egg
  collection is at least **reminder_threshold_days** old, independent of
  whether the push-notification reminder itself is enabled

They update immediately after you log, edit, or delete an entry, and are
refreshed every minute in the background regardless. Since these are set
directly via the Home Assistant REST API rather than through a full
integration, they don't survive a Home Assistant restart on their own — they
reappear automatically within a minute (or as soon as you log something)
once both Home Assistant and the add-on are back up.

### Notes on the reminder

- The check runs once a day, in-process — no Home Assistant Automation
  needed.
- The "already notified today" guard is stored in the add-on's database,
  so a restart won't re-send a reminder that already went out that day.
- Requires the add-on's `homeassistant_api` permission (already granted
  in `config.yaml`), which lets it call Home Assistant's `notify` service
  directly — no long-lived access token setup needed on your end.
