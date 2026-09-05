# Changelog

## 1.57.0

- **A tonic row folds once it is done.** Due, and it is open with its dose and
  its caution showing — which is exactly when you read them, since that is when
  you are about to give it. Given, and it collapses to its name and when it is
  next due. Four routines all up to date was a card full of instructions for
  things nobody had to act on.
- Press **Given** and the row folds itself away, which is the feedback as much
  as the tidying.
- Nothing is hidden for good — tap a folded row and it opens. **Given** and
  **✕** stay on the row folded or not, so giving one early never needs it
  opened first, and pressing either one no longer counts as asking to fold it.
- The rows are `<details>` rather than a class and a click handler: the
  keyboard and screen-reader behaviour comes free, and "open" is a state the
  browser already knows how to keep.

## 1.56.0

- **A history of stirs.** Each batch row gains a small count button; tap it for
  that tub's stirs, newest first, each with the gap before it.
- **The gaps, not just the times.** A column of timestamps says you stirred it;
  the interval between them says whether the rhythm held, and a long gap is
  exactly where a batch came closest to the compost. Anything past
  `ferment_stir_hours` is the only thing coloured.
- The first entry of a batch reads **mixed** rather than showing a gap — that
  stir is the moment you made it up, and there is nothing earlier for it to be
  late after.
- A summary line answers "am I keeping up" as a proportion: how many stirs, how
  many late, the longest gap and the typical one. The typical is a median, so
  one forgotten weekend does not make a well-kept batch look erratic.
- Gaps are measured within a batch, never across two. Consecutive stirs in
  different tubs are unrelated and the interval between them would describe
  nothing.
- Stirs outlive their batch, so a tub that was fed or binned keeps its record —
  which is most of why it is worth having.
- Loaded when you ask rather than with the card: a week of twice-daily stirs is
  fourteen lines nobody is reading yet.

## 1.55.0

- **Scan a receipt when logging an expense.** Photograph the till receipt and
  the amount, date and shop name go into the form. Tesseract runs inside the
  add-on, so nothing leaves the machine.
- **It fills the form in and never saves.** OCR on a creased thermal receipt is
  wrong often enough that logging its guess unattended would put bad numbers in
  the books faster than typing them by hand would.
- Reads what a Danish till actually prints: `1.234,56` as twelve hundred and
  thirty-four kroner, **I ALT** and **At betale** as the total — and **Moms**,
  **Kontant**, **Byttepenge**, **Subtotal** and **Rabat** as never the total.
  That last group is why a naive reading fails: VAT sits near the bottom at a
  plausible fraction of the real figure, and the cash tendered is larger than
  what was paid.
- Dates and times are stripped before the scan rather than filtered after, or
  the year reads as a four-figure sum and the timestamp as two more.
- Other prices found are offered as chips beside the amount, because the first
  guess is wrong often enough to want the runner-up one tap away.
- The button appears only where the engine is installed (amd64 and arm64). On
  armv7 there is no OpenCV either, and a button that could only ever apologise
  is worse than none.
- New `receipts.py`: text in, candidates out, so the hard part — which of the
  eleven numbers on a receipt you actually paid — is tested against real
  receipt shapes without a photograph or a Tesseract install.

## 1.54.0

- **Click a point on the eggs-per-day chart to see what it rests on.** The
  figure there is an attributed rate rather than a count, so the answer usually
  names a different day from the one you clicked: the eggs credited to Monday
  often arrive in a basket found on Wednesday and spread back.
- That is also what makes a rate above the flock size possible, so the sheet
  spells it out — the count is right, the day it lands on is not — instead of
  leaving you with a red ring and no explanation.
- Where the eggs came from a later collection, that collection and its date are
  shown alongside whatever was logged on the day itself. They are different
  things and a day can have both.
- A day nothing covers says which kind of gap it is: before the first log, or
  after the most recent one with the eggs still in the nest. Better than an
  empty sheet, and it is the distinction between "the hens stopped" and
  "nobody has been out yet".
- Only the day chart drills down. A monthly point averages thirty collections,
  so there is nothing single to show, and those points are not clickable.
- The rate and the explanation of the rate come from one implementation, with a
  test walking every day of a range to prove the two cannot disagree.

## 1.53.0

- **Proper hover tooltips on the charts.** 1.52.0 leaned on the browser's own
  `<title>` tooltip, which waits about a second, is styled by the operating
  system and does nothing at all on a touchscreen. The charts now carry their
  own: instant, themed, positioned above the point, and shown on a tap as well
  as a hover.
- The point being read is ringed while you hover it, so on a dense chart you
  can see which day the figure belongs to.
- Nearest by horizontal distance rather than whatever is directly under the
  cursor — on thirty points a few pixels apart, requiring a direct hit made it
  flicker. A point more than about 40px away shows nothing, so an empty stretch
  of chart stays empty.
