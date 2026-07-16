# Changelog

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
