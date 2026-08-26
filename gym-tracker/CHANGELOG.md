# Changelog

## 1.40.0

- **The exercise picker now puts what you actually do at the top.** Within each
  equipment group, exercises are ordered by how many times they have been
  logged, most first. An alphabet put "Arnold press" above the squat done three
  times a week; now the handful you really use rise to the top of their group.
- Everything never logged keeps its alphabetical order, and the equipment
  grouping itself is unchanged — only the order inside each group moves, so the
  list stays recognisable.
- Ordering is by **count, not recency**: a one-off yesterday does not displace
  something done every week.
- `/api/exercises` now returns `log_count` per exercise.
- The picker's cached list is invalidated after logging a set, so a set that
  changes the ranking is reflected without reloading the page.

## 1.39.1

- **The routine player went see-through once a routine started.** Its work and
  rest tints are translucent by design, but each was set as the background on
  its own, replacing the player's opaque one — so the app behind showed through
  and the two screens looked stacked. The tints are now layered over the page
  background: same colours, nothing showing through.

## 1.39.0

- **Challenge templates.** A new **Start from a template** button under the
  challenge list offers ready-made challenges. Starting one builds the whole
  thing: the challenge, its items, the routines behind them, and any exercises
  those routines need. Anything you already have — matched by name — is reused
  untouched, so a routine you have tuned keeps your steps.
- **The first template is Advanced Kegel**: a 30-day daily challenge with a
  fast-pulse warm-up (10 × 1s/1s), two endurance sets (10 × 10s hold / 10s
  rest), and a 20-round cool-down, with the technique notes attached.
- **A routine run more than once now says so.** A challenge item pointing at a
  routine reads "2 sets · 3m 20s · 10 rounds" instead of dropping the set count
  — the player counts a single run, so a two-set item that didn't mention its
  second set was hiding half the work.

## 1.38.1

- **The routine player wasn't keeping the screen awake.** The wake lock was
  requested a moment before the routine timer started, and the request declines
  when no routine is running — so it was refused every time and the screen slept
  mid-routine. It's now taken once the timer is running. Switching away from the
  page and back used to mask this by re-requesting the lock through a path that
  did hold it.

## 1.38.0

- **A stoic quote every morning at 07:00.** A new option, `stoic_quote_enabled`
  (on by default) with `stoic_quote_time`, sends one stoic line a day through
  your notify service. The quotes are walked in order rather than picked at
  random, so the same one never lands two mornings running — you see all of
  them before any repeats.
- The quote list now lives in the app rather than only in the browser, so the
  morning notification and the end-of-challenge celebration draw on one list.

## 1.37.0

- **Renamed to Goal Tracker.** The add-on name, sidebar entry, page title,
  notification titles, and log prefix now all say "Goal Tracker" — the app had
  grown well past the gym. Nothing about the install changed: the add-on slug
  is still `gym-tracker`, so your database, options, ingress URL, and the data
  pipeline keep working untouched across the update.

## 1.36.0

- **Added a Profile section** (Settings → Profile) to record the age, sex,
  and activity level set on your scale — for reference, since the app never
  reimplements a scale's own body-fat formula.
- **Added a body-fat calibration tool.** If you've changed your scale's
  activity-level setting, enter a few back-to-back readings (old setting vs.
  new) and the app computes the average offset between them, with a spread so
  you can judge how consistent it is.
- **Added a reversible bulk correction** for historical body-fat % entries
  recorded under the old setting: pick a cutoff date, apply the calibrated
  offset, and every affected entry shows its original value alongside the
  corrected one. Correction history is kept, and any correction can be
  reverted.

## 1.35.2

- **The daily-completion quote toast stays up 2x as long** — up to 14s for a
  full quote plus a streak line, instead of racing the same up-to-7s clock
  every other toast in the app uses. Scoped to that one message: `toast()`
  gained an opt-in `multiplier`, so "Set logged.", error messages, and
  everything else keep their existing, shorter timing.

## 1.35.1

- **The quote toast was gone before it could be read.** Toasts now stay up
  longer the more there is to read, instead of every message racing the same
  fixed 2.6s clock — a short one still clears quickly, a full quote gets up to
  7s.
