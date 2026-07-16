# Coop Tracker

Log egg collection, coop cleaning, feeding, egg sales, and coop costs for
your chickens, right from your phone via the Home Assistant sidebar.

## Features

- Quick-add buttons for eggs, cleaning, feeding, sales, expenses, and
  eggs used/consumed
- Today / this-week egg counts, eggs on hand, last cleaning and feeding
  times
- Finances section: browse any month's revenue, costs, and net
- Recent activity history with filtering and delete
- Backup & Restore panel (download or restore the SQLite database)
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

## Configuration

- **currency**: `USD` (default), `EUR`, `GBP`, `DKK`, `SEK`, `NOK`, `CHF`,
  `CAD`, `AUD`, or `JPY`. Controls the symbol and decimal formatting used
  for revenue, costs, and net figures. Set it from the add-on's
  **Configuration** tab, then restart the add-on for it to take effect.
