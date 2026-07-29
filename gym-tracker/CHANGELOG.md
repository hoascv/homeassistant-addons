# Changelog

## 1.8.0

- **Garmin history, and honest handling of a watch that hasn't synced.** Your
  watch only reaches Garmin when it syncs with the phone app, which can lag by
  days. Three things went wrong with that before: a day Garmin had nothing for
  was stored as a row of blanks that **overwrote data already synced**; days
  that arrived more than 7 days late were never picked up at all; and the sheet
  only showed when the *add-on* last talked to Garmin, not when the *watch*
  last uploaded.
  - Only metrics Garmin actually returns are written, so an unsynced watch can
    no longer erase history you already have.
  - Each sync now chases holes up to 60 days back (nearest first), so a
    fortnight off the charger fills in rather than being lost.
  - Days with nothing are remembered as holes instead of stored as zeros, and
    are re-checked until your watch uploads again.
- **Last 14 days in the ⌚ sheet.** Sleep, stress or Body Battery as a bar per
  day. Days with nothing are drawn as hatched gaps, not zero-length bars, and
  say which kind of gap they are: **not synced** (after your watch's last
  upload) or **no data** (a day it wasn't worn).
- **The sheet now shows when your watch last uploaded**, with a nudge when it's
  been over ~36 hours, and the home card says **As of ‹date›** when the newest
  numbers aren't from today.

## 1.7.0

- **Expand the goal chart.** A ⤢ button on the chart opens it in a near
  fullscreen sheet, with extra gridlines and dates that only fit at that size.
  Turning the phone to landscape redraws it wider. The expanded chart is drawn
  at its real pixel size rather than scaled up from the card, so the extra room
  becomes more chart instead of bigger text.

## 1.6.0

- **Body-fat forecast on the goal chart.** The chart now has a second panel
  under the weight one, plotting logged body fat with its own least-squares
  trend projected to the same goal date, plus your body-fat target line. Both
  panels share one time axis and one hover crosshair, so weight and body fat
  read together; the tooltip shows both for a weigh-in that has them. Body fat
  gets its own panel rather than a second y-axis on the weight chart, because
  kg and % have no common scale — overlaid, where the two lines cross would be
  an artifact of the scaling. The panel only appears once you've logged body
  fat, and the trend needs two readings on different days.

## 1.5.0

- **Record which scale/device a weigh-in came from.** The Log weight form now
  has a **Scale / device** field (with suggestions from devices you've used
  before, pre-filled with your most recent one). It shows on each entry in the
  weight history and is editable there — so when you later weigh in on a more
  accurate machine, you can see the difference per device and correct earlier
  readings.

## 1.4.1

- **Fix Garmin connect failing with a 502 error.** Connecting saved the login
  token through the wrong internal object (`garmin.garth`), which doesn't exist
  in the current `garminconnect` library, so every successful sign-in (and every
  2FA completion) crashed. Tokens are now persisted via the library's own client
  and detected from the correct `garmin_tokens.json` file, so connecting — and
  staying connected across restarts — works.

## 1.4.0

- **Garmin Connect integration.** Connect your Garmin account (⌚ sheet) to
  pull in **sleep**, **stress**, **Body Battery** and **activities**. Sign in
  with email/password — including a **2-factor** code step when your account
  needs it — and only the refreshing login token is stored locally under
  `/data`; your password is never saved. Data syncs automatically in the
  background (`garmin_sync_interval_hours`, default 6h) plus a **Sync now**
  button, and a new home **Garmin** card shows your latest sleep/stress/Body
  Battery and recent activities. Two new options: **garmin_auto_sync** and
  **garmin_sync_interval_hours**. This is the only feature that contacts an
  external service.

## 1.3.2

- **Instant, optimistic challenge check-off.** Ticking a challenge item now
  updates the check, streak dots and **Recent workouts** the moment you tap —
  before the server replies — then reconciles with the server (and rolls back
  with a message if the request fails). The same applies to the **History**
  grid when backfilling past days.
- **Notes in Recent workouts.** Each entry in the **Recent workouts** card now
  shows its notes when present.

## 1.3.1

- **Recent workouts updates instantly.** Ticking off an exercise in the
  daily challenge logs a workout — that entry now appears in **Recent
  workouts** straight away, instead of only after a refresh. Un-ticking
  removes it just as quickly, and the same applies when backfilling past
  days from **History**.

## 1.3.0

- **Richer supplements.** A supplement now has a structured **dosage**
  (amount + unit, e.g. 500 mg), a **quantity per serving** (e.g. 2
  capsules), a **timing** tag (morning, pre-workout, …), and a **brand /
  notes** field — instead of a single free-text dose. The dose shown in the
  challenge is built from these (e.g. "2× 500 mg"). Editing a supplement now
  uses a proper form rather than pop-up prompts.
- Existing supplements upgrade automatically: the old free-text dose is
  parsed into amount + unit where possible.
- Fixed hidden "Cancel" buttons showing when they shouldn't in some forms.

## 1.2.1

- **Backfill any past day.** The challenge **History** view now has
  **From**/**To** date pickers, so you can look back past the default two
  weeks and tick off older days — handy for importing records you kept
  elsewhere. Range is capped at about a year at a time.

## 1.2.0

- **Weight forecast.** The goal card now projects your weigh-in trend to the
  target date and tells you whether you're **on track** — a badge (On track
  / Ahead / Behind / Off track) plus the weekly rate, the projected weight,
  and, when you're heading the right way, roughly when you'll hit the
  target. The chart draws a dashed **projection** line so you can see the
  trend meet (or miss) your target line. Needs at least two weigh-ins.
- Fixed the **Date** and **Note** fields overlapping in the *Log a set*
  sheet on iPhone/Safari.

## 1.1.0

- **Editable libraries.** The 🏋️ button now opens a **Library** with two
  tabs: **Exercises** and a new **Supplements** list (Creatine, protein
  powder, …). Add, rename, and remove entries in either.
- **Structured daily challenge.** Challenge items are no longer free text —
  each one is built from your libraries: pick an **exercise** (with a
  rep/set target) or a **supplement** (with a dose). This keeps the data
  clean and links the challenge back to your libraries, so renaming an
  exercise updates it everywhere.
- **Ticking an exercise logs a workout.** Checking off an exercise
  challenge item (e.g. 40 push-ups) now also records it in your workout
  history; un-checking removes that entry.
- **Edit your history.** A new **History** view on the challenge card lets
  you tap any past day to mark items done or not — handy for backfilling or
  fixing your streak. Workout entries can now be **edited** (sets, reps,
  weight, date), not just deleted.
- Existing installs upgrade automatically: the old default items become
  typed (Creatine, Push-up ×40, Squat ×40), and any earlier free-text
  items are set aside.

## 1.0.0

- First release. Track a body-weight / body-fat goal, a library of home
  workouts with per-session logging, and an editable daily challenge —
  all from your phone through Home Assistant ingress.
- **Goal:** set a target weight, body-fat %, and date; the home screen
  shows progress bars, lean mass, days remaining, and a weight chart with
  your target line.
- **Daily challenge:** tick off each item (starts as *5 g creatine,
  40 push-ups, 40 squats*); items are editable, and a streak counts the
  days you completed everything.
- **Exercises:** a preset library grouped by equipment (bodyweight,
  pull-up bar, dumbbells) that you can extend as you buy gear, plus
  set/rep/weight logging with history.
- **Two reminders** through a Home Assistant notify service: a daily
  challenge nudge and a weekly weigh-in reminder (configurable weekday and
  time). Both are off by default and set on the Configuration tab.
- **Per-user access control** (`restrict_to_user_ids`) and **backup /
  restore** of the database, mirroring the Coop Tracker add-on.
