# Changelog

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
