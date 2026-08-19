# Changelog

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
