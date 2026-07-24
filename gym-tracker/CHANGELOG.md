# Changelog

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