- Fixed alongside: `APP_VERSION` in `app.py` had not been bumped with 1.35.0,
  so the app's own version endpoint still reported 1.34.0.

## 1.35.0

- **Finishing a challenge's day now leaves you with a line, not just a
  checkmark.** The toast that used to say "Day done 🎉" now carries a short
  Stoic quote — Marcus Aurelius, Seneca, Epictetus — alongside the streak,
  picked so the same one never shows twice in a row.
- **Clearing every challenge due on a given day gets a bigger moment.** When
  the last item across *all* of today's challenges gets ticked, a full-screen
  card replaces the small toast: a heavier confetti burst, a count of how much
  got done, and its own quote. It only fires on the tick that actually
  finishes the day, never on the way back down.
- Completing a routine through the ▶ player now shares this instead of its own
  separate "Done — streak" toast, so the moment is the same whether you tick
  the row by hand or count yourself through it.

## 1.34.0

- **An exercise can now be a routine, and the app counts you through it.** Give
  one steps in the 🏋️ Library — *30s jumping jacks, 15s rest, 45s plank* — set a
  round count, and press **▶** on its challenge card. The screen fills with the
  countdown, what you are doing, what is next, and a bar for the step and one for
  the whole thing. A step borrows an exercise you already have, with its name and
  picture, or is just something you type; rests need neither.
- **Finishing ticks the item and logs the workout**, with the seconds it actually
  took rather than the seconds it was designed to take. The guidance replaces the
  tap — you do not do both. Tapping the row still ticks by hand.
- **Stopping halfway logs what you did and leaves the item unticked.** The effort
  was real so it is kept; the day was not finished so nothing claims it was. It is
  logged as a manual entry on purpose: as a challenge entry a later un-tick would
  delete it, and work you genuinely did would vanish because of something you did
  afterwards.
- **A flash, a sound and a vibration** at every change, plus a countdown beep for
  the last three seconds. Each is a toggle, remembered per device. On an iPhone
  there is no vibration at all so that toggle is absent, and the ringer switch
  silences the beeps — which is why the player holds the screen awake while it
  runs.
- **The timer reads the clock rather than counting.** Look away, take a call, let
  the screen dim: coming back shows the right number immediately instead of
  however far behind the app fell — and it will not replay the beeps it missed
  while it was in the background.
- The **Tabata** button fills in 8 rounds of 20 seconds' work and 10 seconds'
  rest, which is the fastest way to see what a routine is. Every step runs
  including the last rest, so the total is always rounds × the round.
- Durations everywhere now read as *4m* rather than *240s*.
- Fixed alongside: deleting an exercise that is only used as a step in somebody's
  routine now archives it rather than hard-deleting it and leaving the step
  pointing at nothing.
- Needs `pipeline-airflow` 2.15.0, which carries the new table.

## 1.33.0

- **The challenges that owe you something today come first.** With several
  running, the ones resting today used to sit wherever they were created, and the
  card you actually opened the app to tick was the one you had to scroll for.
  Order is now: due today and unfinished, then due today and done, then resting,
  then not yet started. Stable within each group, so it does not reshuffle
  between visits.
- **A resting challenge folds its items away.** On a rest day the list is a wall
  of things you are *not* being asked to do; it now collapses to
  `3 items · nothing due today`, with the streak, the week dots and the next due
  date still on show. It only folds — a bonus session on a rest day is still
  worth ticking, so the items are one tap away, and unfolding survives the tick.

- Dropped `build.yaml` and named the base image directly in the `Dockerfile`.
  Supervisor 2026.04.0 stopped passing `BUILD_FROM` and now warns the file is
  deprecated — and an ARG that never arrives makes `FROM $BUILD_FROM` an *empty*
  base, so the next rebuild of this add-on would have failed. The file named the
  same `python:3.12-slim-bookworm` for both architectures, which one multi-arch
  tag already does.

## 1.32.2

- **Editing a challenge item's reps no longer wipes its sets.** The inline
  fields in **Edit items** each send one value, and the handler read anything
  absent as "clear it" — so changing a `3 × 40` item to 50 reps saved it as
  `50 reps`, with the sets silently gone. Duration behaved correctly already;
  sets and reps did not. Every target now keeps whatever was not sent, and an
  empty field still clears deliberately.

## 1.32.1

