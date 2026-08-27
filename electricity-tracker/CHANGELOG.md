# Changelog

## 1.11.0

- **Backup and restore**, in Settings. Download the whole database as a file, or
  put one back. It goes through ingress, which is already authenticated, so
  unlike the existing `/api/export` there is no port to open and no `api_token`
  to set — which was the only way to get the data out before this.
- Brings this add-on in line with Goal Tracker and Coop Tracker, which have had
  `/api/backup` and `/api/restore` for a while. Same shape: the download is
  taken through SQLite's own backup API rather than read off disk, since the
  background sync writes on its own connection and streaming the file could hand
  out a mid-write snapshot.
- Restore validates the file as one of *this* add-on's databases before
  replacing anything, and writes to a temporary path first — a truncated upload
  or another add-on's backup cannot leave this one with no database. The
  migrations re-run afterwards, so a backup from an older release comes back
  usable rather than with a schema the current code cannot query.
- **Fixed a leak while implementing it**: the sibling add-ons clean up their
  snapshot with `response.call_on_close`, and that callback does not reliably
  fire — leaving a full second copy of the database on disk after every
  download. Here the temporary copy is deleted before the response is built, so
  it cannot survive, and a test downloads three times and asserts nothing is
  left behind. The same latent leak exists in Goal Tracker and Coop Tracker.

## 1.10.0

- **A new Insights tab**, over 7/30/90 days, asking questions of the data
  already collected. Nothing extra is stored or synced: every figure is derived
  on request from rows that already existed.
- **What you paid** is the headline: your average kr/kWh weighted by *when* you
  used power, against what a flat consumer would have paid over the same hours.
  It is the number that says whether being on a spot tariff is worth anything,
  and it reports being worse as plainly as being better — a heat pump running at
  the daily peak should say so, not round to "no difference".
- **Your day** and **When power is cheapest** — average consumption by hour and
  average price by hour. The second comes from price history alone, so it works
  before Eloverblik is connected at all.
- **Always on** — the load that never goes away, from the quietest tenth of
  hours rather than the minimum: one outage hour would otherwise define it as
  zero. Annualised, since that is what makes it worth acting on.
- **Days worth a look** — most used, most spent, best and worst rate. A
  best-rate day is a different question from a cheap day and the more
  interesting one.
- **The car's share** of the house, with a guard: the car draws through the
  house meter so its share cannot really exceed 100%, and when the arithmetic
  says otherwise it is Eloverblik running days behind Easee. The card explains
  that instead of printing "102% of the house".
- Both new charts are expandable like every other, which came for free from
  1.9.0's render registry.

## 1.9.0

- **Every chart can be expanded.** An ⤢ button in each card header opens the
  same chart full width and more than twice as tall. A 160 px chart in a card
  is enough to see a shape and not enough to read a value off — this is for the
  times you want to look properly.
- Axis labels get denser with the room: hourly rather than three-hourly on the
  price curve and the hourly consumption chart, and about twice as many dates
  on the daily consumption and charging charts. Hover and long-press still give
  the exact value, since the tooltips are SVG `<title>` and come along unchanged.
- The renderer now records what each chart host last drew, so expanding needs no
  cooperation from the code that drew it — the price, consumption and charging
  charts all became expandable from one mechanism, and a chart added later gets
  it for free.
- Close with ✕, Escape, or by tapping outside. A full-screen overlay that only
  closes by hitting a small ✕ is a trap on a phone.
- The expanded render remaps its gradient ids: both charts are in the document
  at once, and `url(#id)` resolves to the first match, so reusing them would be
  a latent bug the day the two stopped matching.

## 1.8.0

- **The dashboard now says when the tariffs were never configured.**
  `grid_tariff_low/_normal/_high` and `transmission_tariff` default to 0.0
  because nobody can guess them — they depend on which grid company you are
  behind. Left at zero the arithmetic is still correct, and that is exactly the
  problem: the add-on shows a confident, precise, badly wrong price, and a
  charge costed at 0.12 kr/kWh against a real 1.20 reads as a bug in the
  software rather than a gap in its configuration. A notice now sits directly
  under the price breakdown, where the `Grid 0.00 / Transmission 0.00` it is
  complaining about is already on screen, and names the exact options to fill
  in. `/api/summary` carries the same thing as `price_config_warning`.
