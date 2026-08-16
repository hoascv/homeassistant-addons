import json
from types import SimpleNamespace

import saveeye


# A realistic payload shape, per Saveeye's own official Home Assistant
# integration (github.com/saveeye/SaveEye-HomeAssistant).
def _sample_payload(serial="1234567890", power=512.3, cumulative=98765.4):
    return {
        "saveeyeDeviceSerialNumber": serial,
        "meterType": "Kamstrup",
        "timestamp": "2026-08-17T10:00:00Z",
        "activeActualConsumption": {"total": power, "L1": power, "L2": 0, "L3": 0},
        "activeTotalConsumption": {"total": cumulative, "L1": cumulative, "L2": 0, "L3": 0},
        "rmsVoltage": {"L1": 231.2, "L2": 230.9, "L3": 231.5},
    }


def test_parse_telemetry_extracts_instant_power_and_cumulative_energy():
    parsed = saveeye.parse_telemetry(_sample_payload())
    assert parsed == {
        "device_serial": "1234567890",
        "device_timestamp": "2026-08-17T10:00:00Z",
        "instant_power_w": 512.3,
        "cumulative_wh": 98765.4,
    }


def test_parse_telemetry_returns_none_without_serial():
    payload = _sample_payload()
    del payload["saveeyeDeviceSerialNumber"]
    assert saveeye.parse_telemetry(payload) is None


def test_parse_telemetry_coerces_serial_to_string():
    payload = _sample_payload(serial=1234567890)  # some firmwares send it as a number
    parsed = saveeye.parse_telemetry(payload)
    assert parsed["device_serial"] == "1234567890"


def test_parse_telemetry_tolerates_missing_nested_fields():
    payload = {"saveeyeDeviceSerialNumber": "x"}
    parsed = saveeye.parse_telemetry(payload)
    assert parsed == {
        "device_serial": "x",
        "device_timestamp": None,
        "instant_power_w": None,
        "cumulative_wh": None,
    }


class _FakePahoClient:
    """Stands in for paho.mqtt.client.Client: records what SaveeyeClient does
    to it, and lets tests drive its callbacks directly rather than needing a
    real broker."""

    last_instance = None

    def __init__(self, callback_api_version):
        self.callback_api_version = callback_api_version
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None
        self.subscribed_to = None
        self.connected_to = None
        self.username = None
        self.password = None
        self.loop_started = False
        self.disconnected = False
        _FakePahoClient.last_instance = self

    def username_pw_set(self, username, password=None):
        self.username = username
        self.password = password

    def subscribe(self, topic):
        self.subscribed_to = topic

    def connect_async(self, host, port, keepalive=60):
        self.connected_to = (host, port)

    def loop_start(self):
        self.loop_started = True

    def loop_stop(self):
        self.loop_started = False

    def disconnect(self):
        self.disconnected = True


def _make_message(payload_dict):
    return SimpleNamespace(payload=json.dumps(payload_dict).encode("utf-8"), topic="saveeye/telemetry")


def test_saveeye_client_connects_and_subscribes(monkeypatch):
    monkeypatch.setattr(saveeye.mqtt, "Client", _FakePahoClient)
    client = saveeye.SaveeyeClient(
        host="core-mosquitto", port=1883, topic="saveeye/telemetry", on_telemetry=lambda p: None
    )
    client.start()
    fake = _FakePahoClient.last_instance
    assert fake.connected_to == ("core-mosquitto", 1883)
    assert fake.loop_started is True

    fake.on_connect(fake, None, None, 0)
    assert fake.subscribed_to == "saveeye/telemetry"


def test_saveeye_client_sets_credentials(monkeypatch):
    monkeypatch.setattr(saveeye.mqtt, "Client", _FakePahoClient)
    saveeye.SaveeyeClient(
        host="h", port=1883, topic="t", on_telemetry=lambda p: None, username="user", password="pw"
    )
    fake = _FakePahoClient.last_instance
    assert fake.username == "user"
    assert fake.password == "pw"


def test_saveeye_client_delivers_matching_telemetry(monkeypatch):
    monkeypatch.setattr(saveeye.mqtt, "Client", _FakePahoClient)
    received = []
    client = saveeye.SaveeyeClient(
        host="h", port=1883, topic="t", on_telemetry=received.append, device_serial="1234567890"
    )
    fake = _FakePahoClient.last_instance
    fake.on_message(fake, None, _make_message(_sample_payload(serial="1234567890")))
    assert len(received) == 1
    assert received[0]["device_serial"] == "1234567890"


def test_saveeye_client_ignores_other_device_serials(monkeypatch):
    monkeypatch.setattr(saveeye.mqtt, "Client", _FakePahoClient)
    received = []
    saveeye.SaveeyeClient(host="h", port=1883, topic="t", on_telemetry=received.append, device_serial="expected")
    fake = _FakePahoClient.last_instance
    fake.on_message(fake, None, _make_message(_sample_payload(serial="other-device")))
    assert received == []


def test_saveeye_client_ignores_malformed_json(monkeypatch):
    monkeypatch.setattr(saveeye.mqtt, "Client", _FakePahoClient)
    received = []
    saveeye.SaveeyeClient(host="h", port=1883, topic="t", on_telemetry=received.append)
    fake = _FakePahoClient.last_instance
    fake.on_message(fake, None, SimpleNamespace(payload=b"not json", topic="t"))
    assert received == []


def test_saveeye_client_reports_status_on_connect_and_disconnect(monkeypatch):
    monkeypatch.setattr(saveeye.mqtt, "Client", _FakePahoClient)
    statuses = []
    saveeye.SaveeyeClient(
        host="h", port=1883, topic="t", on_telemetry=lambda p: None,
        on_status=lambda connected, detail: statuses.append((connected, detail)),
    )
    fake = _FakePahoClient.last_instance

    fake.on_connect(fake, None, None, 0)
    fake.on_disconnect(fake, None, None, 1)

    assert statuses[0] == (True, "connected, subscribed to t")
    assert statuses[1][0] is False


def test_saveeye_client_stop(monkeypatch):
    monkeypatch.setattr(saveeye.mqtt, "Client", _FakePahoClient)
    client = saveeye.SaveeyeClient(host="h", port=1883, topic="t", on_telemetry=lambda p: None)
    client.start()
    client.stop()
    fake = _FakePahoClient.last_instance
    assert fake.loop_started is False
    assert fake.disconnected is True