- **A workout's exercise can be changed when editing it.** It could not before:
  the form offered the choice and sent it, and the server quietly ignored that
  field — so picking a different exercise looked like it saved and changed
  nothing. Reported against a workout a challenge had created, but it affected
  every workout.
- Editing a **challenge-created** workout's exercise or date now detaches it
  from the challenge: it becomes an ordinary manual entry. It has to. Un-ticking
  the challenge deletes its row by item and day, so a row that was edited and
  left attached would be silently deleted later — and until then the log would
  claim a different exercise than the item it pointed at. Changing only the sets,
  reps or weight leaves it attached, because that is not a claim that it was a
  different exercise. The confirmation says which happened.
- Opening the edit form now shows the right fields for the exercise being
  edited. Selecting an exercise in script does not fire the `change` event the
  form listened for, so a timed exercise could open with a reps box, or a
  counted one without it.

## 1.32.0

- **`api_token` now actually protects the published port.** It never did. The
  docs have always said it "is the only thing protecting the API once you publish
  the port", but the token was only ever a *bypass* of the `restrict_to_user_ids`
  allowlist — and with that option at its default (empty), every endpoint
  answered an unauthenticated caller. Anyone who followed the documented advice
  and mapped a host port had `GET /api/export`, `GET /api/backup` (the entire
  database) and `POST /api/restore` open to whoever could route to it.
- The rule is now the one the docs described: a request that did not come through
  Home Assistant's ingress must carry a valid bearer token, **including when no
  `api_token` is configured** — "no credential is set" cannot mean "no check is
  needed". Refusals are `401` with a body saying what to set.
- **Ingress is unchanged.** The web UI, the sidebar panel and `restrict_to_user_ids`
  all behave exactly as before; the Supervisor has already authenticated the user.
- **If you use the Add-on Watchdog, copy this add-on's `api_token` into its
  `api_tokens` option**, or its Records column will go blank. The add-on still
  reports healthy — a 401 proves something is alive and enforcing — and the
  watchdog names the fix in its own log. Add-on Watchdog 1.10.3 recognises the
  401 for this.

## 1.31.1

- Documentation fixes. A code fence closed on the same line as the sentence that
  followed it, so that sentence rendered inside the code block.
- The dumbbell preset is "floor press", not "floor bench press".

## 1.31.0

- `/api/stats` now counts **every** table, not only the tracked ones, split into
  `counts` and `other_counts` with `total_all` alongside. Reporting a row count
  beside a whole-file size invited a division that did not hold — `change_log`
  and `app_state` take real space and appeared nowhere.
- `total` keeps its original meaning (tracked tables only), so a consumer
  written against 1.29.0 keeps its number.

## 1.30.0

- Dropped `armhf`, `armv7` and `i386` from `arch`. Supervisor now reports all
  three as deprecated; `aarch64` and `amd64` remain. If you run this on a
  32-bit ARM or x86 box, stay on 1.29.0.

## 1.29.0

- New `/api/stats`: row counts per tracked table, the database size and the
  current `max_seq`. `/api/export` already answered this, but only by
  serialising every row — megabytes to learn a dozen integers, which is no way
  to poll on a timer. The Add-on Watchdog reads it every scan.
- Counts cover the same tables as the change feed, so a number here and a
  number in the lakehouse are counting the same thing.

## 1.28.0

- **Finishing is celebrated.** Two moments, weighted differently so the frequent
  one doesn't wear out:
  - **The day** — ticking the last item sets off a short confetti burst and says
    what the streak is now. Only on the tick that completes the day, and never
    on the way back down: un-ticking is not an achievement.
  - **The challenge** — completing one that has an end date brings up the name
    and what you actually did: days completed, the rate, the longest streak.
- The end-of-challenge moment counts **the last day the moment it is finished**,
  not the following morning — finishing a 31-day challenge on the 31st should be
  congratulated while it still feels like one.
- It is also shown **once**, and is not lost if you were away. The server records
  when it was displayed rather than when the challenge ended, so a challenge that
  ran out while the app was closed is still celebrated next time you open it,
  and a reload doesn't replay it.
- Challenges that had already ended before this existed are not queued up: the
  migration marks them as already celebrated, or upgrading would have set off a
  run of them at once.
