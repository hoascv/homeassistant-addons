# Changelog

## 1.23.0

- The overdue-eggs reminder's "already notified today" guard is now stored
  in the database instead of only in memory, so restarting the add-on
  shortly after a reminder went out no longer sends a duplicate that day.
  This closes a previously documented known limitation.

## 1.22.1

- Fixed chicken photos not updating after a re-upload: the photo URL
  doesn't change when you replace a chicken's picture, so the browser
  could keep serving the previously cached image instead of the new one
  until a hard refresh. The photo endpoint now tells the browser not to
  cache it.

## 1.22.0

- Added a **Given away** checkbox to the Log Used sheet, for eggs you hand
  off rather than eat yourself. Given-away eggs still count against "eggs
  on hand" like any other used egg, but are excluded from the Finances
  section's "Est. savings" figures, since giving eggs away doesn't reduce
  your own grocery spending.

## 1.21.1

- The "Est. savings" price option is now **supermarket_egg_price** — a
  price per single egg (default `2.5`) instead of per dozen. If you'd
  already set **supermarket_egg_price_per_dozen** in 1.21.0, that option
  is no longer read; set the new one to what a single egg costs you
  instead (e.g. a dozen at 30 becomes `2.5`).

## 1.21.0

- Added **Est. savings** to the Finances section: what your used eggs
  would have cost at supermarket prices, for the current month and
  all-time — new **supermarket_egg_price_per_dozen** option (default
  `30`) to match your local price. Only counts eggs logged as used, not
  sold, so it doesn't double up with the revenue you already track.

## 1.20.0

- Chicken records in **My Flock** can now have a photo — pick one from
  your phone in the chicken form (auto-resized before saving, so it
  won't bloat the database), shown as a thumbnail in the list. Removing
  a chicken's photo is one tap away too.

## 1.19.0

- Added a small red/green connection status dot next to the top bar's
  icons — green when Home Assistant is reachable, red when it isn't. Tap
  it to jump straight to the full Debug info detail (already in the 🔔
  Notifications panel) instead of having to dig for it.

## 1.18.0

- Added **My Flock** (🐔 icon): track individual chickens — name, breed,
  hatch date, active/lost status — instead of just a flat count per
  breed. Breeds (Isabrown/Sussex by default, each with a published
  eggs/year estimate) are also editable, so you can add any breed you
  keep.
- The egg collection forecast now uses each active chicken's actual age
  once you've added at least one: no eggs before ~20 weeks old, full rate
  through ~18 months, a reduced rate after — instead of a flat per-breed
  count. Falls back to the previous flat-count method
  (`flock_isabrown_count`/`flock_sussex_count`) if no chickens are added.
  The forecast backtest (what it would have predicted for past months)
  now also uses each bird's age as of that past month.

## 1.17.0

- Added a **Feed refill cadence** table to the Trends tab: every food
  type you've logged, with its all-time average days between refills,
  days since last emptied, and times fed — a one-screen comparison across
  all your feeds, instead of checking them one at a time in the Log
  Feeding sheet.

## 1.16.0

- Food types are now stored in the database and editable from the app: a
  new **Manage list** link next to the Food type dropdown on the Log
  Feeding sheet lets you add or remove entries yourself, instead of a
  fixed built-in list. Removing one only affects future entries — nothing
  already logged is changed.
- Fixed a bug where, after updating the add-on to a new version,
  Home Assistant's browser/webview could keep showing the previous
  version's UI (e.g. still showing the old free-text Food type field)
  until a manual hard-refresh, because the app's JS/CSS files had no
  cache-busting. They're now tagged with the running version, so a new
  version is always fetched fresh after an update — no manual refresh
  needed.

## 1.15.0

- Food type on the Log Feeding sheet is now a fixed dropdown (Layer feed,
  Pellets, Scratch grains, etc.) instead of free text, pre-filled with
  whatever you used last time — guarantees consistent spelling, which is
  what the feed-duration estimate's history grouping depends on. Entries
  logged before this change with a food type not on the list keep showing
  their original text rather than having it silently swapped out.

## 1.14.0

- Added a **Container was empty** checkbox to the Log Feeding sheet. Once
  logged twice for the same food type, the sheet shows a live estimate —
  right there while you're logging — of the average days between refills
  and days since the last one, to help gauge how long a bag/container of
  feed typically lasts.

## 1.13.1

- Fixed a bug where logging, editing, or deleting an entry could silently
  fail — the app would close the entry sheet as if it had saved even when
  the request actually failed (e.g. a brief network hiccup, often after
  the phone had been idle for a while), so the entry never showed up on a
  later refresh with no error shown. Failed saves now show a clear error
  and keep the entry sheet open with your input intact, instead of
  discarding it silently.
- Fixed a related issue where, if `ha_sensors_enabled` was on, a slow or
  unreachable Home Assistant could make a simple "log an egg" request
  hang for up to ~45 seconds (9 sequential HA API calls, 5s timeout each)
  before it either succeeded or errored. That push now always runs in the
  background instead of blocking the response — saving an entry is no
  longer affected by whether Home Assistant is reachable at that moment.

## 1.13.0

- Added an expand (⛶) button to the Trends chart to view it full-screen —
  tap again or press Esc to go back. Especially useful in landscape,
  which gives a long history much more room to read.

## 1.12.0

- The Trends tab chart is now a line chart instead of a grouped bar chart.
- Added a forecast backtest: the dashed forecast line now runs back
  through your history too, showing what it would have predicted for each
  past month using only the data available at the time — next to what
  actually happened, so you can see how well it's tracking. Also shown as
  a new "Forecast" column in the table.

## 1.11.0

- Added a 3-month egg collection forecast to the Trends tab, shown as
  lighter bars after your actual history. It's based on published laying
  rates for your flock's breeds (new **flock_isabrown_count** /
  **flock_sussex_count** options, defaulting to 3 and 2), scaled by your
  actual collection over the last 30 days once you've logged at least one
  egg — so it adapts to your real flock without any manual retraining.

