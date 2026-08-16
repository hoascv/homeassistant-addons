# Changelog

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
