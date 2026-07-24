# Changelog

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
