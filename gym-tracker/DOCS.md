# Goal Tracker

Track a body-weight / body-fat goal, home workouts, and a daily challenge —
from your phone, through Home Assistant's sidebar (ingress). Everything is
stored locally in the add-on's own SQLite database; nothing leaves your
Home Assistant instance — with one opt-in exception, the **Garmin Connect**
sync, which reaches out to Garmin to pull your health data in (see below).

## Tabs

**Home** is the day-to-day view: goal progress, the daily challenge, recent
workouts and Garmin. **Trends** holds the weight and body-fat chart with its
projections. The app reopens on whichever tab you used last.

## The goal

Set a **target weight**, **target body-fat %**, and a **target date** in the
⚙️ settings sheet. The app then shows:

- A **weight** progress bar from your starting point to the target.
- A **body-fat** progress bar toward the target %.
- Your **lean mass** (computed from weight and body-fat, when you log a
  body-fat reading).
- **Days remaining** until the target date.
- A **weight chart**, on the **Trends** tab, with a horizontal line marking the target, and
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

### What the trend is fitted through

The line is fitted over your **last 28 days**, not over everything you have ever
logged. A projection four months out is a statement about the current trend, and
a fit spanning months lets a starting point you have long since moved past pull
on an answer about December.

If 28 days does not hold at least four readings — which is normal for body fat —
the most recent four are used instead, whatever their age. A slightly stale fit
beats refusing to answer.

### What the two projections imply together

Weight and body fat are trended separately, and the card states what the pair
implies in kilograms:

> Together these imply −3.4 kg fat, +2.1 kg lean.

That is a more checkable statement than two percentages, because you can weigh
it against what you know about how fast tissue actually changes.

It is also checked. Two independently fitted trends can imply something
impossible — gaining weight while body fat falls steeply means adding lean mass,
and there is a limit to how fast that happens. If the implied gain exceeds about
a kilogram a month, the card says so and says the projection is what is wrong:

> Together these imply +13.5 kg of lean mass (3.4 kg/month), which is faster
> than is generally achievable — so one of the two trends is over-reading
> rather than this being on the cards.

Neither fit is quietly adjusted to make the numbers agree, because there is no
way to tell which of the two is over-reading. Showing an unreasonable figure and
naming it is more useful than reshaping it into a plausible-looking one.

Only a fast *gain* is flagged. Losing lean mass quickly is entirely possible.


### The band around the projection

The dashed trend is drawn inside a faint **95% band** — narrow where your
readings are, flaring as it projects forward. That widening is the honest shape
of an extrapolation: four months predicted from four weeks of weigh-ins is far
less certain at the end than at the beginning, and a plain dashed line had no
way of saying so.

It is a confidence interval for the **trend**, not a prediction of what the
scale will read on a particular morning. The question a goal asks is "where is
this heading", so the band shows where the underlying line plausibly sits. A
prediction interval would additionally carry the two or three kilograms of
day-to-day water movement, and would be so wide as to say nothing.

Read it as: *the narrower the band, the more the projected figure is worth.* A
band that fills the panel means the number at the end of the dashed line should
not be planned around.

It needs at least three readings, and it is drawn only when there is a trend to
draw — see below.


### "Too early"

Sometimes the honest answer is that there is no trend yet.

Weight carries two or three kilograms of day-to-day water movement, while a
deliberate gain or loss is a few hundred grams a week. When the fitted slope is
smaller than its own statistical uncertainty, the *direction* is not established
by the readings — the line has a sign, but next week's weigh-in could reverse it.

In that case the card says so rather than choosing a verdict:

> **Too early** · Weight is moving less than the scatter in the readings
> (−0.09 kg/wk over 26 days). No trend to project yet — keep logging.

No projected figure is quoted, on purpose: printing a number would lend a
figure that is about to change sign the authority of a forecast. The badge is
the quietest on the card, because this is a statement about the data rather
than bad news about you.

It needs at least three readings. A line through two points passes through both
exactly, so its uncertainty is unknown rather than zero.


**Body fat gets its own line**, directly under the weight one and reading the
same way — trend per week, where it lands by the target date, and what rate
would be needed instead:

> **Behind** · Body fat trending −0.33 %/wk, projected 23.6 % by target.
> Need −0.74 %/wk to reach 15 %.

Its band is wider than the weight forecast's: half a percentage point either
side of the target rather than 0.3. Body fat is measured far less precisely
than weight — a bioimpedance scale drifts with hydration by more than a few
tenths across a single morning — so a tighter band would change the badge on
water rather than on progress.

