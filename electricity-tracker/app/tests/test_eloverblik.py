import json
import urllib.error
from io import BytesIO
from unittest.mock import patch

import pytest

import eloverblik


class _FakeResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_get_access_token_reads_result_field():
    body = {"result": "short-lived-token"}
    with patch("urllib.request.urlopen", return_value=_FakeResponse(json.dumps(body).encode())):
        token = eloverblik.get_access_token("refresh-token-value")
    assert token == "short-lived-token"


def test_get_access_token_raises_when_result_missing():
    with patch("urllib.request.urlopen", return_value=_FakeResponse(json.dumps({}).encode())):
        with pytest.raises(eloverblik.EloverblikError):
            eloverblik.get_access_token("refresh-token-value")


def test_get_access_token_raises_on_http_error():
    err = urllib.error.HTTPError("url", 403, "Forbidden", {}, BytesIO(b"bad token"))
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(eloverblik.EloverblikError):
            eloverblik.get_access_token("bad-token")


def test_list_metering_points_returns_result_list():
    body = {"result": [{"meteringPointId": "5713131111111111"}]}
    with patch("urllib.request.urlopen", return_value=_FakeResponse(json.dumps(body).encode())):
        points = eloverblik.list_metering_points("access-token")
    assert points == [{"meteringPointId": "5713131111111111"}]


def test_list_metering_points_tolerates_missing_result():
    with patch("urllib.request.urlopen", return_value=_FakeResponse(json.dumps({}).encode())):
        points = eloverblik.list_metering_points("access-token")
    assert points == []


# A realistic single-day hourly response shape, per Energinet's technical
# description (doc 19/11830-1): one MyEnergyData_MarketDocument per metering
# point, one TimeSeries, one Period per day, Point.position 1-based within
# the period, out_Quantity.quantity as a string.
def _sample_time_series_body():
    return {
        "result": [
            {
                "MyEnergyData_MarketDocument": {
                    "TimeSeries": [
                        {
                            "Period": [
                                {
                                    "timeInterval": {
                                        "start": "2026-08-15T22:00:00Z",
                                        "end": "2026-08-16T22:00:00Z",
                                    },
                                    "resolution": "PT1H",
                                    "Point": [
                                        {"position": "1", "out_Quantity.quantity": "0.512", "out_Quantity.quality": "A04"},
                                        {"position": "2", "out_Quantity.quantity": "0.488", "out_Quantity.quality": "A04"},
                                    ],
                                }
                            ]
                        }
                    ]
                }
            }
        ]
    }


def test_get_hourly_consumption_parses_points_with_correct_offsets():
    body = _sample_time_series_body()
    with patch("urllib.request.urlopen", return_value=_FakeResponse(json.dumps(body).encode())):
        rows = eloverblik.get_hourly_consumption("access-token", "5713131111111111", "2026-08-15", "2026-08-16")
    assert rows == [
        {"time_utc": "2026-08-15T22:00:00+00:00", "kwh": 0.512, "quality": "A04"},
        {"time_utc": "2026-08-15T23:00:00+00:00", "kwh": 0.488, "quality": "A04"},
    ]


def test_get_hourly_consumption_empty_result_returns_empty_list():
    with patch("urllib.request.urlopen", return_value=_FakeResponse(json.dumps({"result": []}).encode())):
        rows = eloverblik.get_hourly_consumption("access-token", "mp", "2026-08-15", "2026-08-16")
    assert rows == []


def test_get_hourly_consumption_skips_unparseable_points():
    body = _sample_time_series_body()
    body["result"][0]["MyEnergyData_MarketDocument"]["TimeSeries"][0]["Period"][0]["Point"].append(
        {"position": "3", "out_Quantity.quantity": "not-a-number"}
    )
    with patch("urllib.request.urlopen", return_value=_FakeResponse(json.dumps(body).encode())):
        rows = eloverblik.get_hourly_consumption("access-token", "mp", "2026-08-15", "2026-08-16")
    assert len(rows) == 2