- Confetti is skipped for anyone whose system asks for reduced motion. The
  message and the numbers still appear — the celebration is the point, the
  animation is the decoration.

## 1.27.0

- **The add-on log now says whether token auth is usable.** A data pipeline that
  presents a bearer token gets the same 403 whether the token is wrong or no
  `api_token` is configured here at all — the two are indistinguishable from the
  caller's side. Startup now states which it is, with the token's length so it
  can be compared against the caller's copy. The value itself is never logged.
- `/api/debug` reports the same as `api_token_set`, `api_token_length` and
  `restrict_to_user_ids_set`.

## 1.26.0

- **Today is no longer counted as a missed day before it's over.** A challenge
  done yesterday and untouched this morning read *50% · 1 of 2 days*, with today
  already drawn as missed. The same card showed 100% at bedtime and half that at
  midnight, having done nothing wrong in between — an error that only ever ran
  in the discouraging direction, worst first thing in the morning.

  A due day that is today and unfinished is now **pending**: left out of the
  completion rate and out of each item's rate until it closes. The streak
  already worked this way; the rest of the card now agrees with it. Missing a
  day still costs, from tomorrow rather than from midnight.
- The adherence chart draws today in its own style — a dashed outline, with a
  **today** key in the legend. It is neither a miss nor a rest day: something is
  owed and there is still time, and all three now look different. The caption
  says *today still open* so the count explains itself.
- Volume is unaffected: a plank held this morning shows in the totals straight
  away, whether or not the day is finished.

## 1.25.1

- **Un-ticking something you've already done now asks first.** A challenge is a
  list you tap at speed, and clearing a tick throws away the day's credit — and
  for an exercise, the workout it logged along with any heart rate matched to
  it. The prompt says which of those applies. Ticking something still happens
  straight away.
- The same applies in the challenge history when clearing a past day.

## 1.25.0

- **Every recorded change now says who made it**: `user` for something done in
  the app, `automation` for the add-on's own background work, `migration` for an
  upgrade or one-off fix. It comes through on the change feed, so a pipeline can
  tell a figure you entered from one the software wrote.
- Changes recorded before this leave it empty rather than being guessed at.

## 1.24.0

- **Exercises can be timed rather than counted.** Each one in the 🏋️ Library
  now has a **reps / time** toggle. A timed exercise asks for a hold instead of
  repetitions everywhere: logging a set, setting a challenge target, and in the
  labels — *Plank · 1m 30s* or *3 × 45s* rather than *1×60*.
- **Plank is timed out of the box**, and an existing timed target that had to be
  written as reps is moved across on upgrade, so the number keeps meaning what
  you meant by it.
- Ticking a timed challenge item now logs the **hold**, so an auto-logged
  workout matches one you entered by hand — previously it recorded seconds in
  the reps field.
- Challenge volume counts held time alongside reps, instead of a plank
  contributing nothing.

## 1.23.2

- **Fix a token with non-ASCII characters breaking authentication.** A
  passphrase with an accent in it made every authenticated request fail with a
  server error rather than being accepted, because of how the comparison was
  done. Any characters work now.
- Documented that the token has no required length or format, with a note on
  generating a strong one — it's the only thing protecting the API if you
  publish the port.

## 1.23.1

- **The change feed's snapshot now names the column that identifies each row.**
  Without it a consumer had to guess, and guessing "the first column" is wrong:
  the JSON has its keys sorted alphabetically, so the id is rarely first. Rows
  would have been merged together on the wrong column.

## 1.23.0

- **A change feed, for loading this data somewhere else.** Every insert, update
  and delete on the tables worth analysing is recorded with a sequence number,
  so an external pipeline can ask "what changed since X?" instead of reloading
  everything. `/api/export` gives a full snapshot to start from, `/api/changes`
  the deltas after it.
- Deletes are included, which is the part a "last modified" column can't do —
  and this data deletes plenty: un-ticking a challenge item removes both the
  tick and the workout it logged.
- Reading it needs an **api_token** (Configuration tab) sent as
  `Authorization: Bearer …`, since a pipeline has no Home Assistant session.
  Optionally publish the port to reach it from outside Home Assistant.
- **Backups are now taken through SQLite's own snapshot mechanism** rather than
  streaming the file off disk, which could catch a background sync mid-write.