- The tooltip text moved from `<title>` to `data-tip`. Keeping both meant two
  tooltips, one of them a second late; `aria-label` keeps the value available
  to a screen reader.
- The e2e suite gained the `page_errors` fixture the newer suites have, and a
  test that drives a real mouse and asserts a tooltip appears — "the markup
  contains a tooltip div" is exactly the kind of assertion that passes while
  nothing shows on screen.

## 1.52.0

- **Hover any chart to read the exact figure.** None of the four had a way to
  do this — the line showed a shape and you could see a spike on roughly the
  22nd with no way to ask what it was. Same mechanism Electricity Tracker uses,
  so the two behave alike.
- **The eggs-per-day charts draw the flock ceiling**, a dashed line at one egg
  per hen per day labelled `5 hens`. It is a hard physical bound, and it turns
  a bare number into a proportion.
- **A day above that ceiling is ringed in red.** Six eggs from five hens is not
  a record harvest: it means the spreading rule's assumption broke, since each
  collection is credited to the days since the previous one and that assumes
  every visit empties the nest. Eggs missed on Monday and found on Tuesday all
  land on Tuesday.
- The figure is left alone rather than capped. The number is what the
  collections say; what is wrong is a fact about the collecting, so it is the
  keeper who is told rather than the number that is changed.
- **Fixed: the forecast divider was a gutter-width off.** Adding the y axis in
  1.49.0 offset every x except that one, so the dashed line marking where
  history ends pointed at the wrong month on both monthly charts.

## 1.51.0

- **Every chart on Trends expands**, not just the first. Eggs per day, eggs per
  day by month and the advanced forecast all had the frame for it and no
  button. The daily one packs ninety points into a few hundred pixels, so it
  is the one that most wanted making bigger.
- One delegated handler over the button class rather than a listener per id, so
  a fifth chart needs a button and no JavaScript. Escape collapses whichever is
  open, and leaving the tab collapses all of them — an expanded chart is
  `position: fixed` and would otherwise float over the Home tab with its close
  button out of reach.
- A test asserts that every `.trends-chart-wrap` contains an expand button, so
  a chart added later cannot quietly miss one.

## 1.50.0

- **Flock tonics.** Garlic in the water, oregano in the feed, cider vinegar,
  fresh greens — a schedule for the homemade things that support a flock's
  condition, with a reminder when each is due. Unlike a ferment nothing goes
  mouldy when one is missed, which is exactly why it needs reminding about: it
  does not fail, it quietly stops happening.
- **Four routines ship filled in**, with the amounts keepers actually use and
  the cautions worth having. Cider vinegar must never go in a galvanised
  drinker — the acid leaches zinc and that poisons birds. Garlic is an allium
  and more is not better. Both are on the card, not buried in the docs.
- **The card says these are supplements, not medicine**, and that a sick bird
  needs a vet. A tidy schedule of ticked-off tonics quietly implies the flock's
  health is handled, and that implication is what needed answering.
- Seeded on first opening the card with the feature on, never at startup, and
  only when the table is empty — so ones you delete stay deleted.
- Only a routine more than three days late is coloured. "Due" on a weekly
  rhythm is not an emergency, and colouring it would bury the ferment row that
  is.
- Pausing keeps a routine's history; deleting takes it. A routine paused over
  winter comes back in spring with its record.
- Reminder once a day at `tonic_times` (default `09:00`), to
  `tonic_notify_service` or `notify_service`. Once, not twice like the stir
  reminder: telling somebody twice a day about a Sunday job is how a reminder
  becomes noise.
- **The Ferment tab is now the Feed tab**, holding both cards. Each hides on
  its own option and the tab appears if either is on.
- New `tonics.py` rather than more of `app.py`. `tonic_routines` and
  `tonic_doses` join the change feed.

## 1.49.0

- **Every chart on Trends now has a labelled y axis.** Monthly egg totals, eggs
  per day by month, eggs per day by day, and the advanced forecast all plotted
  unlabelled numbers: a line that rises is not information until you know
  whether it rose by two eggs or two hundred.
- Gridlines at readable intervals — 1, 2 or 5 times a power of ten — with the
  unit on the top tick (`4 eggs/day`, `600 eggs`) rather than a rotated axis
  title, which would cost more width than the labels it explains.
- The tick algorithm is ported from Electricity Tracker rather than invented
  again, so the two add-ons pick gridlines the same way and moving between them
  does not mean learning a second convention. Never finer than 0.1: eggs are
  counted, and a 0.05 step would label gridlines with numbers they do not sit
  on.
- The gutter is added beside the plot, never taken out of it, so adding the
  axis does not squeeze the data or reflow it when a label gains a digit.

## 1.48.0

- **Fermented feed has its own tab.** 🪣 Ferment, between Home and Trends,
  instead of a card wedged between the Log buttons and Recent activity.
