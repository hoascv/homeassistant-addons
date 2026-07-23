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
- Finances section: browse any month's revenue, costs, and net, plus an
  all-time total, plus an estimate of what you've saved by not buying
  your used eggs at the supermarket
- Trends tab: line chart (expandable to full screen) and table of eggs
  collected/sold/used over the last 3, 6, or 12 months, plus a 3-month
  egg-collection forecast based on your flock — and how that forecast
  would have performed in past months, so you can see how well it's
  tracking
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
rotated one. Eggs touching or overlapping in the photo may be missed or
undercounted; use the review screen's **+ Add egg** and the ✕ on any chip
to correct the count by hand. This feature also requires an **amd64** or
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
one that learns which detected shapes are really eggs (needs ~25
corrections; replaces a fixed one-size-fits-all cutoff), one that learns
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

### Estimated savings

The Finances section's **Est. savings** figures answer "what would this
have cost me at the supermarket?" — computed as eggs you've logged as
**used** (not sold, not just sitting uncollected) × your configured
**supermarket_egg_price**. Sold eggs aren't counted here, since those
already show up as revenue; this is specifically the value of eggs that
replaced a store purchase. It's shown for the current month and
all-time, right alongside Revenue/Costs/Net.

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
to take it off again.

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
