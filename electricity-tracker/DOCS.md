# Electricity Tracker

Danish electricity spot prices and your own smart-meter consumption, combined
into what you actually pay per kWh — spot price plus your grid company's
tariff, Energinet's transmission tariff, elafgift, and VAT.

Three independent data sources, none of which needs another:

- **Prices** — Energinet's [Energi Data Service](https://www.energidataservice.dk/),
  public and unauthenticated. Works out of the box; only `price_area` needs
  setting.
- **Consumption** — [Eloverblik](https://eloverblik.dk) (Energinet's DataHub
  customer portal), your own smart-meter readings. Needs a refresh token you
  generate yourself (see below). Without it, this add-on is a price tracker
  only — the price side of the dashboard works either way. This is the
  **measured, official** number — what you're actually billed on — but it
  typically lags 1-3 days behind.
- **Saveeye** (optional) — a Saveeye Base HAN-port reader, if you have one:
  live instant power and a same-day *estimate* of hourly consumption, over
  MQTT. Fills the gap while Eloverblik catches up; never overrides Eloverblik
  once it has the real number for an hour. See *Setting up Saveeye*, below.
- **Easee** (optional) — an Easee Home charger, if you have one: read-only
  monitoring of charging status, live power, and what the current/last
  charging session cost against the real price. This add-on never starts,
  stops, or throttles charging — see *Setting up Easee*, below.

## Setting up prices

Set **price_area** to `DK1` (west of the Great Belt) or `DK2` (east,
including Copenhagen). That's it — prices sync automatically.

Since 2025-10-01 the day-ahead market clears at **15-minute** resolution, not
hourly, so that's the resolution stored and charted here.

## Setting up consumption (Eloverblik)