- The tab *button* is what hides when `ferment_enabled` is off, not the page. A
  tab you can reach that turns out empty reads as something broken; a tab that
  is not there reads as a feature you have not turned on. Turning the option
  off while you are standing on the page moves you back to Home.
- Opening the tab refetches. Batches age by the clock alone, so a tab opened an
  hour after the page loaded is stale without anything having happened.
- **The tab bar moved to the top**, under the title, and sticks there as you
  scroll. It is `position: sticky` in the flow rather than fixed, so nothing
  has to reserve a gap the exact height of the bar — a number that goes stale
  the first time a label wraps.

## 1.47.2

- **The fermented feed card was unreadable in dark mode.** It painted itself
  with a `--card` token this stylesheet has never defined, so it fell back to
  white and drew the dark theme's near-white text on it — 1.18:1 contrast,
  effectively invisible. It now takes `--surface` and `--text` like every other
  card.
- `.training-photo` had the same bug with a `--surface-2` that does not exist,
  silently rendering a 4%-white fallback instead of a recessed tile.
- **`--danger`, `--positive` and `--negative` had no dark value**, so a figure
  in the red kept its light-theme ink on a near-black card. All three now have
  one, and the amber and red used by the stir and bin states are tokens rather
  than literals so both themes can answer them.
- `--positive` was 3.93:1 on white, under the 4.5:1 body-text line. Darkened 3%
  at the same hue and saturation.
- New stylesheet tests: every `var()` resolves to a token that exists (a
  fallback makes a typo look like a working default, which is how this reached
  a user), every colour token has a dark value, and every ink clears 4.5:1 on
  the surface it sits on.

## 1.47.1

- **The day counter read a day ahead for the last hour of every day.** A batch
  three days and twenty-three hours old showed **day 4 of 11**: its age was
  rounded to the nearest tenth before the card floored it, and 3.958 rounds up.
  At the eleven-day line the row said **day 11 of 11** while the batch was
  still ready and no bin warning had gone out. Age now truncates — a batch is
  not four days old until it is.

## 1.47.0

- **A feeding window, and an end to it.** A batch now has three lives rather
  than two: fermenting, ready to feed from, and spent. Past
  `ferment_max_age_days` (default 11) the row turns red, says **Past it — day
  12 of 11**, loses its **Stirred** button and the reminder says to bin it.
- This is the state people miss. A tub that has been ready for a week looks
  exactly like one that was ready this morning — nothing about it announces
  that it has gone over, only the clock knows. Past the window the culture has
  run out of sugar and stopped holding the spoilage organisms back.
- While it is in the window the row counts the days — **Ready · day 5 of 11** —
  and with several tubs ready the reminder says which to use up first. Once
  three are ready, "ready" is not the useful fact.
- **`ferment_notify_service`.** Ferment reminders can go somewhere other than
  the egg reminder. Blank falls back to `notify_service`, so the common case
  still needs one setting; set only the ferment one and you get stir alerts
  without turning on the egg reminder at all.
- **Still one notification.** When there is more than one thing to say the
  lines are combined, worst-to-get-wrong first: stirring stops mould, binning
  stops somebody feeding spoiled grain, and feeding will still be true in an
  hour. Three pushes at 08:00 is how you teach somebody to swipe the whole lot
  away, starting with the one that cannot afford to be ignored.
- A spent tub is now reported even when everything has just been stirred. The
  stir clock used to decide on its own whether anything got said at all.
- A closed batch never goes spent. Nagging somebody to bin a tub they emptied
  last week is how a reminder loses its authority.
- `ferment_max_age_days` is held to at least `ferment_days + 1` — a batch that
  went off before it was ever ready is a state the settings can express and a
  tub cannot be in.

## 1.46.0

- **Carry the culture forward.** When you press **Fed**, the add-on offers to
  keep the liquid; the next batch can then be seeded from it and is ready in
  **two days instead of three** — the new grain starts with a working culture
  rather than waiting for wild lactobacillus to find it.
- **The liquid, not the grain.** That distinction is the whole safety argument.
  Three-day-old wet grain is the substrate spoilage organisms have had three
  days to establish on; the drained brine is the culture without it. Keeping
  the grain back is never offered.
- **Never from a binned batch.** A batch thrown out for mould is exactly the
  culture that must not reach the next tub — and that is the moment somebody is
  most tempted to save it, with three days of waiting otherwise wasted. Not
  offered in the UI, and rejected by the API.
- **One jar, and it says its age.** Saving again replaces what was there rather
  than stacking, because there is one jar in the fridge. Past 7 days the card
  says the culture may have gone quiet; past about 8 generations it suggests a
  clean batch, since whatever is most vigorous gradually takes over. Neither
  refuses anything — **Discard jar** is the way back to a fresh start.
