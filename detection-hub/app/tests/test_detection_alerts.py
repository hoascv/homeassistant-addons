"""Push notifications for configured object classes.

The rule that matters is the same one the camera watchdog lives by: a thing
that keeps being true is one notification, not sixty. A person standing in a
driveway is detected over and over, and a phone that buzzes every few seconds
is a phone that gets muted — which defeats the whole feature.
"""
import pytest

import app as hub
import detector
import hass


@pytest.fixture(autouse=True)
def _reset_alert_state():
    hub._alert_sent.clear()
    yield
    hub._alert_sent.clear()


@pytest.fixture
def sent(monkeypatch):
    """Capture notifications instead of sending them."""
    captured = []

    def fake_notify(service, message, title="Detection Hub"):
        captured.append({"service": service, "message": message, "title": title})
        return None

    monkeypatch.setattr(hass, "notify", fake_notify)
    return captured


def _alerts(labels=("person",), cooldown=300, service="mobile_app_test"):
    return {"labels": set(labels), "cooldown": cooldown, "notify_service": service}


def _det(label="person", confidence=0.92, zone=None):
    return {"label": label, "confidence": confidence, "box": [0, 0, 10, 10], "zone": zone}


# --- what gets alerted on ---


def test_a_configured_label_alerts(sent):
    assert hub._maybe_alert("drive", [_det()], alerts=_alerts()) == [_det()]
    assert len(sent) == 1
    assert sent[0]["service"] == "mobile_app_test"
    assert "person on drive" in sent[0]["message"]


def test_a_label_not_configured_is_ignored(sent):
    assert hub._maybe_alert("drive", [_det("car")], alerts=_alerts(labels=("person",))) == []
    assert sent == []


def test_nothing_is_sent_when_no_labels_are_configured(sent):
    assert hub._maybe_alert("drive", [_det()], alerts=_alerts(labels=())) == []
    assert sent == []


def test_nothing_is_sent_without_a_notify_service(sent):
    """Configured to alert but with nowhere to send it: not an error, and not
    a reason to burn the cooldown either."""
    assert hub._maybe_alert("drive", [_det()], alerts=_alerts(service="")) == []
    assert sent == []
    assert hub._alert_sent == {}


def test_the_message_names_the_zone_when_there_is_one(sent):
    hub._maybe_alert("drive", [_det(zone="Porch")], alerts=_alerts())
    assert sent[0]["message"] == "person on drive — Porch (92%)"


def test_the_message_reads_plainly_without_a_zone(sent):
    hub._maybe_alert("drive", [_det(confidence=0.837)], alerts=_alerts())
    assert sent[0]["message"] == "person on drive (84%)"


def test_label_matching_ignores_case(sent):
    hub._maybe_alert("drive", [_det("Person")], alerts=_alerts(labels=("person",)))
    assert len(sent) == 1


# --- the rate limit ---


def test_a_second_detection_inside_the_cooldown_is_silent(sent):
    hub._maybe_alert("drive", [_det()], alerts=_alerts(cooldown=300), now=1000.0)
    hub._maybe_alert("drive", [_det()], alerts=_alerts(cooldown=300), now=1200.0)
    assert len(sent) == 1


def test_the_cooldown_expires(sent):
    hub._maybe_alert("drive", [_det()], alerts=_alerts(cooldown=300), now=1000.0)
    hub._maybe_alert("drive", [_det()], alerts=_alerts(cooldown=300), now=1301.0)
    assert len(sent) == 2


def test_a_zero_cooldown_sends_every_time(sent):
    hub._maybe_alert("drive", [_det()], alerts=_alerts(cooldown=0), now=1000.0)
    hub._maybe_alert("drive", [_det()], alerts=_alerts(cooldown=0), now=1000.5)
    assert len(sent) == 2


def test_each_camera_has_its_own_cooldown(sent):
    """A quiet driveway must not silence the back door."""
    hub._maybe_alert("drive", [_det()], alerts=_alerts(), now=1000.0)
    hub._maybe_alert("back_door", [_det()], alerts=_alerts(), now=1000.0)
    assert len(sent) == 2


def test_each_label_has_its_own_cooldown(sent):
    alerts = _alerts(labels=("person", "car"))
    hub._maybe_alert("drive", [_det("person")], alerts=alerts, now=1000.0)
    hub._maybe_alert("drive", [_det("car")], alerts=alerts, now=1000.0)
    assert len(sent) == 2


