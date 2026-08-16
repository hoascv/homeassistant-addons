"""Client for Energinet's Energi Data Service — Danish day-ahead spot prices.

Public and unauthenticated. `Elspotprices` (hourly resolution) stopped being
updated on 2025-10-01, the day the day-ahead market moved to 15-minute
resolution; `DayAheadPrices` is its replacement and is queried here. Prices
come back in DKK/MWh and are converted to DKK/kWh, since that is the unit
consumption is metered in.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.energidataservice.dk"
DATASET = "DayAheadPrices"


class EnergiDataServiceError(Exception):
    pass


def fetch_day_ahead_prices(price_area, start, end, timeout=15):
    """Quarter-hourly spot prices for `price_area` in [start, end).

    `start`/`end` are ISO-ish strings (e.g. "2026-08-16" or
    "2026-08-16T00:00") interpreted by the API against Danish local time
    (TimeDK). Returns a list of {"time_dk": ISO str, "price_dkk_kwh": float},
    ascending by time.
    """
    params = {
        "filter": json.dumps({"PriceArea": [price_area]}),
        "start": start,
        "end": end,
        "sort": "TimeDK ASC",
        "limit": 0,
    }
    url = f"{API_BASE}/dataset/{DATASET}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise EnergiDataServiceError(f"HTTP {exc.code}: {exc.read()[:200]!r}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise EnergiDataServiceError(f"request failed: {exc}") from exc
    except ValueError as exc:
        raise EnergiDataServiceError(f"bad JSON: {exc}") from exc

    records = body.get("records") or []
    out = []
    for r in records:
        time_dk = r.get("TimeDK")
        price_dkk_mwh = r.get("DayAheadPriceDKK")
        if time_dk is None or price_dkk_mwh is None:
            continue
        out.append({"time_dk": time_dk, "price_dkk_kwh": price_dkk_mwh / 1000.0})
    return out
