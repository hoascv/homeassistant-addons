# Changelog

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
