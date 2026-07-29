# Gym Tracker

Track a body-weight / body-fat goal, home workouts, and a daily challenge —
from your phone, through Home Assistant's sidebar (ingress). Everything is
stored locally in the add-on's own SQLite database; nothing leaves your
Home Assistant instance — with one opt-in exception, the **Garmin Connect**
sync, which reaches out to Garmin to pull your health data in (see below).

## The goal

Set a **target weight**, **target body-fat %**, and a **target date** in the
⚙️ settings sheet. The home screen then shows:

- A **weight** progress bar from your starting point to the target.
- A **body-fat** progress bar toward the target %.
- Your **lean mass** (computed from weight and body-fat, when you log a
  body-fat reading).
- **Days remaining** until the target date.
- A **weight chart** over time with a horizontal line marking the target, and
  — once you've logged body fat — a second panel below it charting body fat %
  against your target. The two panels share one time axis and one hover
  crosshair, so weight and body fat read together. The **⤢** button opens the chart
  near fullscreen, where it gets more gridlines and dates; turn the phone to
  landscape for a wider view.

The add-on ships seeded for a bulk / recomposition goal — starting at
**99.7 kg on 3 Jul 2026**, targeting **105 kg at 15 % body fat by
28 Dec 2026** — so gaining weight and lowering body-fat both read as
progress. Change any of it in settings.

All weights are in **kilograms**.

### Are you on track? (forecast)

Once you have **at least two weigh-ins**, the goal card fits a straight-line
trend through your weights and projects it to your target date. You get:

- A **badge** — *On track*, *Ahead*, *Behind*, or *Off track*.
- Your current **weekly rate** and the **projected weight** at the target
  date; when you're trending the right way, it also estimates roughly when
  you'll reach the target.
- A dashed **projection line** on the chart, so you can see your trend meet
  (or miss) the target line.
- The same straight-line projection for **body fat**, on its own panel, once
  you have two body-fat readings on different days. It's fitted independently
  of the weight trend (body fat is logged less often) but runs to the same
  target date.

The forecast is a simple linear trend — good for spotting whether the last
few weeks point at your goal, not a precise prediction. More weigh-ins make
it steadier.

## Logging weight

Tap **Log weight** on the home screen. Enter your weight and, optionally, a
body-fat percentage, the **scale / device** you measured on, and a note. The
weight sheet lists every reading and lets you edit or delete past entries.

The **scale / device** field remembers the devices you've used (and pre-fills
your most recent one), so you can tell readings from a home scale apart from a
more accurate machine. When you later measure on a better device, compare the
two and edit earlier entries to correct them.

## The library: exercises and supplements

The 🏋️ button opens your **Library**, with two tabs:

- **Exercises** — home exercises grouped by the equipment they need:
  - **Bodyweight** — push-up, squat, lunge, plank, glute bridge, and more.
  - **Pull-up bar** — pull-up, chin-up, hanging knee raise.
  - **Dumbbells** — curl, shoulder press, floor bench press, row, goblet
    squat, Romanian deadlift, lateral raise.
- **Supplements** — things you take, each with a **dosage** (amount +
  unit, e.g. 500 mg), a **quantity per serving** (e.g. 2 capsules), an
  optional **timing** (morning, pre-workout, …), and a **brand / notes**
  field. Starts with Creatine and protein powder. When you add a supplement
  to the challenge, its dose is filled in from these (e.g. "2× 500 mg").

Add, **rename** (✎), or remove entries in either tab. Tap an exercise to
**log a set** (sets × reps, with optional weight or duration); each exercise
keeps a short history of what you logged.

## The daily challenge

The challenge is a checklist you complete each day, **built from your
library** — every item is either an exercise (with a rep/set target) or a
supplement (with a dose), so there's no loose free text to keep tidy. It
starts as:

- Creatine · 5 g
- Push-up · 40 reps
- Squat · 40 reps

Tick each item off as you do it. A **streak** counts the consecutive days
on which you completed *every* active item. Use **Edit items** on the
challenge card to add items (choose an exercise or supplement from your
library), change a target or dose, or remove one — removing keeps your past
streak intact.

Ticking off an **exercise** item also records a workout in your history
using its target sets/reps; un-ticking removes that entry again. Supplement
items don't create workouts.

### Editing history

