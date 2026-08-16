import json
from io import BytesIO
from unittest.mock import patch

import pytest

import energidataservice as eds


class _FakeResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_day_ahead_prices_converts_mwh_to_kwh():
    body = {
        "records": [
            {"TimeDK": "2026-08-16T00:00:00", "PriceArea": "DK2", "DayAheadPriceDKK": 1000.0},
            {"TimeDK": "2026-08-16T00:15:00", "PriceArea": "DK2", "DayAheadPriceDKK": 500.0},
        ]
    }
    with patch("urllib.request.urlopen", return_value=_FakeResponse(json.dumps(body).encode())):
        rows = eds.fetch_day_ahead_prices("DK2", "2026-08-16", "2026-08-17")
    assert rows == [
        {"time_dk": "2026-08-16T00:00:00", "price_dkk_kwh": 1.0},
        {"time_dk": "2026-08-16T00:15:00", "price_dkk_kwh": 0.5},
    ]


def test_fetch_day_ahead_prices_skips_incomplete_records():
    body = {"records": [{"TimeDK": None, "DayAheadPriceDKK": 100.0}, {"TimeDK": "x", "DayAheadPriceDKK": None}]}
    with patch("urllib.request.urlopen", return_value=_FakeResponse(json.dumps(body).encode())):
        rows = eds.fetch_day_ahead_prices("DK2", "2026-08-16", "2026-08-17")
    assert rows == []


def test_fetch_day_ahead_prices_raises_on_bad_json():
    with patch("urllib.request.urlopen", return_value=_FakeResponse(b"not json")):
        with pytest.raises(eds.EnergiDataServiceError):
            eds.fetch_day_ahead_prices("DK2", "2026-08-16", "2026-08-17")


def test_fetch_day_ahead_prices_raises_on_connection_error():
    import urllib.error

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
        with pytest.raises(eds.EnergiDataServiceError):
            eds.fetch_day_ahead_prices("DK2", "2026-08-16", "2026-08-17")