def test_two_of_the_same_label_in_one_frame_send_once(sent):
    """Three people arriving together is one event to a person holding a phone."""
    hub._maybe_alert("drive", [_det(), _det(), _det()], alerts=_alerts(), now=1000.0)
    assert len(sent) == 1


# --- when Home Assistant will not take it ---


def test_a_failed_send_retries_sooner_than_the_full_cooldown(monkeypatch):
    """A transient failure must not silence the label for five minutes — but
    retrying every detection would put a blocking HTTP call on a camera thread
    for as long as Home Assistant stays down."""
    monkeypatch.setattr(hass, "notify", lambda *a, **k: "connection error")
    alerts = _alerts(cooldown=300)
    assert hub._maybe_alert("drive", [_det()], alerts=alerts, now=1000.0) == []

    # Still quiet immediately afterwards...
    assert hub._maybe_alert("drive", [_det()], alerts=alerts, now=1010.0) == []
    # ...but retried well before the 300s cooldown would have allowed.
    monkeypatch.setattr(hass, "notify", lambda *a, **k: None)
    assert hub._maybe_alert("drive", [_det()], alerts=alerts, now=1000.0 + hub.ALERT_RETRY_SECONDS + 1) != []


def test_a_failed_send_is_not_reported_as_sent(monkeypatch):
    monkeypatch.setattr(hass, "notify", lambda *a, **k: "boom")
    assert hub._maybe_alert("drive", [_det()], alerts=_alerts()) == []


# --- reading the configuration ---


def test_alert_labels_are_parsed_from_the_option(monkeypatch):
    monkeypatch.setattr(hub, "_read_options", lambda: {
        "alert_labels": "person, Car\ndog", "alert_cooldown_seconds": 60,
        "notify_service": "mobile_app_test",
    })
    config = hub.get_alert_config()
    assert config["labels"] == {"person", "car", "dog"}
    assert config["cooldown"] == 60
    assert config["notify_service"] == "mobile_app_test"


def test_alerts_are_off_by_default(monkeypatch):
    monkeypatch.setattr(hub, "_read_options", lambda: {})
    assert hub.get_alert_config()["labels"] == set()


def test_a_nonsense_cooldown_falls_back_rather_than_crashing(monkeypatch):
    monkeypatch.setattr(hub, "_read_options", lambda: {"alert_cooldown_seconds": "soon"})
    assert hub.get_alert_config()["cooldown"] == 300


# --- alerts that could never fire ---


def test_a_label_the_model_does_not_have_is_reported(monkeypatch):
    problems = hub.unalertable_labels(_alerts(labels=("dragon",)), labels=None)
    assert "dragon" in problems
    assert "detect" in problems["dragon"]


def test_a_label_excluded_by_the_labels_option_is_reported(monkeypatch):
    """Configured to alert on a class the detector has been told to filter out:
    a working-looking setup that produces nothing but silence."""
    problems = hub.unalertable_labels(_alerts(labels=("dog",)), labels=["person", "car"])
    assert "dog" in problems
    assert "labels" in problems["dog"]


def test_a_usable_label_is_not_reported():
    assert hub.unalertable_labels(_alerts(labels=("person",)), labels=["person", "car"]) == {}


def test_every_label_passes_when_the_filter_is_off():
    assert hub.unalertable_labels(_alerts(labels=("person", "dog")), labels=None) == {}


def test_alert_labels_are_real_coco_classes():
    """Guards the example in config.yaml against drifting from the model."""
    assert "person" in detector.COCO_LABELS


# --- the wiring into the recorder ---


def test_recording_a_detection_alerts(monkeypatch, sent, db_path):
    """The hook lives in record(), which both the HTTP API and the camera
    threads go through — so neither can miss it."""
    monkeypatch.setattr(hub, "get_alert_config", lambda: _alerts())
    monkeypatch.setattr(hub, "get_ha_config", lambda: {
        "sensors": False, "events": False, "prefix": "detection_hub",
        "notify_service": "mobile_app_test", "offline_seconds": 120,
    })
    import numpy as np
    import store

    conn = store.connect(db_path)
    try:
        hub.record(conn, "drive", [_det()], np.zeros((10, 10, 3), dtype="uint8"))
    finally:
        conn.close()
    assert len(sent) == 1
    assert "person on drive" in sent[0]["message"]