- It clears itself as soon as any grid band or the transmission tariff is set —
  a grid company with only a peak rate modelled counts as configured, not empty.

## 1.7.1

- **Fixed a four-hour charge being reported as a 159-hour session.** Easee's
  counter holds its final value indefinitely after a charge, and a car can sit
  plugged in for days without drawing anything — so an untrimmed session ran
  from the moment the cable went in to the moment it came out. A session's span
  is now the stretch where energy actually moved: from the sample the first kWh
  arrived from, to the sample the last one arrived at. Idle time with a cable in
  is not charging time.
- Deliberately not a split on idle: `session_energy_kwh` is cumulative, so
  cutting a paused-then-resumed charge in two would make the second half report
  the whole session's energy as its own. A pause stays inside its session.
- **Fixed a leftover counter reading being listed as a charging session.** A run
  where the counter never moved while this add-on was watching — 26.51 kWh with
  "cost covers 0.0 kWh" — is a value found lying around from a charge that
  happened before the samples begin, not something anything here observed. Those
  no longer appear in the history, and no longer inflate the range's kWh total.
  The live card still prefers a charge it actually watched, falling back to a
  bare counter reading only when there is nothing better.
- Note on cost, not a code change: if the charging cost looks implausibly cheap,
  check `grid_tariff_*` and `transmission_tariff` in the add-on's configuration.
  They default to 0.0, and with them unset the "full end-user price" is only
  spot plus VAT — around 0.12 kr/kWh where the real figure is nearer 1.20.

## 1.7.0

- **Charging history.** A new card listing every past charging session over
  7/30/90 days — energy, cost, duration, and the average rate that session
  actually paid — over a per-day chart of energy charged, with the roll-up
  above it: sessions, kWh, kr, and the average kr/kWh for the range. The
  per-session rate is the useful one if you charge on spot prices: it is what
  says whether the cheap hours are being caught. `/api/easee/history?days=N`
  returns the same thing.
- Sessions are derived from the samples already stored rather than a new table,
  so the history reaches back exactly as far as the samples do — no migration,
  and it works from existing data the moment this release starts.
- The costing is the same code path the live card uses, so a session cannot be
  priced one way on the dashboard and another way in the list underneath it —
  including the partial-cost handling from 1.6.1, which the history reports per
  session and counts in the totals.
- Averages are taken against the energy the cost actually covers rather than
  the full session, since a partially observed charge would otherwise report a
  rate it never paid.
- The chart uses monotone cubic interpolation rather than the Catmull-Rom
  spline the other charts use. Charging is mostly zeroes with occasional
  30 kWh nights, and an overshooting spline swings below the axis between them
  — drawing negative charging. Impossible quantities should not be drawable.
- A static wiring test now checks that every element id `app.js` looks up
  exists in the template. The dashboard has no build step, so a mistyped id
  fails silently: the card renders empty and nothing says why.

## 1.6.2

- **Fixed `CHARGING` being reported while nothing was flowing.** Easee holds
  `chargerOpMode` at 3 for as long as a cable is in — car full, car's own
  schedule pausing it, load balancing throttled to zero — and this add-on
  trusted that field alone, so the card read `CHARGING` next to `0.00 kW`.
  Measured power now decides, because it is the one field that cannot be wrong
  about whether energy is moving, and the state is reported as `PAUSED` with
  the cause: *"Plugged in but not drawing — limited by EV."*
- Easee's `reasonForNoCurrent` is now captured (new nullable
  `reason_for_no_current` column, added in place on upgrade) purely to supply
  that explanation. It is deliberately never consulted to *decide* the state:
  the code table is reverse-engineered by the community rather than documented,
  and a mis-mapped code must not be able to turn a visible charge into a pause.
  Easee's own opMode is still stored, and still reported as `raw_status`.