1. Log in at [eloverblik.dk](https://eloverblik.dk) with MitID.
2. Go to **Data access** → **Data access via API** (or similar wording — the
   portal moves this occasionally) and create a **refresh token**. It's a long
   JWT string; copy the whole thing.
3. Paste it into **eloverblik_refresh_token** on this add-on's Configuration
   tab.
4. You also need your **metering point id** (an 18-digit GSRN number) in
   **eloverblik_metering_point**. Find it either on the Eloverblik site
   itself (under your metering points), or open this add-on's **Settings**
   panel and click **Test Eloverblik connection** — it calls Eloverblik live
   with your token and lists every metering point it can see, so you can copy
   the right id without hunting for it.

Consumption syncs roughly once an hour (Eloverblik's access token is valid
24h and is refreshed automatically). Eloverblik itself commonly lags 1-3 days
behind for **measured** readings — very recent hours may be missing or
marked `quality: "A04"` (estimated) until the real reading lands; nothing
here has to reconcile that, later syncs simply overwrite the row with the
firmer number once Eloverblik has it.

## Setting up Saveeye (optional, live instant power + same-day hourly)

A Saveeye Base clips onto your smart meter's HAN port and publishes live
telemetry over MQTT. Two things need to be wired up: a broker between the
Base and this add-on, and this add-on's own `saveeye_*` options.

1. **Install an MQTT broker**, if you don't already run one: **Settings →
   Add-ons → Add-on Store**, install the official **Mosquitto broker**
   add-on, and start it. In its **Configuration** tab, add a dedicated login
   (username/password) for the Saveeye Base to use, rather than reusing a
   real Home Assistant account's password.
2. **Point the Saveeye app's MQTT settings at that broker.** The Base itself
   is a device on your LAN, not inside Home Assistant's own container
   network, so it needs your Home Assistant host's **LAN address** (its IP,
   or `homeassistant.local`), not `core-mosquitto` — that hostname only
   resolves *inside* Supervisor's network, which is where this add-on lives,
   not where the physical Base reader is. Port `1883`, plus the login you
   created. Follow Saveeye's own
   [Home Assistant guide](https://github.com/saveeye/SaveEye-HA-Guide) for
   the exact steps in their app, since the UI moves occasionally.
3. **Configure this add-on** — the reverse direction, this add-on reaching
   the broker from *inside* Supervisor's network, where `core-mosquitto`
   *is* correct (it's the Mosquitto broker add-on's fixed internal hostname):
   - **saveeye_enabled**: `true`.
   - **saveeye_mqtt_host** / **saveeye_mqtt_port**: leave at the defaults
     (`core-mosquitto` / `1883`) unless you're running a different broker.
   - **saveeye_mqtt_username** / **saveeye_mqtt_password**: the same login
     you created in step 1.
   - **saveeye_mqtt_topic**: leave at the default (`saveeye/telemetry`)
     unless you changed it in the Saveeye app.
   - **saveeye_device_serial**: leave empty unless you ever have more than
     one Base reporting to the same broker — empty accepts telemetry from
     whichever one publishes first.
4. Restart the add-on, then check **Settings → Saveeye connection** in the
   dashboard (or `/api/saveeye/now`) for a live status: connected, and the
   most recent reading.

This add-on does **not** need Saveeye's own community Home Assistant
integration — it speaks MQTT to the broker directly and keeps its own data,
independent of whatever entities that integration might also create.

What this buys you: a live **kW now** reading on the dashboard (with the
cost rate at the current price), and, once a couple of hours of samples
exist, same-day hourly consumption for hours Eloverblik hasn't reported yet
— computed by interpolating the meter's own cumulative energy counter at
each hour boundary and taking the difference, the same principle a physical
meter's tally uses. Marked `"source": "saveeye_estimate"` everywhere it
appears (API, dashboard chart), and always superseded by Eloverblik's
measured figure once that hour arrives.

## Setting up Easee (optional, EV charging monitoring)

Read-only. This add-on authenticates with your Easee account credentials
directly (the same pattern Gym Tracker uses for Garmin) — there's no
app-generated token to create first.

1. **easee_enabled**: `true`.
2. **easee_username** / **easee_password**: the email and password you use
   to log into the Easee app.
3. **easee_charger_id**: leave empty to use the first charger on the
   account. If you have more than one, restart the add-on once, then open
   **Settings → Test Easee connection** in the dashboard (or
   `/api/easee/diagnose`) to list every charger's id and name, and copy the
   one you want in.
4. Restart the add-on. State refreshes on the same ~5-minute tick as
   everything else — live in the sense of "current", not sub-minute.

What this buys you: an **EV charging** card on the dashboard with live
status (`CHARGING`, `COMPLETED`, `AWAITING_START`, ...), power, and the
current/most recent session's energy and cost — the cost is derived by
diffing Easee's own per-session energy counter between polls and pricing
each delta at that hour's real rate, the same principle Saveeye's estimate
uses, just scoped to one appliance's session rather than the whole house. A
push of `sensor.electricity_tracker_ev_power` to Home Assistant.

Two honesty notes on that card, because the energy and the cost do not always
describe the same thing:

- **Energy** is Easee's own session counter — the whole session, however long
  it has been running. **Cost** is only what this add-on was awake to price. If
  the add-on was installed, restarted, or simply not running when a charge
  began, the card says *"Cost covers 2.83 of 26.83 kWh"* rather than quietly
  reporting a charge that looks ten times too cheap. Energy consumed before the
  first poll happened at prices nothing recorded, and is not invented.
- **The status is as old as the last poll.** Easee is read once per background
  tick, and a failed sync writes nothing at all, so the reading could be older
  than it looks. Past two ticks the status pill shows its own age
  (`COMPLETED · 1 h ago`) instead of presenting a stale reading as live.
- **`PAUSED` is this add-on's word, not Easee's.** Easee keeps `chargerOpMode`
  at `CHARGING` for the whole time a cable is in, including when nothing is
  flowing — the car is full, its own schedule has paused it, load balancing has
  throttled it to zero. Measured power decides instead, because it is the one
  field that cannot be wrong about whether energy is moving, and the card says
  why: *"Plugged in but not drawing — limited by EV."* Easee's own opMode is
  still stored and still reported as `raw_status`.

The reason text comes from `reasonForNoCurrent`, whose code table is
reverse-engineered by the community rather than documented by Easee. It is used
only to explain a state, never to decide one — a mis-mapped code cannot turn a
charge that is visibly happening into a pause.

Charging energy is already included in your whole-house numbers from
Eloverblik (and Saveeye, if configured) — Easee's session figures are a
breakdown of part of that same total, not counted again on top of it.

## Setting up the full end-user price

Only spot price and VAT (25%) have safe, stable defaults. Everything else —
your grid company's tariff and Energinet's own transmission tariff — varies
by company and changes over time, so this add-on does not guess at them:

- **grid_tariff_low / grid_tariff_normal / grid_tariff_high** (DKK/kWh, excl.
  VAT): your grid company's net tariff, which is commonly time-differentiated
  into up to three bands. Look yours up on your grid company's own site —
  find out which one you're with via the Eloverblik metering point details,
  or your electricity bill.
  - **grid_tariff_high_start/end**, **grid_tariff_low_start/end**: the
    "HH:MM" windows for the high/low bands (default: low = 00:00-06:00, high
    = 17:00-21:00). Whatever's outside both uses `grid_tariff_normal`.
  - **grid_tariff_high_weekdays** (default `1,2,3,4,5`, Mon-Fri):
    high only applies on these ISO weekdays (1=Monday). Empty means every day.
  - **grid_tariff_high_months** (default `1,2,3,10,11,12`, the Danish winter
    half): high only applies in these calendar months. Empty means all year.
- **transmission_tariff** (DKK/kWh, excl. VAT): Energinet's own system +
  transmission tariffs, combined into one flat number (both are
  non-time-differentiated). Current rates are published at
  [energinet.dk/el/elmarkedet/tariffer](https://energinet.dk/el/elmarkedet/tariffer).
- **electricity_tax** (DKK/kWh, excl. VAT): elafgift, set by the Danish
  Parliament. The 2026/2027 rate is **0.008** (0.8 øre) — a large cut from
  2025's ~0.90 DKK/kWh — and is the default here. It typically changes at the
  turn of a year; update it when it does.
- **vat_rate** (default `0.25`): Danish VAT, applied on top of everything
  above.

Leaving a tariff at its `0.0` default is honest: the dashboard then shows
spot + tax + VAT, not a number silently wrong by whatever your grid company
actually charges.

## Insights

A second tab, over 7/30/90 days, answering questions of the data already
collected — nothing extra is stored or synced for it.

- **What you paid** — your average kr/kWh, weighted by *when* you actually used
  power, against what a flat consumer would have paid over the same hours. This
  is the number that says whether being on a spot tariff is worth anything to
  you, and it reports being worse just as plainly as being better.
- **Your day** — average consumption by hour, so "shift usage to cheap hours"
  becomes a specific hour to move something to.
- **When power is cheapest** — average price by hour from price history alone,
  so it works before Eloverblik is connected at all.
- **Always on** — the load that never goes away, estimated from the quietest
  tenth of hours rather than the minimum, so one outage hour cannot define it.
  Annualised, because that is the number that makes it worth acting on.
- **Days worth a look** — most used, most spent, and the best and worst rate
  achieved. A best-rate day is a different question from a cheap day, and the
  more interesting one: it is the day the timing worked.
- **The car's share** — kWh charged and what fraction of the house that was.
  The car draws through the house meter, so a share over 100% means Eloverblik
  is running behind the charger; the card says so rather than printing it flat.

## Dashboard

Every chart has an **⤢** button in its card header. A chart in a card is about
160 px tall, which is enough to see a shape and not enough to read a value off;
expanding re-renders the same data more than twice as tall and full width, with
denser axis labels — hourly instead of three-hourly on the price curve, and
roughly twice as many dates on the daily charts. Hover or long-press still
gives the exact value. Close with **✕**, **Escape**, or by tapping outside.

- **Price now** — the current 15-minute price, full end-user total, with the
  spot/tariff/tax/VAT breakdown underneath and today's cheapest/priciest hour.
  If the grid or transmission tariffs are still at their 0.0 defaults, a notice
  appears here saying so: the arithmetic is correct either way, which is
  precisely why it needs pointing out — a price of 0.12 kr/kWh where the real
  figure is nearer 1.20 looks like a broken add-on rather than an unfilled
  configuration field, and every cost in the add-on is understated by the same
  amount until they are set.
- **Price today/tomorrow** — a smooth line chart at 15-minute resolution,
  with the cheapest and priciest hour dotted and a marker on the current
  quarter. Tomorrow's day-ahead auction clears in the early afternoon (CET),
  so the "Tomorrow" toggle stays disabled until that's published.
- **Consumption** — today/yesterday/week/month kWh and cost, plus a chart
  over today (hourly) or 7/14/30 days (daily). Hidden behind an explanation
  until Eloverblik is configured. Two lines when both sources have something
  to say: **Measured (Eloverblik)**, a solid line with a soft area beneath
  it, and **Live estimate (Saveeye)**, a dashed line. They cover different
  stretches — Eloverblik runs 1-3 days behind, Saveeye only goes back as far
  as this add-on has been collecting — so the dashed line typically carries
  on past where the solid one stops, and the overlap is where you can see
  how closely the two agree. Hovering any point shows both numbers, the
  cost, and (on the daily view) an hour count for any day either source only
  partly covers. A source with nothing in range is simply absent, legend
  entry included.
- **kW now** — under the price card, once Saveeye is enabled and reporting:
  live instant power plus what that costs per hour at the current price.
- **EV charging** — once Easee is enabled: status, live power, and the
  current/last session's energy and cost.
- **Charging history** — every past charging session over 7/30/90 days (kWh,
  cost, duration, and the average rate that session actually paid), a per-day
  chart of energy charged, and the roll-up: sessions, kWh, kr and the average
  kr/kWh across the range. The per-session rate is the interesting one if you
  charge on spot prices — it is what tells you whether the cheap hours are
  being caught.
- **Settings → Test Eloverblik connection** — a live round-trip to Eloverblik
  with your configured token, listing every metering point it can see.
- **Settings → Saveeye connection** — live MQTT connection status and the
  most recent reading.
- **Settings → Test Easee connection** — a live round-trip to Easee with
  your configured account, listing every charger it can see.

## Home Assistant sensors

Pushed via the Supervisor API (`homeassistant_api: true`) every sync tick
(~5 minutes):

- `sensor.electricity_tracker_price_now` — DKK/kWh, full price. Attributes
  include the spot/tariff/tax/VAT breakdown and today's cheapest/priciest
  hour (and whether tomorrow's prices are published yet) — enough for an
  automation that shifts load to the cheapest remaining hour.
- `sensor.electricity_tracker_consumption_today` — kWh so far today (blends
  in a Saveeye estimate for any hour Eloverblik hasn't reported yet, if
  configured). Attributes carry yesterday/week/month kWh and cost. Only
  pushed once Eloverblik is configured.
- `sensor.electricity_tracker_power_now` — instant power, Watts. Only pushed
  once Saveeye is enabled and has reported at least once.
- `sensor.electricity_tracker_ev_power` — EV charging power, Watts.
  Attributes carry status (plus `charger_op_mode`, `charging` and `reason`),
  session energy, and session cost. Only pushed
  once Easee is enabled and a charger has been found.

## Endpoints

- `/` — the ingress dashboard.
- `/api/summary` — current price, today/tomorrow curves, cheapest/priciest
  hour, consumption totals, last sync times.
- `/api/prices?days=N` (default 2, max 14) — quarter-hourly prices with the
  full breakdown, for whatever's stored.
- `/api/consumption?days=N` (default 14, max 90) — hourly consumption with
  matched price and cost, for whatever's stored (empty until a metering point
  is configured and has synced). Each row's `"kwh"`/`"source"` is the blended
  series the totals are built from: `"source"` is `"eloverblik"`,
  `"saveeye_estimate"` or `"saveeye_partial"`. Alongside it each row also
  carries the two sources unblended — `"measured_kwh"` (null for an hour
  Eloverblik hasn't reported) and `"saveeye_kwh"` (null when Saveeye has no
  estimate for it) — which is what the dashboard's two lines are drawn from.
  An hour both cover has both, even though only Eloverblik's shows up in
  `"kwh"`.
- `/api/eloverblik/diagnose` — live Eloverblik connection test (see Settings,
  above).
- `/api/saveeye/now` — live Saveeye MQTT connection status and the most
  recent telemetry reading.
- `/api/easee/now` — the current/most recent charging session's live state
  and cost so far. `session_energy_kwh` is Easee's own counter;
  `cost_covers_kwh` is how much of it `session_cost_dkk` accounts for, and
  `cost_is_partial` is true when those differ. `session_start_observed` says
  whether the session's beginning was actually seen, and `measured_at` is when
  the reading was taken. `status` is the derived reading (`PAUSED` where Easee
  says `CHARGING` with no power), `raw_status` is Easee's own `chargerOpMode`,
  `charging` is the boolean, and `reason` explains a non-flowing state.
  `session_ended_at` is set once the car has been unplugged since.
- `/api/easee/history?days=N` (default 30, max 365) — past charging sessions
  newest first, per-day totals, and the roll-up. Each session carries
  `energy_kwh`, `cost_dkk`, `avg_dkk_kwh`, `duration_minutes`, `ongoing`, and
  the same `cost_is_partial` / `cost_covers_kwh` pair the live card uses.
- `/api/easee/diagnose` — live Easee connection test, listing every charger
  on the account (see Settings, above).
- `/api/health`, `/api/stats`, `/api/export` — for the Add-on Watchdog and a
  pipeline: liveness, row counts, and a full data dump.

Everything above requires Home Assistant's ingress, except when a request
carries `Authorization: Bearer <api_token>` — the published port (if you
mapped one) needs that; ingress never does. `api_token` is off by default,
which also means the published port is off by default. `restrict_to_user_ids`
narrows ingress access to specific Home Assistant users on top of
`panel_admin: true`.

## Backup and restore

**Settings → Backup & restore** downloads the whole database as a `.db` file,
and takes one back. Nothing needs configuring for it: it goes through Home
Assistant's ingress, which is already authenticated, so there is no port to open
and no token to set.

The download is taken through SQLite's own backup API rather than read off
disk — the background sync writes on its own connection, so streaming the file
could hand out a snapshot taken mid-write.

**Restoring replaces everything currently stored.** The file is validated as one
of this add-on's databases before anything is touched, and written to a
temporary path first, so a truncated upload or somebody else's backup cannot
leave the add-on without a database. The migrations re-run afterwards, so a
backup taken before a column existed comes back usable rather than with a schema
the current code cannot query.

`/data` is inside Home Assistant's own add-on backups too; this is for when you
want a copy in your own hands, or to move to another install.

## Notes

- Prices are stored keyed by Danish local wall-clock time; consumption is
  stored keyed by UTC. Combining the two (for cost) converts consumption's
  UTC hour into Denmark's local time — including across the DST transitions —
  and averages that hour's four quarter-hour prices.
- Averages in the history are computed against the energy the cost actually
  covers, not the full session — a partially observed charge would otherwise
  report a rate it never paid. Sessions in that state are counted under the
  card, so a total missing some of its cost says so.
- Charging history is derived from the stored samples rather than kept as its
  own table, so it goes back exactly as far as the samples do — from when the
  add-on was first enabled with Easee configured.
- A session's duration is the stretch where energy actually moved: from the
  sample the first kWh arrived from, to the sample the last one arrived at.
  Time spent plugged in without drawing — before a charge starts, or after it
  finishes — is not charging time, and counting it turned a four-hour charge
  into a 159-hour one. A charge that pauses and resumes stays one session; the
  counter is cumulative, so splitting it would make the second half report the
  whole session's energy as its own.
- A run where the counter never moved is not listed in the history at all. That
  is a value left over from a charge that happened before the samples begin,
  not a session anything here observed.
- A charging session ends when the car is unplugged (`DISCONNECTED`) or when
  Easee's counter resets. `COMPLETED` does not end it — that is the tail of the
  session the card is meant to show. `OFFLINE` does not either: it means the
  charger is unreachable, which says nothing about whether the cable is in.
- A day with a DST transition has 23 or 25 hourly consumption points; nothing
  here assumes 24.
- Two price areas' data can coexist in the database (e.g. if you ever change
  `price_area`) — history for the old area is kept, not deleted.
- Saveeye's hourly estimate only ever fills a genuine gap *in the blended
  series*: an hour Eloverblik has already reported is never recomputed or
  overridden from Saveeye, even if samples exist for it too. That estimate
  is still reported separately as `"saveeye_kwh"` and charted as its own
  line, so the two can be compared without either being folded into the
  other's totals. A completed hour is only estimated when real
  samples bracket both its start and end — no interpolation is ever
  extrapolated past the edge of what was actually observed. The hour
  currently in progress is the one exception: it gets a running
  `"source": "saveeye_partial"` estimate from whenever Saveeye's first
  sample in that hour arrived through the latest reading, so "Today" shows
  something immediately rather than staying at zero until midnight. A
  session that started mid-hour is undercounted for that hour rather than
  guessed at backward.
- `saveeye_mqtt_password` is stored like any other add-on secret
  (`/data/options.json` on the host); it's whatever login you created on the
  Mosquitto broker for this purpose, not a Home Assistant account password.
- Easee's own `sessionEnergy` field resets at the start of each new charging
  session, which is what lets a session's cost be attributed by diffing
  consecutive polls rather than needing Saveeye-style interpolation of an
  ever-increasing counter — a decrease between two polls is read as "a new
  session started here," and only samples from that point on count.
- This add-on only ever reads Easee's state — it never calls any command
  endpoint (start/stop/pause/resume/set current). Your own charging schedule,
  the Easee app, and any smart-charging settings you already have are
  completely unaffected by installing this.
