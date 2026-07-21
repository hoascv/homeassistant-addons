# Coop Tracker

Log egg collection, coop cleaning, feeding, egg sales, and coop costs for
your chickens, right from your phone via the Home Assistant sidebar.

## Features

- Quick-add buttons for eggs, cleaning, feeding, sales, expenses, and
  eggs used/consumed
- "Container was empty" checkbox on feeding entries, with a live estimate
  of how long a container/bag of that food typically lasts
- Today / this-week egg counts, eggs on hand, last cleaning and feeding
  times
- Finances section: browse any month's revenue, costs, and net, plus an
  all-time total
- Trends tab: line chart (expandable to full screen) and table of eggs
  collected/sold/used over the last 3, 6, or 12 months, plus a 3-month
  egg-collection forecast based on your flock's breed composition — and
  how that forecast would have performed in past months, so you can see
  how well it's tracking
- Recent activity history with filtering and delete
- Backup & Restore panel (download or restore the SQLite database)
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

**Food type** on the Log Feeding sheet is a fixed dropdown (Layer feed,
Grower feed, Starter feed, Pellets, Crumbles, Mash, Scratch grains, Mixed
grain, Kitchen scraps, Grit, Oyster shell) rather than free text, and
pre-fills with whichever one you used last time — so logging the same
feed you always feed takes no typing. It's a fixed list on purpose (not
configurable) so the exact same text is always used for the same food,
which is what makes the estimate below reliable.

There's also a **Container was empty** checkbox — check it when you feed
and notice the container/bag was completely empty beforehand (i.e. this
feeding is a refill). As soon as you've logged it twice for the same food
type, the sheet shows a live estimate right where you're logging: the
average number of days between refills, and how many days it's been
since the last one. Different food types are tracked separately, so
pellets and layer feed, for example, each get their own estimate.

If you logged feeding entries before this dropdown existed with a food
type that isn't on the list, editing that entry (or logging a new one
right after it) keeps showing your original text as an extra option
rather than silently swapping it for something else — nothing already
logged gets lost or renamed.

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
  How many hens of each breed you keep — used only to compute the egg
  collection forecast on the Trends tab (see below). Set both to `0` to
  turn the forecast off.

Set these from the add-on's **Configuration** tab, then restart the
add-on for changes to take effect.

### Egg collection forecast

The Trends tab projects the next 3 months of expected egg collection,
shown as a dashed line continuing past your actual history. It starts
from published average annual laying rates for your configured breeds
(Isabrown ~300 eggs/year/hen, Sussex ~260 eggs/year/hen), then — once
you've logged at least one egg — scales that baseline by how your actual
collection over the last 30 days compares to it. There's no training
step: it's recomputed from scratch every time you open the Trends tab, so
it naturally tracks your flock's real performance (a hen going broody,
molting, or a new hen coming into lay) without you doing anything. The
forecast is intentionally a flat rate — it doesn't yet account for
seasonal changes in day length, so a projection made in summer may run a
little high once winter arrives, and vice versa.

The same dashed line also runs back through your history: for each past
month it shows what the forecast *would have* predicted using only the
data available at the time, next to what actually happened (also broken
out in the table's "Forecast" column). Early months, with little or no
prior data to work from, will tend to be less accurate; the forecast
should track closer to actual as more collection history builds up.

Tap the ⛶ icon on the chart to expand it to fill the screen (tap again,
or press Esc, to go back) — turning your phone to landscape while
expanded gives noticeably more width to read a long history at a glance.

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
- The "already notified today" guard is in-memory only (not persisted).
  If the add-on restarts shortly after sending today's reminder, it may
  send one extra duplicate that day; this is a deliberate simplicity
  tradeoff, not a bug.
- Requires the add-on's `homeassistant_api` permission (already granted
  in `config.yaml`), which lets it call Home Assistant's `notify` service
  directly — no long-lived access token setup needed on your end.