## 1.10.0

- Added a new **Trends** tab (bottom navigation) with a monthly bar chart
  and table of eggs collected, sold, and used, so you can see how they
  trend over time instead of just the current totals. Choose a 3, 6, or
  12-month window from the dropdown.

## 1.9.0

- The Finances section now also shows an "All time" revenue/costs/net
  overview below the per-month figures, so you don't have to page through
  every month to see the running total.

## 1.8.0

- Added optional Home Assistant sensor integration: when `ha_sensors_enabled`
  is turned on, Coop Tracker pushes egg counts, last cleaning/feeding times,
  monthly finances, and an "eggs overdue" binary sensor into Home Assistant
  as real entities (`sensor.coop_tracker_*` / `binary_sensor.coop_tracker_*`),
  so they can be used on dashboards and in automations — not just the
  existing one-way push notification. Uses the same Supervisor API access
  already granted via `homeassistant_api`; no MQTT broker needed. Entities
  update immediately after logging/editing/deleting an entry, and once a
  minute in the background otherwise.
- The 🔔 Notifications panel's "Debug info" section (and the startup log
  line) now also shows the running add-on version.

## 1.7.0

- Added a "Debug info" section to the 🔔 Notifications panel (collapsed
  by default): container time/timezone, whether `SUPERVISOR_TOKEN` is
  set, whether the Home Assistant API is reachable (with the error if
  not), database path/health, and Python/Flask/platform versions.
- The same key facts are now printed to the add-on's Log tab on every
  startup, so most connectivity issues can be diagnosed without opening
  the app at all.

## 1.6.1

- Fix `SUPERVISOR_TOKEN` not being visible to the app, which broke push
  notifications and the notify-service discovery list even with
  `homeassistant_api: true` granted. The base image's s6-overlay v3 does
  not expose the container's environment variables to a script unless it
  explicitly requests them via `with-contenv`; `run.sh` now does.

## 1.6.0

- Added a push notification reminder: if no eggs have been collected in
  a configurable number of days (default 2), Coop Tracker sends a push
  notification to your phone via the Home Assistant Companion App, once
  a day at a configurable check time. No Home Assistant Automation
  needed — configure `reminder_enabled`, `reminder_check_time`,
  `reminder_threshold_days`, and `notify_service` on the add-on's
  Configuration tab.
- New 🔔 Notifications panel: shows current reminder settings, lists
  discovered `notify.*` services to help you find your phone's exact
  service name, and includes a "Send test notification" button.
- Requires the add-on's new `homeassistant_api` permission to call Home
  Assistant's `notify` service directly.

## 1.5.1

- Changed the default currency to DKK ("kr").

## 1.5.0

- Added a "Currency" configuration option (add-on Configuration tab):
  USD, EUR, GBP, DKK, SEK, NOK, CHF, CAD, AUD, or JPY. Revenue, cost, and
  net figures are formatted accordingly (symbol placement and decimals
  included). Restart the add-on after changing it.

## 1.4.0

- Added a "Log Used" action to track eggs you consume yourself; "Eggs on
  hand" now correctly subtracts both sold and used eggs from eggs
  collected.
- The Finances section can now browse past months (‹ / › navigation)
  instead of always showing the current month only.

## 1.3.0

- Added egg sales tracking (Log Sale: quantity + price received) and coop
  cost tracking (Log Expense: category + amount spent).
- New Finances section: eggs on hand, and this month's revenue, costs,
  and net.
- Existing databases are migrated automatically (new columns are added
  on startup and after a restore).

## 1.2.1

- Added an egg icon (`icon.png`) shown in the Home Assistant add-on store
  and add-on page.

## 1.2.0

- Added a Backup & Restore panel (gear icon in the top bar): download the
  raw SQLite database at any time, or restore from a previously downloaded
  backup file. Restore validates the file before replacing existing data.

## 1.1.1

- Fix `s6-overlay-suexec: fatal: can only run as pid 1` startup crash by
  disabling Supervisor's own init wrapper (`init: false`), since the base
  image already provides s6-overlay as PID 1.

## 1.1.0

- Entries can now be logged with a custom date/time (for retroactive logging).
- Tap any history entry to edit its date, time, or details.

## 1.0.0

- Initial release: egg, cleaning, and feeding logging with mobile-first UI.