- **Fixed a finished charge being reported as an ever-lengthening current
  one.** A session was delimited only by the energy counter resetting, but
  Easee simply holds the counter after a charge ends, so no reset ever arrives.
  The run therefore absorbed every later sample: "Session started 2 h ago"
  became 3 h, then a day, describing a charge that was long over — bounded only
  by the 500-sample window. Unplugging (`DISCONNECTED`) now ends a session, and
  a finished one reads "Last session 2 h ago → ended 1 h ago".
- As a consequence, two charges that both start at 0 kWh and so never produce a
  decrease are no longer costed as a single session.
- Status and power now always come from the newest sample while energy and cost
  come from the most recent session that actually drew something — they answer
  different questions, and conflating them meant unplugging the car blanked the
  card instead of showing what the charge had cost.
- Twenty-two tests, covering the derivation, the session boundaries, and the
  in-place column migration.

## 1.6.1

- **Fixed EV session cost being wildly understated.** The cost was built purely
  from poll-to-poll deltas of Easee's session counter, which never prices the
  *first* sample of a session — so whatever the counter already read when this
  add-on first saw it was free. A 26.83 kWh charge showed 3.33 kr: an implied
  12 øre/kWh against a real price nearer 1.20 kr. Every existing test started
  its session at exactly 0.0 kWh, where there is nothing to miss, which is why
  the suite never caught it.
- Where the session's start *was* observed (a counter reset seen between two
  polls), that first sample's energy now gets priced at its own hour — closing
  the gap entirely.
- Where it was not — the add-on installed, restarted, or simply not running
  when the charge began — that energy happened at prices nothing recorded, and
  is not invented. The card now says so: *"Cost covers 2.83 of 26.83 kWh — the
  rest was charged before this add-on was watching"*, and the API exposes
  `cost_covers_kwh`, `cost_is_partial` and `session_start_observed`. The
  footnote is the repair: a cost silently describing less energy than the
  figure beside it is the bug, and disclosure is the only correct handling of
  the part that cannot be recovered.
- The session line now reads "Watching since" rather than "Session started"
  when the start was never seen, instead of asserting a start time it does not
  know.
- **Fixed a stale Easee reading being presented as live.** Easee is polled once
  per background tick and a failed sync writes no row, so the dashboard could
  show a status of any age with nothing to indicate it — and had no way to tell,
  since `/api/summary` did not carry the sync time. It now does, each reading
  carries its own `measured_at`, and past two ticks the status pill shows its
  age (`COMPLETED · 1 h ago`).
- Nine tests, eight of which fail against the previous release.

## 1.6.0

- **The consumption chart now draws two series instead of one blended
  line**: *Measured (Eloverblik)*, solid with the area wash beneath it, and
  *Live estimate (Saveeye)*, dashed in a second hue. Previously Saveeye's
  number for an hour Eloverblik had already reported was computed and then
  thrown away, so the chart could only ever show one source per hour and the
  legend claimed "Live estimate" for a curve that was mostly measured data.
  Both now ride along on every row, which makes the overlap — where you can
  actually see how closely the reader tracks the meter — the interesting
  part of the chart.
- `/api/consumption` rows gained `measured_kwh` and `saveeye_kwh`, either of
  which is null where that source has nothing for the hour. `kwh` and
  `source` are unchanged: still the blended series, still what every total
  and tile is computed from, so nothing double-counts.
- The two lines cover different stretches of the same axis (Eloverblik lags
  1-3 days; Saveeye only goes back as far as this add-on has been
  collecting), so the shared chart renderer now takes any number of series
  and draws each one only where it has data — a gap stays a gap rather than
  being bridged by a line implying readings we don't have.
- Hover text shows both numbers at once, and on the daily view flags any day
  a source only partly covers with its hour count, so a half-reported day
  can't be misread as a real drop in consumption.