The required rate is measured from your **latest reading**, not from the fitted
line, so it answers "what has to happen from here" rather than "from where the
regression believes you are".

With no body-fat target the trend is still drawn and stated, but no verdict is
given — there is nothing to be behind. With no body-fat readings at all the
line is hidden entirely rather than nagging under a goal you never set.


## Meals

A card on **Home** with a row per meal and two buttons: **Ate** and **Skipped**.

This is adherence, not nutrition. There are no calories, no portions and no food
database, deliberately — a field nobody fills in honestly is worse than no
field, because it still looks like evidence.

### The three states

| | |
|---|---|
| **Ate** | You recorded that the meal happened |
| **Skipped** | You recorded that it did not |
| *Nothing* | You recorded nothing — and this is never read as either |

That third state is the point. If a missing record counted as a skip, every day
you did not open the app would read as skipping every meal, and a fortnight away
would become the worst run in your history — which would ruin the very thing
this is for, namely looking at the weight chart and knowing whether you were
eating.

So the card always says **"2 of 3 recorded"** rather than a bare count, and the
skip rate is out of what you actually recorded, not out of what you could have.
A separate **coverage** figure says how much of the window has any record at
all, so you can judge how much weight the numbers carry.

### Using it

- Tap **Ate** or **Skipped**. Tapping again on the same button **clears** the
  record and returns the meal to *nothing* — the way to undo a mis-tap without a
  third button. That is not the same as logging a skip.
- Meals can carry an optional **note** ("skipped lunch, long meeting"). A run of
  skips is hard to interpret months later without one.
- A **streak** counts consecutive days where every configured meal was eaten. A
  day with anything unrecorded ends it rather than being skipped over — counting
  it would make the streak a measure of how often you opened the app.

### On the weight chart

Every day with an explicit skip gets a short tick at the foot of the weight
panel, in the same amber as the **Skipped** button. Hover one to see which meals.

This is the reason the feature exists. Weight swings of two or three kilos
between consecutive morning weigh-ins are usually glycogen and the water bound
to it — roughly 3 g of water per gram of glycogen — rather than anything
happening to fat or muscle. Under-eating for a day partially empties that store
and the scale drops; eating normally refills it and the scale jumps back. With
the ticks in place you can see whether your low readings are the mornings after
skip days, instead of arguing about it from memory.

A gap in the ticks means **nothing was recorded**, not that you ate. The chart
cannot tell you about days you did not log.

### Configuring the meals

The `meals` option is free text, comma-separated, defaulting to
`Breakfast, Lunch, Dinner`. Four meals, or calling the evening one *tea*, is
entirely up to you.

Renaming or removing a meal **keeps its history**: anything logged under the old
name still shows, marked *retired*, and does not count towards today's total.

## Logging weight

Tap **Log weight** on the home screen. Enter your weight and, optionally, a
body-fat percentage, the **scale / device** you measured on, and a note. The
weight sheet lists every reading and lets you edit or delete past entries.

The **scale / device** field remembers the devices you've used (and pre-fills
your most recent one), so you can tell readings from a home scale apart from a
more accurate machine. When you later measure on a better device, compare the
two and edit earlier entries to correct them.

## The library: exercises and supplements

### The order exercises appear in

Exercises are grouped by equipment, and **within each group the ones you log
most often come first**. An alphabet is the wrong order for a list you scroll
while logging: it puts *Arnold press* above the squat you do three times a week.

Only what you have actually logged moves. Anything never logged keeps its
alphabetical place, and the equipment groups themselves do not reorder — so the
list stays recognisable, it just brings your handful of regulars to the top of
each group. The ranking is by **how many times** something has been logged, not
by how recently, so a one-off yesterday does not displace a weekly staple.

### Counted or timed

Some exercises are held rather than repeated. Each exercise in the 🏋️ Library
has a **reps / time** toggle: switch it to *time* and everything asks for a hold
instead of repetitions — logging a set, setting a challenge target, and the
labels, which read *Plank · 1m 30s* or *3 × 45s*.

