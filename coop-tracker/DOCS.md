# Coop Tracker

Log egg collection, coop cleaning, and feeding for your chickens, right from
your phone via the Home Assistant sidebar.

## Features

- Quick-add buttons for eggs, cleaning, and feeding
- Today / this-week egg counts, last cleaning and feeding times
- Recent activity history with filtering and delete
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

No configuration options are required.