- Legend entries appear only for the sources actually present in the range —
  "Today" typically shows Saveeye alone, since Eloverblik hasn't reported it
  yet.
- Verified with a browser render (headless Chrome through the local
  header-injecting proxy) against seeded data where the two sources overlap
  partially, in both light and dark, on the hourly, 7d and 30d views.

## 1.5.0

- **Redesigned all three charts** (price, hourly consumption, daily
  consumption) from a flat multi-color bar field to a single smooth line
  with a soft gradient-wash area beneath it — a calmer, less "candy-striped"
  read, applying the dataviz skill's mark specs (thin 2px line, ~25%→0%
  opacity wash, per-point hover, sparse axis labels) through one shared
  renderer all three now use. Cheapest/priciest and "live estimate" hours
  are now small dot markers on the line instead of whole-bar coloring/hatch.
- Fixed the "now" marker landing on the chart's first (00:00) point
  regardless of the actual time. `.find()` on an ascending array with a
  "time ≤ now" test always matches index 0 first — trivially true for every
  earlier row — so it never found the *most recent* row, only the earliest.
  This existed since the original bar chart too; a subtle opacity
  difference on a bar hid it, a misplaced dashed line on a smooth curve did
  not.
- Verified this release with an actual browser render (headless Chrome
  through a local proxy that supplies the ingress auth header) against
  seeded data spanning all three consumption sources
  (eloverblik/saveeye_estimate/saveeye_partial), not just by reading the code.

## 1.4.2

- Fixed the actual reason Saveeye consumption never showed up even with
  telemetry flowing and samples persisting correctly: leaving
  `saveeye_device_serial` empty — the documented default, for the common
  case of one Base reader — meant every consumption query filtered
  `saveeye_samples` on `device_serial = NULL`, matching nothing, no matter
  how much data existed. The write path already accepted telemetry from any
  device when this option is empty; the read path now resolves the same
  way — the device currently sending live telemetry, or failing that the
  most recent device seen in storage — instead of requiring the option to
  be filled in for reads to work at all.

## 1.4.1

- Fixed the price chart's colors: the "cheap"/"expensive" tiers were swapped
  (the most expensive third of the day rendered blue/"normal" and the middle
  third rendered red/"expensive"), and separately the chart's own min/max
  calculation artificially floored the minimum at 0 DKK/kWh — since real
  prices never get close to that, the cheap-price threshold sat below every
  actual price and no bar ever qualified as cheap. Both together made the
  chart's colors close to arbitrary. Also means the chart now uses its full
  vertical range instead of only the top ~60%, so hour-to-hour differences
  read more clearly.
- **Settings → Saveeye connection** now also shows the cumulative energy
  reading and how many samples have been persisted — the two numbers that
  actually determine whether an hourly estimate can be computed, previously
  invisible even though the live power reading looked fine. Diagnoses a real
  gap: a meter that reports live power but never sends the cumulative energy
  counter over MQTT will show a working "kW now" tile forever while
  consumption stays at zero, with nothing on the page saying why.

## 1.4.0

- **Live partial-hour Saveeye estimate.** Previously, the hourly estimate
  only ever filled a *completed* hour (samples bracketing both ends), which
  meant a freshly-connected Saveeye showed nothing for "Today" at all until
  after the current hour finished — technically honest, but not what
  watching a live power reading leads you to expect. The hour in progress
  now gets a `"source": "saveeye_partial"` estimate too, from the earliest
  sample after the hour started through the latest live reading. It
  undercounts a session started mid-hour (there's no reading for the energy
  used before Saveeye connected) rather than guessing backward past it, and
  still yields immediately to Eloverblik's measured figure once that lands.
  Shown with the same hatched styling as a completed estimate, with its own
  tooltip wording ("hour in progress").

## 1.3.0

- The consumption chart's **Today** view now shows one bar per hour instead
  of only daily totals — the 7d/14d/30d views stay aggregated to a bar per
  day. `/api/consumption` was already hourly; this is the dashboard catching
  up to it. Hours filled in by a Saveeye estimate keep the same hatched
  styling and legend the daily view already used.

