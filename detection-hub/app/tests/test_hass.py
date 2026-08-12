"""Home Assistant integration: events, sensors, and the watchdog status file.

`fake_ha` stands in for the Supervisor's Core API so these run with no Home
Assistant anywhere. What matters is the exact shape of what gets sent — an
automation template reads specific keys, and the watchdog discards a report it
cannot age.
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import hass


@pytest.fixture
def fake_ha(monkeypatch):
    """A real HTTP server standing in for Home Assistant. Yields the list of
    requests it received."""
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            calls.append(
                {
                    "path": self.path,
                    "body": json.loads(body) if body else None,
                    "auth": self.headers.get("Authorization"),
                }
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def do_GET(self):  # noqa: N802
            calls.append({"path": self.path, "body": None})
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.end_headers()
            self.wfile.write(b"\xff\xd8jpegbytes")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(hass, "HA_API_BASE", f"http://127.0.0.1:{server.server_address[1]}")
    monkeypatch.setattr(hass, "SUPERVISOR_TOKEN", "test-token")
    yield calls
    server.shutdown()
    thread.join(timeout=2)


DETECTION = {"label": "person", "confidence": 0.91, "box": [10, 20, 30, 40]}


# --- events -------------------------------------------------------------------


def test_a_detection_fires_an_event(fake_ha):
    """Events, not states. A detection happens at an instant — as a state, two
    in a row look like one, and an automation watching for a change misses the
    second entirely."""
    error = hass.fire_event("drive", DETECTION, snapshot_id=7)

    assert error is None
    assert fake_ha[0]["path"] == "/events/detection_hub_detection"


def test_the_event_payload_is_flat_and_complete(fake_ha):
    """An automation template reads trigger.event.data.label. Nesting that
    behind another key buys nothing but a longer template."""
    hass.fire_event("drive", DETECTION, snapshot_id=7)
    body = fake_ha[0]["body"]

    assert body == {
        "camera": "drive",
        "label": "person",
        "confidence": 0.91,
        "box": [10, 20, 30, 40],
        "snapshot_id": 7,
        # Null when no named area claimed it, and present either way: an
        # automation template reading trigger.event.data.zone should get a
        # falsy value rather than an error on cameras with no areas drawn.
        "zone": None,
    }


def test_the_event_names_the_area_when_there_was_one(fake_ha):
    """The reason areas have names: "a person on the Porch" is an automation,
    "a person on the drive camera" is a camera."""
    hass.fire_event("drive", {**DETECTION, "zone": "Porch"}, snapshot_id=7)
    assert fake_ha[0]["body"]["zone"] == "Porch"


def test_the_supervisor_token_is_sent(fake_ha):
    hass.fire_event("drive", DETECTION)
    assert fake_ha[0]["auth"] == "Bearer test-token"


def test_no_supervisor_token_is_an_error_not_a_crash(monkeypatch):
    """Local development, and the case every module here has to survive."""
    monkeypatch.setattr(hass, "SUPERVISOR_TOKEN", None)
    error = hass.fire_event("drive", DETECTION)
    assert "SUPERVISOR_TOKEN not set" in error


def test_an_unreachable_home_assistant_is_reported_not_raised(monkeypatch):
    """A camera thread calls this. An exception there kills the capture loop
    over something that has nothing to do with watching a camera."""
    monkeypatch.setattr(hass, "SUPERVISOR_TOKEN", "t")
    monkeypatch.setattr(hass, "HA_API_BASE", "http://127.0.0.1:1")
    error = hass.fire_event("drive", DETECTION)
    assert error and "connection error" in error


# --- sensors ------------------------------------------------------------------


def _cameras():
    return [
        {
            "id": "drive", "state": "ok", "alive": True, "detail": "streaming",
            "last_detection_at": "2026-08-09T10:00:00",
            "frames_seen": 100, "frames_detected": 3,
        },
        {
            "id": "back garden", "state": "error", "alive": False,
            "detail": "could not open stream", "last_detection_at": None,
            "frames_seen": 0, "frames_detected": 0,
        },
    ]


def test_the_daily_total_carries_state_class_measurement(fake_ha):
    """Without it the recorder keeps recent states only — the history is there
    for a day and then gone, which defeats the point of a sensor."""
    hass.publish_sensors("detection_hub", {"person": 4, "car": 1}, _cameras(), {})

    total = next(c for c in fake_ha if c["path"].endswith("_detections_today"))
    assert total["body"]["state"] == 5
    assert total["body"]["attributes"]["state_class"] == "measurement"
    assert total["body"]["attributes"]["by_label"] == {"person": 4, "car": 1}


def test_cameras_online_counts_only_the_live_ones(fake_ha):
    hass.publish_sensors("detection_hub", {}, _cameras(), {})

    sensor = next(c for c in fake_ha if c["path"].endswith("_cameras_online"))
    assert sensor["body"]["state"] == 1
    assert sensor["body"]["attributes"]["configured"] == 2


def test_each_camera_gets_a_connectivity_binary_sensor(fake_ha):
    """A camera that is up but seeing nothing and one that is down are
    different situations; an automation should not have to parse a detail
    string to tell them apart."""
    hass.publish_sensors("detection_hub", {}, _cameras(), {})
    paths = [c["path"] for c in fake_ha]

    assert "/states/binary_sensor.detection_hub_drive_online" in paths
    assert "/states/binary_sensor.detection_hub_back_garden_online" in paths

    drive = next(c for c in fake_ha if c["path"].endswith("drive_online"))
    garden = next(c for c in fake_ha if c["path"].endswith("back_garden_online"))
    assert drive["body"]["state"] == "on"
    assert garden["body"]["state"] == "off"
    assert drive["body"]["attributes"]["device_class"] == "connectivity"


def test_a_camera_name_with_spaces_becomes_a_legal_entity_id(fake_ha):
    """'back garden' cannot appear in an entity id, and silently producing an
    invalid one means the sensor never appears at all."""
    hass.publish_sensors("detection_hub", {}, _cameras(), {})
    paths = [c["path"] for c in fake_ha]
    assert any("back_garden" in p for p in paths)
    assert not any(" " in p for p in paths)


def test_a_camera_that_never_detected_says_never_not_none(fake_ha):
    hass.publish_sensors("detection_hub", {}, _cameras(), {})
    garden = next(
        c for c in fake_ha if c["path"] == "/states/sensor.detection_hub_back_garden_last_seen"
    )
    assert garden["body"]["state"] == "never"


def test_sensor_failures_are_returned_not_raised(monkeypatch):
    monkeypatch.setattr(hass, "SUPERVISOR_TOKEN", None)
    errors = hass.publish_sensors("detection_hub", {"person": 1}, _cameras(), {})
    assert errors and all("SUPERVISOR_TOKEN" in e for e in errors)


def test_the_prefix_is_honoured(fake_ha):
    hass.publish_sensors("my_hub", {}, [], {})
    assert any("sensor.my_hub_detections_today" in c["path"] for c in fake_ha)


# --- camera snapshots ---------------------------------------------------------


def test_a_camera_entity_snapshot_comes_back_as_bytes(fake_ha):
    data, error = hass.camera_snapshot("camera.front_door")
    assert error is None
    assert data.startswith(b"\xff\xd8")
    assert fake_ha[0]["path"] == "/camera_proxy/camera.front_door"


# --- the watchdog status file -------------------------------------------------


def test_the_status_file_has_the_shape_the_watchdog_reads(tmp_path):
    error = hass.write_status(
        True, "2 cameras streaming", {"frames_seen": 10}, status_dir=str(tmp_path)
    )
    assert error is None

    with open(tmp_path / "detection-hub.json") as handle:
        payload = json.load(handle)

    assert payload["slug"] == "detection-hub"
    assert payload["ok"] is True
    assert payload["detail"] == "2 cameras streaming"
    assert payload["metrics"] == {"frames_seen": 10}
    # The watchdog discards a report whose updated_at is not a number, and ages
    # everything against it — an ISO string here means the report is ignored.
    assert isinstance(payload["updated_at"], int)


def test_the_status_file_is_replaced_atomically(tmp_path):
    """The watchdog reads on its own schedule. A half-written file is a parse
    error it reports as a fault of this add-on."""
    hass.write_status(True, "first", status_dir=str(tmp_path))
    hass.write_status(True, "second", status_dir=str(tmp_path))

    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
    with open(tmp_path / "detection-hub.json") as handle:
        assert json.load(handle)["detail"] == "second"


def test_an_unwritable_share_is_reported_not_fatal(tmp_path):
    """/share can legitimately be absent in development, and status reporting
    failing is not a reason to take the add-on down."""
    blocked = tmp_path / "blocked"
    blocked.write_text("i am a file, not a directory")
    error = hass.write_status(True, "x", status_dir=str(blocked))
    assert error is not None


def test_a_failing_detector_makes_the_report_not_ok(tmp_path):
    hass.write_status(False, "detector unavailable: model not found",
                      status_dir=str(tmp_path))
    with open(tmp_path / "detection-hub.json") as handle:
        payload = json.load(handle)
    assert payload["ok"] is False
    assert "model not found" in payload["detail"]