- **A cold room still wins.** Seeding shortens the wait relative to your
  `ferment_days`; it does not override a keeper who set 4 because the utility
  room is 12°C in February.
- Batches record which generation they are, and the row says `seeded (gen 2)`.
- Upgrading from 1.45.0 adds the column in place; existing batches read as
  unseeded, which they were. `ferment_starter` joins the change feed.

## 1.45.0

- **Fermented feed.** Track tubs of soaking grain: start a batch, log the stirs,
  and mark it fed — or binned, which is recorded separately because a batch lost
  to mould is a different event from one the birds ate.
- **Push notifications to stir.** This is the point of the feature. An unstirred
  batch grows mould on top and has to be thrown away, so a reminder is not a
  nicety here — it is what stops three days of waiting going in the compost.
- The stir reminder is **its own tick, not part of the daily egg one**. Stirring
  is a twice-a-day job and a reminder that can only arrive at 18:00 is no use
  for the morning one.
- It fires at **times of day** rather than on an interval. "Every 12 hours"
  lands at 3am half the time, and a notification nobody can act on is one people
  learn to swipe away — which costs you the reminders that mattered. Default
  08:00 and 20:00, configurable.
- One notification per window, recovered from the database so a restart an hour
  later does not resend it. A window that had nothing due does **not** count as
  used, so a batch falling due at 20:30 is still reminded about at 20:30.
- **Batch size is suggested from your flock.** Five hens over a three-day
  ferment is about 675 g of dry feed; the number is offered rather than filled
  in, because you know your birds and the add-on does not.
- A batch counts as stirred the moment it is started — you have just mixed it —
  so the first reminder comes an interval after you last touched it rather than
  an interval after you started.
- Off by default. `ferment_enabled`, plus `ferment_days`, `ferment_stir_hours`
  and `ferment_stir_times`.
- New `ferment.py` rather than more of `app.py`, which is already 3,600 lines.
  `ferment_batches` and `ferment_stirs` join the change feed.

## 1.44.2

- Dropped `build.yaml` and named the base image directly in the `Dockerfile`.
  Supervisor 2026.04.0 stopped passing `BUILD_FROM` and now warns the file is
  deprecated — and an ARG that never arrives makes `FROM $BUILD_FROM` an *empty*
  base, so the next rebuild of this add-on would have failed. The file named the
  same `python:3.12-slim-bookworm` for both architectures, which one multi-arch
  tag already does.

## 1.44.1

- ARCHITECTURE.md brought back in line with the code. It had drifted far enough
  to mislead: "SQLite, one table" against ten, a five-architecture build against
  two, an Alpine base that became Debian in 1.29.0, "never accepts connections
  except through ingress" against a documented published port, a deferred
  watershed fix that shipped in 1.32.2, and UI line counts off by 5x.
- §20 described the coin-calibrated egg analysis in the present tense. That
  version never shipped as described — the coin was replaced in 1.31.0 — and the
  section now says so at the top rather than 80 lines in. It also claimed photos
  are never persisted, which 1.31.0 reversed.
- Two things the document had never covered at all, though the pipeline depends
  on both: the change feed (§22 — why triggers rather than write paths, why seq
  rather than a timestamp, what is deliberately excluded and why) and the data
  endpoints (§22a). §21a records the published-port hole closed in 1.44.0,
  including the two-line reason it existed.

## 1.44.0

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

## 1.43.1

- Documentation fixes. A code fence closed on the same line as the sentence
  that followed it, so that sentence rendered inside the code block.
- The egg/not-egg model was described as needing "~25 corrections". It needs 15
  confirmed eggs *and* 15 rejected shapes — corrections in both directions count,
  and neither alone is enough.

## 1.43.0

- `/api/stats` now counts **every** table, not only the tracked ones, split into
  `counts` and `other_counts` with `total_all` alongside. Here that matters:
  `egg_vision_samples` and `egg_vision_models` hold images and dominate the
  database while contributing nothing to the tracked count — 75 records in
  10.5 MB was the pairing that prompted this.
- `total` keeps its original meaning (tracked tables only), so a consumer
  written against 1.41.0 keeps its number.

## 1.42.0

- Dropped `armhf`, `armv7` and `i386` from `arch`. Supervisor now reports all
  three as deprecated; `aarch64` and `amd64` remain. If you run this on a
  32-bit ARM or x86 box, stay on 1.41.0.

## 1.41.0

- New `/api/stats`: row counts per tracked table, the database size and the
  current `max_seq`. `/api/export` already answered this, but only by
  serialising every row — megabytes to learn half a dozen integers, which is no
  way to poll on a timer. The Add-on Watchdog reads it every scan.
- Counts cover the same tables as the change feed, so a number here and a
  number in the lakehouse are counting the same thing.

## 1.40.0