**Plank** is set to time already. If you had a timed target written as reps
(*1×60* for a minute's plank), it's moved across automatically the first time
this version runs.

### Routines — being counted through it

Some exercises aren't one number. *30 seconds of jumping jacks, 15 seconds' rest,
45 seconds of plank* is one thing you do, and the app can count you through it.

Press **⏱** on any exercise in the 🏋️ Library to give it steps. Each step is
either **an exercise** you already have (it borrows the name and picture), **something
else** you just name, or a **rest**. Set **rounds** to repeat the whole list —
the **Tabata** button fills in the classic 8 rounds of 20 seconds' work and 10
seconds' rest, which is the quickest way to see what a routine is.

Every step runs, including a final rest, so the total is always rounds × the
round: 8 × (20s + 10s) is four minutes exactly.

**Running one.** A routine on a challenge card gets a **▶**. Tap it and the
screen fills with the countdown, what you're doing now, what's next, and two
bars — one for the step, one for the whole routine. **Pause**, **Skip** and
**Stop** are there throughout.

- **Finishing ticks the item and logs the workout**, with the seconds it actually
  took. The guidance replaces the tap; you don't do both.
- **Stopping early logs what you did and leaves the item unticked.** The effort
  is real, so it's kept — but the day isn't done, so it doesn't say it is.
- Tapping the row itself still ticks by hand, exactly as before.

**Cues.** Three toggles on the start screen — a **flash**, a **sound** and a
**vibration** at each change, plus a countdown beep for the last three seconds.
Turn any of them off; the choice is remembered on that device. Two things worth
knowing on an iPhone: there's no vibration at all (the toggle is simply absent),
and the ringer switch silences the beeps — so the screen carries it, which is
why the player keeps your screen awake while it runs.

**If you look away**, the timer keeps going and stays correct — it reads the
clock rather than counting, so coming back to the app shows the right number
immediately. It won't replay the beeps you missed while it was in the background.

### Pictures

Every exercise can have a picture. In the 🏋️ Library, tap the thumbnail beside
an exercise to choose one from your phone or computer; tap an existing picture
to replace or remove it. Pictures show in the Library and next to exercise items
on your challenge cards.

They're resized in your browser before being sent (longest side 512 px), and
stored inside the add-on's database — so they're included in a **backup** and
come back on **restore**, and never leave your Home Assistant instance. JPEG,
PNG and WebP are accepted.

The 🏋️ button opens your **Library**, with two tabs:

- **Exercises** — home exercises grouped by the equipment they need:
  - **Bodyweight** — push-up, squat, lunge, plank, glute bridge, and more.
  - **Pull-up bar** — pull-up, chin-up, hanging knee raise.
  - **Dumbbells** — curl, shoulder press, floor press, row, goblet
    squat, Romanian deadlift, lateral raise.
- **Supplements** — things you take, each with a **dosage** (amount +
  unit, e.g. 500 mg), a **quantity per serving** (e.g. 2 capsules), an
  optional **timing** (morning, pre-workout, …), and a **brand / notes**
  field. Starts with Creatine and protein powder. When you add a supplement
  to the challenge, its dose is filled in from these (e.g. "2× 500 mg").

Add, **rename** (✎), or remove entries in either tab. Tap an exercise to
**log a set** (sets × reps, with optional weight or duration); each exercise
keeps a short history of what you logged.

## Challenges

You can run **several challenges at once**. Each is a named set of items you
tick off daily, with its own streak and its own card on Home. **+ New
challenge** creates one; **Edit challenge** renames it or changes its dates.

**Start from a template** builds a whole challenge for you — its schedule, its
routines, and any exercises those routines need. See
[Challenge templates](#challenge-templates) below.

A challenge also has a **schedule**: every day (the default), **every N days**,
or **certain days of the week**. Days it isn't scheduled are **rest days** — the
card still shows, marked *Rest day* with the next due date, but nothing is owed.
Rest days don't break your streak, don't trigger the reminder, and aren't counted
in the statistics, so a Mon/Wed/Fri challenge kept perfectly reads 100%. Ticking
on a rest day is recorded but doesn't change any of the numbers.

**On a rest day the item list folds away**, down to a line like
`3 items · nothing due today` — the streak, the week dots and the next due date
stay visible. Tap that line to unfold it: a bonus session on a rest day is still
worth ticking, so the items are hidden rather than taken away, and unfolding
survives ticking one.

**Home puts the challenges that owe you something first**: due today and
unfinished, then due today and already done, then the resting ones, then any that
haven't started yet. Within each group the order you created them in is kept, so
the list doesn't rearrange itself between visits. Once you have a handful of
challenges, most days most of them are resting, and this is what stops the one
you came to tick sitting at the bottom.

A challenge has a **start date** and an optional **end date**. With an end date
it is time-boxed: the card shows *day 12 of 30*, and once the end date passes it
finishes — it drops off Home but keeps its statistics. Leave the end date empty
for an open-ended daily habit.

Archiving a challenge hides it without deleting anything: its items and every
tick you made stay in the database.

**Repeat this challenge**, on its Trends card, starts another run of the same
challenge: the same items, the same length, beginning today, with the dates
editable before it is created. The original keeps its own record.

**Edit items** also lets you **move** an item to another challenge. The days you
already ticked stay with the challenge you earned them in — a move ends the
item's membership there and starts a new one in the destination, so neither
challenge's statistics are rewritten by it.

Tapping an item you've already ticked asks before clearing it — the tick is the
day's credit, and for an exercise it also removes the workout that ticking
logged. Ticking is immediate; only undoing asks.

**Editing a workout the challenge logged.** You can change any of it, including
the exercise. Changing the **exercise** or the **date** takes that entry out of
the challenge's hands and makes it an ordinary manual workout — it has to,
because un-ticking the item deletes the challenge's entry for that day, and an
entry you deliberately changed should not disappear with it. Changing the sets,
reps or weight leaves it attached, since that is not a claim you did something
else. The confirmation message tells you which happened.

### Statistics

The **Trends** tab shows, for each challenge:

- **Completion rate** — days where every item was ticked, out of the days the
  challenge was actually due (rest days don't count against you).
- **Current and longest streak**.
- **Adherence**, one bar per day for the last 30: all done, partly done,
  missed, or a rest day. A missed day is drawn as an empty track rather than a flat bar, so
  it never looks like data that's simply absent.
- **Per item** — how often you actually do each one, which is where you find
  out that the creatine is at 95% and the squats at 40%.
- **Volume** — sessions and reps logged through the challenge, with heart rate
  where Garmin has it.
- **Weight** over the same days as the adherence bars, aligned to them, with
  the change across the period. It sits next to the adherence deliberately —
  the point is to see what the scale did while you were keeping the challenge —
  but a few weigh-ins over a few weeks show a coincidence, not a cause.

Each item in **Edit items** has an *In this challenge since* date. Days before it
don't expect that item, so adding one part-way through never makes your earlier
days look incomplete. It's normally worked out for you — shown greyed in the
field — and you only need to set it where that guess is wrong, which mostly
means items from before the app recorded when they were added. Clearing the
field goes back to the worked-out date.

Each day is scored against the items that were part of the challenge **that
day**. An item you add later counts only from the day you added it, so adding
one never makes your earlier days look incomplete; an item you remove keeps the
days it was there for. Ticking an item on an earlier day counts as saying it
applied then.

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

## Challenge templates

**Start from a template**, under the challenge list, offers ready-made
challenges. Each one lists what it contains — the routines, how long each takes,
and how long the whole thing takes a day — before you commit to it. Starting one
creates the challenge, its items, the routines behind them, and any exercises
those routines reference.

Nothing is overwritten. An exercise or routine you already have, matched by
name, is **reused as it is** — so if you have tuned a routine's steps, starting
the template again keeps your version. Everything a template creates is an
ordinary challenge afterwards: rename it, change its schedule, add or remove
items, edit the routines.

Templates are defined in the add-on rather than stored in your database, so
they don't take up space until you start one, and a corrected template arrives
with an update.

### Advanced Kegel

A 30-day daily pelvic-floor challenge, in three parts:

| Item | What it runs |
| --- | --- |
| Kegel warm-up | 10 rounds of 1s squeeze / 1s relax |
| Kegel endurance hold | 2 sets of 10 rounds: 10s hold, 10s rest |
| Kegel cool-down | 20 rounds of 1s hard squeeze / 1s relax |

About 7½ minutes a day if you do both endurance sets back to back. The player
counts one run of a routine, which is why the endurance item says **2 sets** —
press ▶, then press it again for the second set, and tick the item once both are
done.

The cool-down calls for a *harder* squeeze at the same 1-second rhythm as the
warm-up. That's a cue rather than a different movement, so both reuse the same
**Contract** and **Relax** steps instead of adding near-duplicate rows to your
library.

**Technique**, shown under the template: target the muscles you'd use to stop
the flow of urine midstream; keep breathing steady and don't flex your stomach,
thighs, or buttocks. The endurance sets can be repeated up to **6 times a day**,
spread across morning, afternoon, and evening — the challenge ticks once a day,
so treat extra rounds as a bonus rather than something to record.

## Reminders

Goal Tracker can send three notifications through a Home Assistant **notify
service** (e.g. a mobile-app notification), all configured on the add-on's
**Configuration** tab:

- **Challenge reminder** — a daily nudge at `challenge_reminder_time`. It
  only fires if you haven't already completed the whole challenge that day.
  Off by default.
- **Weigh-in reminder** — fires at `weighin_reminder_time`, either every day or
  once a week, depending on `weighin_reminder_weekday`. Skipped if you already
  logged a weight that day. Off by default.
- **Daily stoic quote** — one stoic line at `stoic_quote_time` (default
  `07:00`), **on by default**. The quotes are walked in order rather than
  picked at random, so you never get the same one two mornings running — every
  quote in the list is seen before any repeats. It's the same list the app
  shows when you finish a day's challenge.

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

Once connected, Goal Tracker syncs automatically in the background (every
`garmin_sync_interval_hours`, default 6), and the ⌚ sheet has a **Sync now**
button for an immediate pull. The home **Garmin** card shows your latest sleep,
stress and Body Battery, plus recent activities. Use **Disconnect** to remove
the stored token; already-imported data stays.

### Heart rate for your exercises

Every exercise you log gets the heart rate Garmin recorded while you were doing
it, shown next to it as `♥ 131 avg · 141 max`. You don't need to start a
workout on the watch — this reads Garmin's all-day heart rate and takes the
part that lines up with your exercise.

Exercises logged within **90 minutes** of each other count as one **session**,
and the heart rate is read over the whole session rather than per exercise —
otherwise five exercises logged after one workout would give five overlapping
windows over the same period. Every exercise in the session reports the
session's reading.

The window **ends when you log the last exercise**, so log as you finish. It
starts by running back from the first exercise by its **duration** if it has
one, otherwise by **garmin_hr_window_minutes** (default 30). Ticking a
challenge item counts as logging it, and records the moment you ticked.

The **Sessions** card on Trends lists your recent sessions with the exercises
in each, how long it ran, the reps and the heart rate.

If your watch hasn't uploaded yet there's simply no heart rate on the entry —
never a zero — and a later sync fills it in, up to 21 days back.

### When things are timestamped

Logging a weigh-in or an exercise **today** records the moment. Filing one
against an **earlier day** — or moving an existing entry to one — stores it at midday, because there's no way to know
when it happened — those rows carry `ts_exact = 0` in the database, and are
skipped when heart rate is worked out rather than being given one from the
wrong window. Rows recorded before this behaviour existed are all marked
`ts_exact = 0`: their times were placeholders too.

If you export the database for your own analysis, `ts_exact` is what tells you
whether a timestamp's time-of-day means anything. The date part is always
reliable.

Exercises, supplements and challenge items also carry `created_at` and
`updated_at`, and every version of your goal is appended to `goal_history`
(`/api/goal/history`) rather than overwritten — so a change of target is a
visible event. Both start from the version that introduced them: anything older
has empty timestamps, because the original times were never recorded.

### History, and a watch that hasn't synced

Every day pulled is kept, building up a history you can scroll back through —
the ⌚ sheet charts the **last 14 days** of sleep, stress, Body Battery or
resting heart rate.

Your watch only reaches Garmin when it syncs with the Garmin Connect phone
app, which can lag by days. Goal Tracker treats that as normal rather than as
missing data:

- Each sync refreshes the last **7 days** and then chases any **holes** as far
  back as **garmin_backfill_days** (default 60), so days that arrive late still
  land in your history. A fortnight off the charger fills in on the next sync.
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

If a metric goes quiet, press **Diagnostics** in the ⌚ sheet. It reports what
each Garmin source returns for yesterday — the shape of the response, not your
data — which is what's needed to work out why something is empty. **Copy** puts
it on the clipboard. The same thing is available at
`/api/garmin/diagnose?day=YYYY-MM-DD` if you'd rather use the URL.

Note that not every Garmin device reports a **sleep score** — some send no such
field at all. Where it isn't available it simply stays empty and the add-on
stops asking. **Resting heart rate** comes from the same data and is usually the
more useful recovery number anyway.

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
- **weighin_reminder_enabled**: turn the weigh-in reminder on/off.
- **weighin_reminder_weekday**: `daily` to be asked every day, or the name of a
  weekday for once a week (default `sunday`). Daily suits tracking a weight
  trend — more readings let the day-to-day noise average out.
- **weighin_reminder_time**: 24-hour `HH:MM` time for the weigh-in reminder
  (default `08:00`).
- **stoic_quote_enabled**: turn the daily stoic quote on/off (default `true`).
- **stoic_quote_time**: 24-hour `HH:MM` time for the daily quote (default
  `07:00`).
- **restrict_to_user_ids**: comma-separated Home Assistant user IDs allowed
  to open the add-on. Empty (default) means any user who can see the
  sidebar entry may use it. Find your own user ID in the ⚙️ settings sheet.
- **garmin_auto_sync**: sync Garmin data automatically in the background
  (default `true`). Turn off to only sync when you press **Sync now**.
- **garmin_sync_interval_hours**: how often the background sync runs, in
  hours, 1–168 (default `6`). Only applies when `garmin_auto_sync` is on.
- **garmin_hr_window_minutes**: how far back from an exercise's log time to
  read heart rate when the entry has no duration of its own, 5–180 (default
  `30`).
- **garmin_backfill_days**: how far back to chase missing Garmin days, 7–730
  (default `60`). Garmin keeps your history indefinitely, so raising this pulls
  in days from before you installed the add-on; they fill in over the next few
  syncs rather than all at once.

## Reading the data elsewhere

If you want this data in a warehouse or notebook, the add-on can tell you what
changed rather than making you reload everything.

Set **api_token** on the Configuration tab and send it as
`Authorization: Bearer <token>` — a pipeline has no Home Assistant session, so
this is how it authenticates.

To reach the add-on from outside Home Assistant, publish its port in the add-on's
Network section. **That port requires the token.** Requests arriving on it
without a valid bearer token are refused with `401`, including when no
`api_token` is configured at all — there is nothing else on that port that could
identify a caller, so "no token set" cannot mean "no check needed". Requests
through Home Assistant's ingress are unaffected: the Supervisor has already
authenticated the user, and `restrict_to_user_ids` narrows that further if you
set it.

So the token really is the only thing standing between the network and
`/api/export` and `/api/backup`, and nothing rate-limits guesses. There's no
required length or format — any text works and surrounding spaces are ignored —
but make it long and random rather than memorable. For example:

```
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

The add-on logs which state it is in at startup, so a caller getting `401` can be
told apart from a caller sending the wrong token without guessing.

- **`GET /api/export`** — every tracked table, plus the `max_seq` the snapshot
  corresponds to. Start here.
- **`GET /api/stats`** — row counts for **every** table, the database size and
  the same `max_seq`, without serialising a single row. `counts` holds the
  tracked tables and `other_counts` the rest (`change_log`, and anything the
  feed does not carry); `total` is the tracked subset and `total_all` the whole
  file, so a count and a size on the same line describe the same scope. For
  anything polling on a timer (the Add-on Watchdog does), this is the one to
  call: `/api/export` answers the same question in megabytes.
- **`GET /api/changes?since=<seq>&limit=<n>`** — everything after `seq`, oldest
  first. Each entry names its **actor** — `user`, `automation` or `migration` —
  so you can tell a value you entered from one the add-on wrote for you. Each
  entry has the row's current state for an insert or update, and
  `null` for a delete. Apply them in order.

`routine_steps` is in the feed too, which is what makes a routine's workout
readable downstream: without the steps, a 240-second row is a duration with
nothing behind it. Note that an abandoned routine is logged as a **manual**
workout with a note rather than a challenge one, so it survives the item later
being ticked and un-ticked.

Deletes are in the feed, which matters here: un-ticking a challenge item
removes both the tick and the workout it created. Watch `full_reload_required`
— entries are pruned after 90 days, so a pipeline that has been away longer
should call `/api/export` again. Picture bytes aren't in the feed; fetch those
from the image endpoint.

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


### When the trend is not established

If your weight is moving less than the readings scatter — a slope smaller than
its own standard error — the badge reads **Too early** and the projection is
drawn differently: a faint, finely dotted line labelled **If this holds**,
inside a wide shaded band.

**The band is the answer, not the line.** When a trend is unestablished the 95%
band is enormous, and its size is the honest statement of how little the data
settles. A line inside a band half the panel high says "we do not know" far
better than an empty panel does — an empty panel says nothing at all.

This is a reversal. The line used to be hidden entirely, on the reasoning that a
confident line would contradict a card saying "no trend to project yet". That
was over-cautious: the chart went silent about the one thing you open it to see,
and read as broken rather than as careful.

The figure narrows as you log more. If the band stays wide, the readings
themselves are the problem — weighing at the same time under the same
conditions is what tightens it.