**History** on the challenge card opens a day-by-day grid. Tap any dot to
mark that day's item done or not — useful for backfilling a day you forgot
to log, or correcting your streak. It defaults to the last two weeks; use
the **From**/**To** date pickers to go further back and import older records
you kept elsewhere (up to about a year at a time). In an
exercise's or the workout log's history, tap **Edit** on any manually
logged workout to change its sets, reps, weight, or date. (Workouts created
by the challenge check-off are managed by the challenge and marked
accordingly.)

## Reminders

Gym Tracker can send two reminders through a Home Assistant **notify
service** (e.g. a mobile-app notification). Both are **off by default** and
configured on the add-on's **Configuration** tab:

- **Challenge reminder** — a daily nudge at `challenge_reminder_time`. It
  only fires if you haven't already completed the whole challenge that day.
- **Weigh-in reminder** — fires weekly on `weighin_reminder_weekday` at
  `weighin_reminder_time`, and is skipped if you already logged a weight
  that day.

Set **notify_service** to the service name to use (without the `notify.`
prefix, e.g. `mobile_app_pixel`). The in-app 🔔 reminders sheet shows the
current status, lets you pick from your available notify services, and can
send a **test** notification.

## Garmin Connect

Connect your Garmin account to pull in **sleep**, **stress**, **Body
Battery** and **activities**. Open the ⌚ Garmin sheet and sign in with your
Garmin email and password. If your account uses **2-factor authentication**,
you'll be asked for the code Garmin sends — enter it and the connection
completes.

Your **password is never stored** — only the resulting login token is saved,
locally, under the add-on's `/data` directory (so it survives restarts and is
included in Home Assistant backups). The token refreshes itself, so you
normally only sign in once.

Once connected, Gym Tracker syncs automatically in the background (every
`garmin_sync_interval_hours`, default 6), and the ⌚ sheet has a **Sync now**
button for an immediate pull. The home **Garmin** card shows your latest sleep,
stress and Body Battery, plus recent activities. Use **Disconnect** to remove
the stored token; already-imported data stays.

### History, and a watch that hasn't synced

Every day pulled is kept, building up a history you can scroll back through —
the ⌚ sheet charts the **last 14 days** of sleep, stress or Body Battery.

Your watch only reaches Garmin when it syncs with the Garmin Connect phone
app, which can lag by days. Gym Tracker treats that as normal rather than as
missing data:

- Each sync refreshes the last **7 days** and then chases any **holes** up to
  **60 days** back, so days that arrive late still land in your history. A
  fortnight off the charger fills in on the next sync.
- A day Garmin has nothing for is **never stored as zeros**, and a day already
  synced is never overwritten with blanks — so an unsynced watch can't erase
  history you already have.
- The sheet shows when your **watch last uploaded**, separately from when the
  add-on last talked to Garmin. Those are different things: the add-on can sync
  happily every 6 hours against a watch that stopped uploading on Tuesday.
- In the history, days after that last upload read **not synced**; earlier days
  with nothing read **no data** (a day the watch wasn't worn). The home card
  says **As of ‹date›** whenever the newest numbers aren't from today.

Empty days are re-checked a few times and then left alone until your watch
uploads again, which is the only thing that can turn them into real data.

Days missing one metric are chased the same way as days missing entirely, so a
metric that starts working fills in backwards through your history over the
next few syncs rather than only from today on.

If a metric goes quiet, `/api/garmin/diagnose?day=YYYY-MM-DD` (append it to the
add-on's URL) reports what each Garmin source returns for that day.

This is the only feature that contacts an external service. It uses Garmin's
unofficial API, so an occasional sync error (shown in the sheet) is normal;
the next sync usually recovers.

## Configuration

- **notify_service**: the Home Assistant notify service used for reminders,
  without the `notify.` prefix (e.g. `mobile_app_myphone`). Leave empty to
  disable all notifications.
- **challenge_reminder_enabled**: turn the daily challenge reminder on/off.
- **challenge_reminder_time**: 24-hour `HH:MM` time for the challenge
  reminder (default `18:00`).
- **weighin_reminder_enabled**: turn the weekly weigh-in reminder on/off.
- **weighin_reminder_weekday**: the day of the week the weigh-in reminder
  fires (default `sunday`).
- **weighin_reminder_time**: 24-hour `HH:MM` time for the weigh-in reminder
  (default `08:00`).
- **restrict_to_user_ids**: comma-separated Home Assistant user IDs allowed
  to open the add-on. Empty (default) means any user who can see the
  sidebar entry may use it. Find your own user ID in the ⚙️ settings sheet.
- **garmin_auto_sync**: sync Garmin data automatically in the background
  (default `true`). Turn off to only sync when you press **Sync now**.
- **garmin_sync_interval_hours**: how often the background sync runs, in
  hours, 1–168 (default `6`). Only applies when `garmin_auto_sync` is on.

## Backup & restore

The ⚙️ settings sheet can **download a backup** of the whole database (a
`.db` file) and **restore** one you previously downloaded. Home Assistant's
own scheduled backups also include this add-on's data automatically.

## Access control

By default only Home Assistant **admin** users see the add-on in their
sidebar. To restrict further to specific people, add their user IDs to
**restrict_to_user_ids** (comma-separated). Everyone else gets an
"access restricted" page. Add your own ID — shown in the settings sheet —
before restricting, so you don't lock yourself out (recoverable by clearing
the option on the Configuration tab).