- **The add-on log now says whether token auth is usable.** A data pipeline that
  presents a bearer token gets the same 403 whether the token is wrong or no
  `api_token` is configured here at all — the two are indistinguishable from the
  caller's side. Startup now states which it is, with the token's length so it
  can be compared against the caller's copy. The value itself is never logged.
- `/api/debug` reports the same as `api_token_set`, `api_token_length` and
  `restrict_to_user_ids_set`.

## 1.39.0

- **Every recorded change now says who made it**: `user` for something done in
  the app, `automation` for the add-on's own background work, `migration` for an
  upgrade or one-off fix. It comes through on the change feed, so a pipeline can
  tell a figure you entered from one the software wrote.
- Changes recorded before this leave it empty rather than being guessed at.

## 1.38.2

- **Fix a token with non-ASCII characters breaking authentication.** A
  passphrase with an accent in it made every authenticated request fail with a
  server error rather than being accepted, because of how the comparison was
  done. Any characters work now.
- Documented that the token has no required length or format, with a note on
  generating a strong one — it's the only thing protecting the API if you
  publish the port.

## 1.38.1

- **The change feed's snapshot now names the column that identifies each row.**
  Without it a consumer had to guess, and guessing "the first column" is wrong:
  the JSON has its keys sorted alphabetically, so the id is rarely first. Rows
  would have been merged together on the wrong column.

## 1.38.0

- **A change feed, for loading this data somewhere else.** Every insert,
  update and delete on the tables worth analysing — collections, chickens,
  breeds, food types, health events, nesting boxes — is recorded with a
  sequence number, so an external pipeline can ask "what changed since X?"
  instead of reloading everything. `/api/export` gives a full snapshot to start
  from, `/api/changes` the deltas after it.
- Deletes are included, which a "last modified" column can't do.
- Reading it needs an **api_token** (Configuration tab) sent as
  `Authorization: Bearer …`, since a pipeline has no Home Assistant session.
  Optionally publish the port to reach it from outside Home Assistant.
- **Backups are now taken through SQLite's own snapshot mechanism** rather than
  streaming the file off disk, which could catch the background loop mid-write.
- Egg-vision training samples and models stay out of the feed, as does internal
  bookkeeping — they're the app's machinery, not data to analyse.

## 1.37.0

- New **Eggs per day, recently** chart at the top of the Trends tab: a
  day-by-day view of the last 14, 30 or 90 days, for how your flock is
  laying *right now*. The monthly chart can't tell you that — its
  current-month figure averages everything since the 1st.
- The line normally **stops a day or two short of today**, at your last
  collection. Anything laid since is still in the nest as far as the app
  knows, so it's left off rather than drawn as zero, which would make the
  chart look like it fell off a cliff every time you hadn't been out yet.
  The caption says how far short it stops.
- The monthly chart and table now show **how many days each rate was
  averaged over**, and months resting on fewer than 10 days get a hollow
  point. That's usually the month you started logging — it only covers
  from your first collection onwards, so it reads high next to a full
  month instead of being comparable to one.

## 1.36.0

- New **Eggs per day** chart on the Trends tab, with a matching **Per
  day** column in the table: how many eggs a day your flock actually laid
  each month, rather than how many you happened to carry in.
- **You don't have to collect every day.** Each collection counts across
  every day since the one before it — 12 eggs found after four days away
  is 3 a day for those four days — so collecting daily and collecting
  twice a week give the same line, and months are comparable regardless.
  Eggs still sitting in the nest since your last collection aren't
  counted yet, so the current month doesn't sag just because you haven't
  been out today, and a month with no collection to go on is left blank
  instead of drawn as zero.
- The forecast now continues onto that chart too, as eggs per day.

(This release also corrects the in-app version number, which still read
1.34.0 in 1.35.0's debug panel.)

## 1.35.0

- Training-photo gallery: **Remove** is now a reversible **Exclude**. An
  excluded photo stays stored (greyed out, marked *Excluded*) and simply
  drops out of training — tap **Include** to put it back, or **Delete** to
  remove it for good. Lets you try pulling a suspect photo, retrain, and
  restore it if that didn't help.
- The gallery now shows a **Retrain now** banner as soon as you edit,
  exclude, include or delete a photo — those changes only take effect when
  you retrain, and the banner makes that one tap away instead of a silent
  pending change.
- Tap a chicken's photo in the flock list to see it **full-size**.

## 1.34.0

- New **access control**: you can now limit the add-on to specific Home
  Assistant users. Add their user IDs to the **restrict_to_user_ids**
  option (comma-separated) and everyone else gets an "access restricted"
  page — a hard block, on top of Home Assistant only showing the add-on
  in admin users' sidebars. Empty by default (no change for existing
  installs). Find your own user ID in the ⚙️ settings sheet under
  **Access control**, and add it before restricting so you don't lock
  yourself out (recoverable by clearing the option on the Configuration
  tab if you do).

