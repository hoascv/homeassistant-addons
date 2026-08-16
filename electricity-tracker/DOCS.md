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

## Dashboard

- **Price now** — the current 15-minute price, full end-user total, with the
  spot/tariff/tax/VAT breakdown underneath and today's cheapest/priciest hour.
- **Price today/tomorrow** — a bar chart at 15-minute resolution. Tomorrow's
  day-ahead auction clears in the early afternoon (CET), so the "Tomorrow"
  toggle stays disabled until that's published.
- **Consumption** — today/yesterday/week/month kWh and cost, plus a daily
  bar chart over 7/14/30 days. Hidden behind an explanation until Eloverblik
  is configured. A day partly covered by a Saveeye estimate is shown with a
  hatched bar and a legend note; hovering any bar shows the breakdown.
- **kW now** — under the price card, once Saveeye is enabled and reporting:
  live instant power plus what that costs per hour at the current price.
- **Settings → Test Eloverblik connection** — a live round-trip to Eloverblik
  with your configured token, listing every metering point it can see.
- **Settings → Saveeye connection** — live MQTT connection status and the
  most recent reading.

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

## Endpoints

- `/` — the ingress dashboard.
- `/api/summary` — current price, today/tomorrow curves, cheapest/priciest
  hour, consumption totals, last sync times.
- `/api/prices?days=N` (default 2, max 14) — quarter-hourly prices with the
  full breakdown, for whatever's stored.
- `/api/consumption?days=N` (default 14, max 90) — hourly consumption with
  matched price and cost, for whatever's stored (empty until a metering point
  is configured and has synced). Each row's `"source"` is `"eloverblik"` or
  `"saveeye_estimate"`.
- `/api/eloverblik/diagnose` — live Eloverblik connection test (see Settings,
  above).
- `/api/saveeye/now` — live Saveeye MQTT connection status and the most
  recent telemetry reading.
- `/api/health`, `/api/stats`, `/api/export` — for the Add-on Watchdog and a
  pipeline: liveness, row counts, and a full data dump.

Everything above requires Home Assistant's ingress, except when a request
carries `Authorization: Bearer <api_token>` — the published port (if you
mapped one) needs that; ingress never does. `api_token` is off by default,
which also means the published port is off by default. `restrict_to_user_ids`
narrows ingress access to specific Home Assistant users on top of
`panel_admin: true`.

## Notes

- Prices are stored keyed by Danish local wall-clock time; consumption is
  stored keyed by UTC. Combining the two (for cost) converts consumption's
  UTC hour into Denmark's local time — including across the DST transitions —
  and averages that hour's four quarter-hour prices.
- A day with a DST transition has 23 or 25 hourly consumption points; nothing
  here assumes 24.
- Two price areas' data can coexist in the database (e.g. if you ever change
  `price_area`) — history for the old area is kept, not deleted.
- Saveeye's hourly estimate only ever fills a genuine gap: an hour Eloverblik
  has already reported is never recomputed or overridden from Saveeye, even
  if samples exist for it too. An hour is only estimated when real samples
  bracket both its start and end — no interpolation is ever extrapolated
  past the edge of what was actually observed.
- `saveeye_mqtt_password` is stored like any other add-on secret
  (`/data/options.json` on the host); it's whatever login you created on the
  Mosquitto broker for this purpose, not a Home Assistant account password.