## 1.2.0

- **Optional Easee Home charger support, read-only.** Authenticates with
  Easee's cloud API using your account email/password (`easee_enabled`) and
  polls charger state on the same ~5-minute tick as everything else:
  - a new **EV charging** dashboard card — status, live power, and the
    current/most recent session's energy and cost against the real price;
  - session cost is derived by diffing Easee's own per-session energy
    counter between polls (it resets at the start of each session) and
    pricing each delta at that hour's rate;
  - pushes `sensor.electricity_tracker_ev_power` to Home Assistant.
  - **Never controls the charger** — no start/stop/pause/throttle command is
    ever called; your existing charging schedule and the Easee app are
    unaffected by installing this.
- New `/api/easee/now` and `/api/easee/diagnose` endpoints, and a
  Settings-panel connection test (lists every charger on the account, to
  find `easee_charger_id` without guessing).

## 1.1.0

- **Optional Saveeye Base support.** A Saveeye HAN-port reader publishes live
  instant power and a cumulative energy counter over MQTT
  (`saveeye_enabled`); this add-on subscribes directly (no dependency on
  Saveeye's own community Home Assistant integration) and:
  - shows live **kW now** on the dashboard, with the cost rate at the
    current price;
  - fills in a same-day **hourly consumption estimate** for any hour
    Eloverblik hasn't reported yet, by interpolating the meter's cumulative
    counter at each hour boundary — the same principle a physical meter's
    tally uses. Never overrides Eloverblik's measured figure once an hour
    has one; marked `"source": "saveeye_estimate"` everywhere it appears.
  - pushes `sensor.electricity_tracker_power_now` to Home Assistant.
- New `/api/saveeye/now` endpoint and a Settings-panel status view (live
  connection state + most recent reading) — same role
  `/api/eloverblik/diagnose` plays for Eloverblik.
- Needs an MQTT broker between the Base and this add-on — the Home Assistant
  "Mosquitto broker" add-on is the documented path. See DOCS.md's *Setting
  up Saveeye* for the full walkthrough, including which hostname is correct
  from which side (the physical reader needs your LAN address; this add-on
  needs `core-mosquitto`, the two are not interchangeable).

## 1.0.1

- Fixed consumption sync failing on every real account with
  `#20013: No meteringpoints in request conforms to valid meteringpoint
  format.` Energinet's own technical description documents the
  `GetTimeSeries` request body as `{"meteringPointIds": [...]}`, but the live
  API actually rejects that and only accepts a nested
  `{"meteringPoints": {"meteringPoint": [...]}}` shape — confirmed against
  every working community client. Prices were unaffected; only consumption
  sync was broken.

## 1.0.0

- First release. Danish day-ahead electricity spot prices (Energi Data
  Service's `DayAheadPrices` dataset, 15-minute resolution since the market
  moved off hourly on 2025-10-01) for `DK1`/`DK2`, combined with your own
  smart-meter consumption (Eloverblik's Customer API, via a refresh token you
  generate yourself) into a full end-user price: spot + your grid company's
  time-of-day tariff + Energinet's transmission tariff + elafgift, all under
  VAT.
- Ingress dashboard: current price with its component breakdown, a
  today/tomorrow 15-minute price chart, and today/yesterday/week/month
  consumption + cost with a daily chart. Works as a price-only tracker before
  Eloverblik is configured.
- Settings panel includes a live Eloverblik connection test that lists every
  metering point the configured token can see, so the 18-digit GSRN id can be
  copied straight into `eloverblik_metering_point` rather than hunted for.
- Pushes `sensor.electricity_tracker_price_now` and
  `sensor.electricity_tracker_consumption_today` to Home Assistant, each with
  the full breakdown as attributes.
- `/api/health`, `/api/stats`, `/api/export` for the Add-on Watchdog and a
  data pipeline; a published port (off by default) behind `api_token`, same
  pattern as Gym Tracker and Coop Tracker.