## 1.33.0

- New **View training photos** button in the egg-vision training settings.
  Opens a gallery of every photo the model has learned from, each showing
  its egg count and sizes. **Remove** any photo to exclude it from future
  training (a blurry shot, or one that was corrected wrongly), or **Edit**
  it to reopen the review screen, re-correct the eggs/sizes/box edges, and
  save the fix back onto that photo. Changes take effect next time you
  train.

## 1.32.2

- **Touching eggs are now counted separately.** Two eggs resting against
  each other used to be detected as a single oversized egg (the "XL" you
  may have seen); they're now automatically split apart and counted and
  sized individually. Works for eggs side by side, stacked, or at an
  angle. Two eggs overlapping so heavily that one is mostly hidden can
  still read as one — correct those by hand with **+ Add egg** as before.
  If you have training on, these corrections also improve detection over
  time.

## 1.32.1

- No user-facing behavior changes, but two real bugs fixed: several
  startup log lines had no `flush=True`, so under Supervisor/Docker's
  buffered stdout they could be lost entirely if the add-on were ever
  SIGKILLed before flushing on its own; and the "SUPERVISOR_TOKEN not
  set" background-loop line was silently dropped by Flask's default
  logger threshold and never appeared in the log at all. All add-on log
  output is now timestamped and immediately flushed. Also fixed a Flask
  deprecation warning (`flask.__version__`, removed in Flask 3.2) in the
  debug endpoint's reported Flask version.

## 1.32.0

- **Eggs are now found by color, not brightness** — the flagship
  real-world case (a brown egg on pale straw bedding) went completely
  undetected before, because detection compared brightness only and a
  brown egg is barely darker than straw. Detection now looks for
  regions whose *color* differs from the bedding, which also works for
  white eggs and any future egg color (green, blue) as long as it
  contrasts with the bedding — an egg colored almost exactly like its
  bedding remains the one hard case.
- **Angled photos now size eggs correctly.** The two wall lines on the
  review screen can be tilted (drag either end) to follow the box's
  walls as they converge in a photo taken into a deep box; each egg is
  measured against the local wall-to-wall distance at its own position,
  so eggs near the back no longer read smaller than they are.
- **Much stronger automatic box recognition.** Photos are matched using
  a small bundled image network (SqueezeNet, ~5MB, runs entirely
  on-device) instead of a coarse color signature that couldn't tell two
  wooden boxes apart. Also fixed box recognition never training at all
  until 25 total photos were stored — two boxes with a few setup photos
  each now train immediately. Existing training photos are reused: tap
  **Train now** once after upgrading (or just take the next wizard
  photo) and recognition retrains automatically.

## 1.31.1

- Fixed the nesting-box setup wizard letting **Finish** be tapped after
  just one photo — with multiple boxes registered, each needs at least a
  few samples before the app can learn to recognize it automatically,
  otherwise every photo falls back to asking "which box is this?" with
  no auto-detection ever kicking in. Finish now stays disabled until
  enough photos have been taken.
- Added a **+ Train more** button next to each nesting box in the ⚙️
  settings sheet, showing how many auto-identification samples it has
  so far — lets you top up an already-registered box's training photos
  without creating a duplicate box.

## 1.31.0

- **Egg photo counting & sizing now measures against a nesting box
  instead of a coin.** Set up a nesting box (name + inside width) from
  the ⚙️ settings sheet or straight from the Log Eggs photo button, and
  the add-on measures eggs against the box's own side walls — no more
  placing a coin in every shot. Register more than one box and the app
  tries to recognize which one is in each photo automatically, asking
  you to confirm (or add a new box) only when it isn't confident.
  Setting up a box walks you through a short guided round of photos so
  it can learn to spot that box's edges reliably. As before, width-based
  sizing is an approximation of real weight-based grading, and — new in
  this release — measurement doesn't correct for a tilted/angled photo,
  so aim for roughly square-on shots. `egg_vision_coin_diameter_mm` is
  removed; existing coin-calibrated installs will need to set up a box
  the next time they use this feature.
- New **optional trainable model** for egg counting & sizing (off by
  default — enable with **egg_vision_training_enabled**): when on, each
  reviewed photo and your corrections are stored on-device, and a
  **Train now** button (in the ⚙️ settings sheet, once ~25 corrections
  are collected) fits small models — replacing the fixed detection
  cutoff and size-bucket formula with ones learned from your own flock,
  camera, and lighting. Nothing changes until you opt in and train;
  storage is capped (**egg_vision_training_retention_count**, default
  200) and clearable at any time. Enabling this increases the size of
  Backup & Restore's `.db` file, since stored photos travel with it.

## 1.30.1

