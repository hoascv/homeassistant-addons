# Electricity Tracker

Danish electricity spot prices and your own smart-meter consumption, combined
into what you actually pay per kWh — spot price plus your grid company's
tariff, Energinet's transmission tariff, elafgift, and VAT.

Two independent Danish data sources, neither of which needs the other:

- **Prices** — Energinet's [Energi Data Service](https://www.energidataservice.dk/),
  public and unauthenticated. Works out of the box; only `price_area` needs
  setting.
- **Consumption** — [Eloverblik](https://eloverblik.dk) (Energinet's DataHub
  customer portal), your own smart-meter readings. Needs a refresh token you
  generate yourself (see below). Without it, this add-on is a price tracker
  only — the price side of the dashboard works either way.

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
  is configured.
- **Settings → Test Eloverblik connection** — a live round-trip to Eloverblik
  with your configured token, listing every metering point it can see.

## Home Assistant sensors

Pushed via the Supervisor API (`homeassistant_api: true`) every sync tick
(~5 minutes):

- `sensor.electricity_tracker_price_now` — DKK/kWh, full price. Attributes
  include the spot/tariff/tax/VAT breakdown and today's cheapest/priciest
  hour (and whether tomorrow's prices are published yet) — enough for an
  automation that shifts load to the cheapest remaining hour.
- `sensor.electricity_tracker_consumption_today` — kWh so far today.
  Attributes carry yesterday/week/month kWh and cost. Only pushed once
  Eloverblik is configured.

## Endpoints

- `/` — the ingress dashboard.
- `/api/summary` — current price, today/tomorrow curves, cheapest/priciest
  hour, consumption totals, last sync times.
- `/api/prices?days=N` (default 2, max 14) — quarter-hourly prices with the
  full breakdown, for whatever's stored.
- `/api/consumption?days=N` (default 14, max 90) — hourly consumption with
  matched price and cost, for whatever's stored (empty until a metering point
  is configured and has synced).
- `/api/eloverblik/diagnose` — live Eloverblik connection test (see Settings,
  above).
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