## 1.22.0

- **The weigh-in reminder can now run every day**, not just once a week. Set
  **weighin_reminder_weekday** to `daily` on the Configuration tab. It still
  stays quiet on any day you've already logged, and still only asks once.

## 1.21.0

- **Resting heart rate**, from Garmin, as a fourth metric in the ⌚ history
  alongside sleep, stress and Body Battery. It arrives in the same response the
  sleep data comes from, so it costs nothing extra, and past days are filled in.
- **The sleep score is no longer attempted beyond its one documented place.**
  The diagnostics settled it: some devices — including the one this was tested
  against — send no sleep score at all, anywhere in the response. Pretending
  otherwise meant re-asking Garmin about months of days for something that was
  never coming. Where a device does report one it's still read; where it
  doesn't, the field stays empty and the add-on stops asking.
- **Fix Copy in the Diagnostics panel.** The clipboard API only exists on a
  secure connection, which Home Assistant over a local address isn't — so it
  silently did nothing. It now falls back to the older copy command, and
  failing that selects the text so you can copy it yourself.

## 1.20.0

- **Diagnostics button in the ⌚ Garmin sheet.** Shows what Garmin actually
  answers for a day — the shape of it, not your data — so an empty metric can
  be worked out from evidence instead of guesswork. Previously this meant
  hand-editing a long URL.
- **Sleep score: the parser now looks inside lists too**, which it wasn't
  doing. That was a real gap — Garmin nests some of this structure in lists,
  and skipping them may be why the score was never found.
- **Days are no longer re-fetched forever over a missing sleep score.** Treating
  it as required meant every day without one counted as incomplete, so the
  backfill would keep re-asking Garmin about months of days for something some
  devices never report. If the diagnostics show the score is available, it goes
  back to being required.

## 1.19.0

- **Say when an item joined a challenge.** Each item in the items sheet now has
  an *In this challenge since* date. Days before it aren't expected to include
  that item, so adding one part-way through no longer marks every earlier day
  incomplete.
- This matters for challenges that predate the app recording when items were
  created: those items look like they were there from day one, so any day
  before you actually started doing them counts against you. Setting the date
  fixes it — and clearing the field goes back to the worked-out date, shown as
  the field's placeholder.
- The date you set wins over anything inferred, including an earlier tick.

## 1.18.1

- **Heart rates that were never really measured are now removed.** Before
  timestamps were recorded properly, an exercise filed at midday had its heart
  rate read over 11:30–12:00 — a real measurement of the wrong window. Those
  readings sit in the resting range and are indistinguishable from correct ones
  once stored, which makes them worse than no reading at all. They're cleared
  once, on upgrade; readings taken over a real window are untouched, and the
  sync was already refusing to make new ones.
- **Fix the sleep score never being recorded.** It was read from one fixed
  place in Garmin's response, which for some accounts holds nothing — so it
  came back empty every single day while sleep durations, stress and Body
  Battery all arrived fine. It's now looked for wherever Garmin puts it, falling
  back to the daily summary, and a day missing its score counts as incomplete so
  past days get filled in.
- `/api/garmin/diagnose` now reports sleep alongside Body Battery.

## 1.18.0

- **Pictures for your exercises.** Tap the thumbnail beside any exercise in the
  🏋️ Library to choose a photo; tap an existing one to replace or remove it.
  They show in the Library and beside exercise items on your challenge cards.
- Pictures are **resized in your browser** before upload (longest side 512 px,
  JPEG) — nothing large is ever sent or stored, and no image library is needed
  on the add-on side.
- They are stored **in the database, not as files**, so they ride along in a
  backup and come back on restore. A picture never leaves your Home Assistant
  instance.
- Uploads are checked by their actual bytes rather than their filename, so a
  file merely *named* `.png` can't be stored and served back.

## 1.17.0

- **Challenges can have a schedule.** As well as every day, a challenge can run
  **every N days** or on **certain days of the week** — Mon/Wed/Fri, weekdays,
  whatever you pick. Existing challenges are unchanged: they stay daily.
- Days it isn't scheduled are **rest days**. Nothing is owed, the streak
  survives them, the reminder stays quiet, and they're left out of the
  statistics. A Mon/Wed/Fri challenge kept perfectly now reads **100%** where
  it used to read 43% and nag four times a week.
