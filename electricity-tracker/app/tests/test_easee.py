import json
import urllib.error
from io import BytesIO
from unittest.mock import patch

import pytest

import easee


class _FakeResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _token_body(access="acc-1", refresh="ref-1", expires_in=3600):
    return {"accessToken": access, "refreshToken": refresh, "expiresIn": expires_in, "tokenType": "Bearer"}


def test_login_parses_tokens():
    with patch("urllib.request.urlopen", return_value=_FakeResponse(json.dumps(_token_body()).encode())):
        token = easee.login("user@example.com", "hunter2")
    assert token["access_token"] == "acc-1"
    assert token["refresh_token"] == "ref-1"
    assert token["expires_at"] > 0


def test_login_raises_without_tokens():
    with patch("urllib.request.urlopen", return_value=_FakeResponse(json.dumps({}).encode())):
        with pytest.raises(easee.EaseeError):
            easee.login("user@example.com", "hunter2")


def test_login_raises_on_http_error():
    err = urllib.error.HTTPError("url", 401, "Unauthorized", {}, BytesIO(b"bad credentials"))
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(easee.EaseeError):
            easee.login("user@example.com", "wrong")


def test_refresh_token_parses_new_tokens():
    body = _token_body(access="acc-2", refresh="ref-2")
    with patch("urllib.request.urlopen", return_value=_FakeResponse(json.dumps(body).encode())):
        token = easee.refresh_token("acc-1", "ref-1")
    assert token["access_token"] == "acc-2"
    assert token["refresh_token"] == "ref-2"


def test_get_chargers_returns_list():
    chargers = [{"id": "EH123456", "name": "Garage", "productCode": 1, "levelOfAccess": 1}]
    with patch("urllib.request.urlopen", return_value=_FakeResponse(json.dumps(chargers).encode())):
        result = easee.get_chargers("acc-1")
    assert result == chargers


def test_get_chargers_tolerates_non_list_response():
    with patch("urllib.request.urlopen", return_value=_FakeResponse(json.dumps({}).encode())):
        assert easee.get_chargers("acc-1") == []


def test_get_charger_state_maps_fields():
    body = {
        "chargerOpMode": 3,  # CHARGING
        "totalPower": 7.2,  # kW
        "sessionEnergy": 4.5,
        "lifetimeEnergy": 1234.5,
    }
    with patch("urllib.request.urlopen", return_value=_FakeResponse(json.dumps(body).encode())):
        state = easee.get_charger_state("acc-1", "EH123456")
    assert state == {
        "status": "CHARGING",
        "total_power_w": 7200.0,
        "session_energy_kwh": 4.5,
        "lifetime_energy_kwh": 1234.5,
    }


def test_get_charger_state_unknown_op_mode():
    body = {"chargerOpMode": 999, "totalPower": None, "sessionEnergy": None, "lifetimeEnergy": None}
    with patch("urllib.request.urlopen", return_value=_FakeResponse(json.dumps(body).encode())):
        state = easee.get_charger_state("acc-1", "EH123456")
    assert state["status"] == "UNKNOWN(999)"
    assert state["total_power_w"] is None