- No user-facing changes. Added diagnostic logging around Supervisor
  restarts (SIGTERM receipt, live thread state, and shutdown timing) to
  investigate add-ons occasionally being killed (exit 137) instead of
  exiting cleanly on restart.

## 1.30.0

- New **experimental egg photo counting & sizing** on the Log Eggs sheet
  (off by default — enable with **egg_vision_enabled**): photograph your
  eggs alongside a coin, and the add-on counts them and estimates each
  one's size (S/M/L/XL) calibrated against the coin's real-world diameter
  (**egg_vision_coin_diameter_mm** — set it to your coin's actual size).
  The result is always a reviewable suggestion — drag the coin into
  place, correct any egg's size, add a missed egg, or remove a wrong one
  — before it fills in the usual count and you hit Save. Only available
  on **amd64**/**aarch64** installs; no further base-image change was
  needed beyond the Debian switch that shipped in 1.29.0. See the app's
  documentation for photographing tips and this feature's honest limits
  (width-based sizing approximates real weight-based grading; touching
  eggs may need manual correction).

## 1.29.0

- New **experimental Advanced forecast** on the Trends tab (off by
  default — enable with **advanced_forecast_enabled**): a real
  statistical model (Holt-Winters) fitted directly on your logged
  history, shown as an independent second opinion alongside the existing
  forecast, with its own confidence range. Needs at least 6 months of
  history for a basic fit, 24 months for a seasonal one. Only available
  on **amd64**/**aarch64** installs — the add-on's base image switched
  from Alpine to Debian (`python:3.12-slim-bookworm`) on every
  architecture to make this work on 64-bit Raspberry Pi installs too, not
  just x86; other architectures are unaffected and simply don't get this
  one optional feature.

## 1.28.0

- The Trends chart now shades an **uncertainty range** around the
  forecast line, based on how far off the backtest has historically been
  (mean absolute error over completed months). The range is flat across
  all forecasted months rather than widening further out — the backtest
  only ever tests a 1-month-ahead prediction, so there's no data to
  support claiming later months are less certain than the first.

## 1.27.1

- Fixed sheets (My Flock in particular) overflowing the screen with no
  way to scroll when their content is taller than the window — sheets now
  cap at 90% of the screen height and scroll internally.

## 1.27.0

- New per-chicken **Health history** in 🐔 My Flock: open a chicken and
  log vet visits, vaccinations, molt start/end, weight checks (grams),
  or general observations, each with a date and optional notes. Events
  list newest-first with one-tap delete; removing a chicken removes its
  history too. Included in backups automatically.

## 1.26.0

- The egg collection forecast now models **seasonality**: longer days
  boost laying in summer, shorter days lower it in winter (a ±25% curve
  peaking at the June solstice). Projections across a season boundary —
  e.g. made in autumn, looking into winter — now show the dip and the
  spring recovery instead of running the current rate flat. Your current
  observed rate is unchanged; only how it's projected forward differs.
  The forecast backtest applies the same curve retroactively, so it stays
  a fair measure of accuracy. This closes a previously documented known
  limitation.

## 1.25.0

- The app is now served by a production WSGI server (waitress) instead of
  Flask's development server — the "development server" warning disappears
  from the add-on log, and requests are handled concurrently. No
  configuration changes needed.

## 1.24.0

- New **Export entries as CSV** button in the Backup & Restore sheet:
  downloads every logged entry as a spreadsheet-friendly CSV file. The
  export is one-way (for analysis only) — restoring still uses the `.db`
  backup file.

## 1.23.0

- The overdue-eggs reminder's "already notified today" guard is now stored
  in the database instead of only in memory, so restarting the add-on
  shortly after a reminder went out no longer sends a duplicate that day.
  This closes a previously documented known limitation.

## 1.22.1

- Fixed chicken photos not updating after a re-upload: the photo URL
  doesn't change when you replace a chicken's picture, so the browser
  could keep serving the previously cached image instead of the new one
  until a hard refresh. The photo endpoint now tells the browser not to
  cache it.

## 1.22.0

- Added a **Given away** checkbox to the Log Used sheet, for eggs you hand
  off rather than eat yourself. Given-away eggs still count against "eggs
  on hand" like any other used egg, but are excluded from the Finances
  section's "Est. savings" figures, since giving eggs away doesn't reduce
  your own grocery spending.

## 1.21.1

- The "Est. savings" price option is now **supermarket_egg_price** — a
  price per single egg (default `2.5`) instead of per dozen. If you'd
  already set **supermarket_egg_price_per_dozen** in 1.21.0, that option
  is no longer read; set the new one to what a single egg costs you
  instead (e.g. a dozen at 30 becomes `2.5`).

## 1.21.0

- Added **Est. savings** to the Finances section: what your used eggs
  would have cost at supermarket prices, for the current month and
  all-time — new **supermarket_egg_price_per_dozen** option (default
  `30`) to match your local price. Only counts eggs logged as used, not
  sold, so it doesn't double up with the revenue you already track.

## 1.20.0

- Chicken records in **My Flock** can now have a photo — pick one from
  your phone in the chicken form (auto-resized before saving, so it
  won't bloat the database), shown as a thumbnail in the list. Removing
  a chicken's photo is one tap away too.

## 1.19.0

- Added a small red/green connection status dot next to the top bar's
  icons — green when Home Assistant is reachable, red when it isn't. Tap
  it to jump straight to the full Debug info detail (already in the 🔔
  Notifications panel) instead of having to dig for it.

## 1.18.0

- Added **My Flock** (🐔 icon): track individual chickens — name, breed,
  hatch date, active/lost status — instead of just a flat count per
  breed. Breeds (Isabrown/Sussex by default, each with a published
  eggs/year estimate) are also editable, so you can add any breed you
  keep.
- The egg collection forecast now uses each active chicken's actual age
  once you've added at least one: no eggs before ~20 weeks old, full rate
  through ~18 months, a reduced rate after — instead of a flat per-breed
  count. Falls back to the previous flat-count method
  (`flock_isabrown_count`/`flock_sussex_count`) if no chickens are added.
  The forecast backtest (what it would have predicted for past months)
  now also uses each bird's age as of that past month.

## 1.17.0

- Added a **Feed refill cadence** table to the Trends tab: every food
  type you've logged, with its all-time average days between refills,
  days since last emptied, and times fed — a one-screen comparison across
  all your feeds, instead of checking them one at a time in the Log
  Feeding sheet.

## 1.16.0

- Food types are now stored in the database and editable from the app: a
  new **Manage list** link next to the Food type dropdown on the Log
  Feeding sheet lets you add or remove entries yourself, instead of a
  fixed built-in list. Removing one only affects future entries — nothing
  already logged is changed.
- Fixed a bug where, after updating the add-on to a new version,
  Home Assistant's browser/webview could keep showing the previous
  version's UI (e.g. still showing the old free-text Food type field)
  until a manual hard-refresh, because the app's JS/CSS files had no
  cache-busting. They're now tagged with the running version, so a new
  version is always fetched fresh after an update — no manual refresh
  needed.

## 1.15.0

- Food type on the Log Feeding sheet is now a fixed dropdown (Layer feed,
  Pellets, Scratch grains, etc.) instead of free text, pre-filled with
  whatever you used last time — guarantees consistent spelling, which is
  what the feed-duration estimate's history grouping depends on. Entries
  logged before this change with a food type not on the list keep showing
  their original text rather than having it silently swapped out.

## 1.14.0

- Added a **Container was empty** checkbox to the Log Feeding sheet. Once
  logged twice for the same food type, the sheet shows a live estimate —
  right there while you're logging — of the average days between refills
  and days since the last one, to help gauge how long a bag/container of
  feed typically lasts.

## 1.13.1

- Fixed a bug where logging, editing, or deleting an entry could silently
  fail — the app would close the entry sheet as if it had saved even when
  the request actually failed (e.g. a brief network hiccup, often after
  the phone had been idle for a while), so the entry never showed up on a
  later refresh with no error shown. Failed saves now show a clear error
  and keep the entry sheet open with your input intact, instead of
  discarding it silently.
- Fixed a related issue where, if `ha_sensors_enabled` was on, a slow or
  unreachable Home Assistant could make a simple "log an egg" request
  hang for up to ~45 seconds (9 sequential HA API calls, 5s timeout each)
  before it either succeeded or errored. That push now always runs in the
  background instead of blocking the response — saving an entry is no
  longer affected by whether Home Assistant is reachable at that moment.

## 1.13.0

- Added an expand (⛶) button to the Trends chart to view it full-screen —
  tap again or press Esc to go back. Especially useful in landscape,
  which gives a long history much more room to read.

## 1.12.0

- The Trends tab chart is now a line chart instead of a grouped bar chart.
- Added a forecast backtest: the dashed forecast line now runs back
  through your history too, showing what it would have predicted for each
  past month using only the data available at the time — next to what
  actually happened, so you can see how well it's tracking. Also shown as
  a new "Forecast" column in the table.

## 1.11.0

- Added a 3-month egg collection forecast to the Trends tab, shown as
  lighter bars after your actual history. It's based on published laying
  rates for your flock's breeds (new **flock_isabrown_count** /
  **flock_sussex_count** options, defaulting to 3 and 2), scaled by your
  actual collection over the last 30 days once you've logged at least one
  egg — so it adapts to your real flock without any manual retraining.

## 1.10.0

- Added a new **Trends** tab (bottom navigation) with a monthly bar chart
  and table of eggs collected, sold, and used, so you can see how they
  trend over time instead of just the current totals. Choose a 3, 6, or
  12-month window from the dropdown.

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