- The card stays on Home on a rest day, marked **Rest day · next Wed**, with
  its items still tickable — a bonus session is recorded but can't move the
  numbers in either direction.
- Rest days are drawn as hatched gaps in the week dots and the adherence chart,
  the same way a Garmin day with no data is: nothing owed is not the same as
  nothing done.

## 1.16.0

- **Repeat a challenge.** Finished a 30-day run and want another? **Repeat this
  challenge** on its Trends card clones its items into a new challenge of the
  same length starting today, with the dates editable first. The original is
  untouched — this is a new run with its own record, not a reset.
- **Heart rate is now read over a training session, not per exercise.** Logging
  five exercises after one workout used to produce five overlapping windows
  over roughly the same period, each a slightly different answer to the same
  question. Exercises logged within 90 minutes of each other are now one
  session with one window, and every exercise in it reports that reading.
- **New Sessions card on Trends**: your last 14 days of sessions, each with the
  exercises in it, how long it ran, total reps and the session's heart rate.

## 1.15.0

- **Weight under each challenge's adherence bars.** The Trends card now draws
  your weigh-ins over exactly the days the bars cover, on the same positions,
  with the change across the period — so you can see what the scale did while
  you were (or weren't) keeping the challenge. Shown side by side, never as
  cause and effect: a handful of weigh-ins over a few weeks can't establish
  that one moved the other.
- **Items can be moved between challenges**, from the items sheet. A move ends
  the item's membership where it was and starts a fresh one in the destination,
  so the days you already ticked stay with the challenge that earned them
  rather than being dragged across. Targets and doses come with it, and the new
  item records which one it continues.

## 1.14.0

- **A weigh-in's date can be edited.** The date field in the edit form was
  being ignored — the update never touched the timestamp — so a weigh-in filed
  against the wrong day could not be corrected. Workout entries had the same
  defect in reverse: editing one that was logged today restamped it with the
  time you did the editing. Both now re-date only when the date actually
  changes, and leave the recorded time alone otherwise.
- **How far back Garmin gaps are chased is now configurable**, via
  **garmin_backfill_days** (7–730, default 60). Garmin keeps your history
  indefinitely, so raising this is how you pull in days from before the add-on
  was installed — it fills in over the following syncs, ten days at a time.
  The ⌚ sheet shows the reach it is currently using.

## 1.13.2

- **A challenge that finished on a perfect run no longer reports a streak of
  zero.** The streak was counted back from today, which for a finished
  challenge is past its end date; it is now counted from the challenge's last
  day.
- **Fix two crashes**: a non-numeric `challenge_id` on `/api/challenge/items`
  or `/api/challenge/history` returned a 500 instead of a 400.
- **Challenge volume is bounded by the challenge's own period**, like every
  other figure on the card — a finished challenge no longer keeps accruing
  reps from workouts logged after it ended.
- Challenge names are capped at 80 characters, and the heart-rate sync no
  longer re-reads the add-on options once per logged exercise.

## 1.13.1

- **Fix adding an item wiping a challenge's history.** Statistics judged every
  past day against the challenge's *current* items, so adding one today made
  every earlier day incomplete — a challenge with ten perfect days dropped from
  100% to 0%. Each day is now judged against the items that were part of the
  challenge *that day*.
  - An item added after the challenge was set up counts only from the day it
    was added. Items that were there at setup count from the start date, so a
    backdated challenge still covers the history you backfill into it.
  - Ticking an item on an earlier day says it applied then, and pulls its
    membership back to that day.
  - An **archived item keeps the days it was part of** instead of vanishing
    from the record, via a new `archived_at`. Items archived before this
    version have no archive time, so they stay excluded rather than being
    allowed to rewrite past days.
- Per-item rates are now measured over the days that item was actually part of
  the challenge (`days_member`), not over the challenge's whole run.

## 1.13.0

- **Run several challenges at once.** A challenge is now a named thing with its
  own items, its own streak and its own card on Home. Your existing items were
  moved into one called **Daily challenge**, backdated to your earliest tick so
  its statistics cover the history you already have.
- **Challenges can be time-boxed.** Give one a **start** and an optional **end
  date** — a 30-day run shows *day 12 of 30*, finishes on its own, and drops off
  Home while keeping its statistics. Leave the end date empty for an open-ended
  daily habit, which is how the existing one behaves.
- **Per-challenge statistics on Trends**: completion rate, current and longest
  streak, an adherence bar per day for the last 30 (all done / partly / missed,
  where a missed day is an empty track rather than a zero-height bar), a
  per-item breakdown showing which items you actually keep up with, and the
  volume logged through the challenge including heart rate.
- The daily reminder now names whichever challenges are still outstanding, and
  stays quiet about ones that have finished or haven't started.

## 1.12.0

- **Exercises, supplements and challenge items now record when they were
  created and last changed** (`created_at` / `updated_at`), including when they
  were archived. Rows that already existed keep both as empty: their real
  creation time was never recorded, and a made-up one would be worse.
- **Goal changes are kept.** The goal is a single row that edits overwrote, so
  changing your target left no trace it had ever been different. Every version
  is now appended to a `goal_history` table, readable at `/api/goal/history`.
  Re-saving the same numbers isn't recorded as a change.

## 1.11.0

- **Weigh-ins and workouts logged today now record the time**, not a midday
  placeholder. Both forms pre-fill today's date, and any entry carrying a date
  was stored at `T12:00:00` — so in practice *every* entry had a made-up time,
  which is no use for heart rate or for any analysis of when you train.
- **Entries filed against an earlier day still keep the midday placeholder**,
  because there's no way to know when they happened — and they're now marked as
  such with a new **`ts_exact`** column on `weight_logs` and `workout_logs`
  (`1` = the time is real, `0` = date only). Existing rows are marked on
  upgrade: everything at midday becomes `0`, anything else `1`, and the seeded
  starting weight `0` since it's a stand-in rather than a weigh-in.
- **Heart rate is no longer guessed for entries with a placeholder time.**
  Before, an entry stamped midday got a heart rate read from 11:30–12:00 — a
  real number for the wrong window. Those entries are now skipped outright
  rather than filled with something plausible but wrong.

## 1.10.0

- **Heart rate for the exercises you log.** Each logged exercise now shows the
  heart rate Garmin recorded while you were doing it — `♥ 131 avg · 141 max` —
  taken from the window ending when you logged it and running back by that
  entry's duration, or by **garmin_hr_window_minutes** (default 30) when it has
  none. You don't have to start anything on the watch: this reads Garmin's
  all-day heart rate.
- **It backfills.** If your watch hasn't uploaded yet, the exercise simply has
  no heart rate — never a zero — and later syncs fill it in, going back 21
  days. Entries are re-checked a few times and then wait for your watch to
  upload again.
- **Challenge ticks now record when you ticked them.** They were being written
  at a hardcoded midday, so there was no record of when anything was actually
  done and no window to read a heart rate from. Ticking today stores the
  moment; ticking an earlier day keeps the midday placeholder, since there's no
  way to know when it happened.

## 1.9.0

- **Trends tab.** The app now has **Home** and **Trends** tabs. The weight and
  body-fat chart, its projection and the on-track line have moved to Trends,
  leaving the Goal card on Home as the quick read: progress bars, lean mass, to
  target, days left and Log weight. The tab you were last on is remembered.

## 1.8.1

- **Fix Body Battery always reading "no data".** It was only ever read from
  Garmin's body-battery series, taking the level from a fixed column of each
  row. Accounts whose rows are laid out differently yielded nothing, so every
  Body Battery value was stored empty while sleep and stress worked fine. It is
  now read from Garmin's daily summary — the same numbers the Garmin app shows
  — and only falls back to the series if that comes up empty, honouring the
  column layout the response declares.
- **Existing days repair themselves.** The backfill used to chase only days
  with nothing stored, so days that already had sleep and stress would have
  kept their empty Body Battery forever. It now also revisits days missing a
  metric, filling them in over the next few syncs.
- **A day that synced without a metric no longer reads "not synced"** in the
  history — that wording is now only used for days your watch hasn't uploaded.
- New `/api/garmin/diagnose?day=YYYY-MM-DD` reports what each Body Battery
  source returns for a day, for when a metric goes quiet again.

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
