"""The camera watchdog: notify on a camera going offline, and coming back.

The rule that matters is *transition only* — a camera that is down for an hour
is one notification, not sixty. And offline has to include a hung stream, not
just a dropped connection, or a frozen feed reads as healthy.
"""
from datetime import datetime, timedelta

import pytest

import app as hub
import hass


@pytest.fixture(autouse=True)
def _reset_watchdog_state():
    hub._camera_online.clear()
    yield
    hub._camera_online.clear()


def _row(camera_id="driveway", *, alive=True, state="ok", frame_age_seconds=5):
    last = (datetime.now() - timedelta(seconds=frame_age_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    return {"id": camera_id, "alive": alive, "state": state,
            "last_frame_at": last, "detail": "streaming"}


HA = {"notify_service": "mobile_app_test", "offline_seconds": 120}


# --- what counts as online ----------------------------------------------------


def test_a_streaming_camera_is_online():
    assert hub._camera_is_online(_row(), 120) is True


def test_a_dead_thread_is_offline():
    assert hub._camera_is_online(_row(alive=False), 120) is False


def test_the_error_state_is_offline():
    assert hub._camera_is_online(_row(state="error"), 120) is False


def test_a_stale_frame_is_offline_even_if_the_thread_lives():
    """The hung-stream case. The thread never threw, the state is still ok, but
    frames stopped arriving — which a connection check alone would miss."""
    assert hub._camera_is_online(_row(frame_age_seconds=300), 120) is False


def test_a_camera_that_never_delivered_a_frame_is_offline():
    row = {"id": "x", "alive": True, "state": "ok", "last_frame_at": None}
    assert hub._camera_is_online(row, 120) is False


# --- notifications, transitions only ------------------------------------------


def _fake_notify(monkeypatch):
    sent = []
    monkeypatch.setattr(hass, "notify", lambda svc, msg, title="Detection Hub": (
        sent.append((svc, msg)) or None))
    return sent


def test_going_offline_notifies_once(monkeypatch):
    sent = _fake_notify(monkeypatch)

    hub._check_camera_health([_row()], HA)                    # first sight: online
    hub._check_camera_health([_row(state="error")], HA)       # -> offline
    hub._check_camera_health([_row(state="error")], HA)       # still offline
    hub._check_camera_health([_row(state="error")], HA)       # still offline

    assert len(sent) == 1, f"expected one alert, got {sent}"
    assert "offline" in sent[0][1] and "driveway" in sent[0][1]


def test_coming_back_notifies_again(monkeypatch):
    sent = _fake_notify(monkeypatch)

    hub._check_camera_health([_row()], HA)
    hub._check_camera_health([_row(state="error")], HA)       # offline alert
    hub._check_camera_health([_row()], HA)                    # recovery alert

    assert len(sent) == 2
    assert "offline" in sent[0][1]
    assert "back online" in sent[1][1]


def test_a_camera_offline_at_first_sight_does_not_page(monkeypatch):
    """Seeded, not announced. A camera still connecting at the first check, or
    broken from boot, should not page — the alert is for something that was
    working and stopped."""
    sent = _fake_notify(monkeypatch)
    hub._check_camera_health([_row(state="error")], HA)
    assert sent == []


def test_a_camera_healthy_throughout_never_pages(monkeypatch):
    sent = _fake_notify(monkeypatch)
    for _ in range(5):
        hub._check_camera_health([_row()], HA)
    assert sent == []


def test_the_offline_message_carries_the_reason(monkeypatch):
    sent = _fake_notify(monkeypatch)
    hub._check_camera_health([_row()], HA)
    row = _row(state="error")
    row["detail"] = "could not open stream"
    hub._check_camera_health([row], HA)
    assert "could not open stream" in sent[0][1]


def test_transitions_are_returned_for_the_caller(monkeypatch):
    _fake_notify(monkeypatch)
    hub._check_camera_health([_row()], HA)
    assert hub._check_camera_health([_row(state="error")], HA) == [("driveway", False)]
    assert hub._check_camera_health([_row()], HA) == [("driveway", True)]


def test_each_camera_is_tracked_independently(monkeypatch):
    sent = _fake_notify(monkeypatch)
    both_up = [_row("driveway"), _row("garden")]
    hub._check_camera_health(both_up, HA)

    hub._check_camera_health([_row("driveway"), _row("garden", state="error")], HA)
    assert len(sent) == 1
    assert "garden" in sent[0][1]


# --- notification disabled ----------------------------------------------------


def test_without_a_notify_service_nothing_is_sent_but_state_still_tracked(monkeypatch):
    """The sensors and the log still reflect offline; only the push is off."""
    sent = _fake_notify(monkeypatch)
    ha = {"notify_service": "", "offline_seconds": 120}

    hub._check_camera_health([_row()], ha)
    transitions = hub._check_camera_health([_row(state="error")], ha)

    assert sent == []
    assert transitions == [("driveway", False)], "state is tracked regardless"


def test_a_notify_failure_does_not_raise(monkeypatch):
    """A broken notify service must not take down the background loop."""
    monkeypatch.setattr(hass, "notify", lambda *a, **k: "service not found")
    hub._check_camera_health([_row()], HA)
    # Should log and move on, not raise.
    hub._check_camera_health([_row(state="error")], HA)


# --- the notify helper --------------------------------------------------------


def test_notify_posts_to_the_service(monkeypatch):
    calls = []
    monkeypatch.setattr(hass, "api_request",
                        lambda m, p, payload=None, timeout=5: (calls.append((m, p, payload)) or (None, None)))
    error = hass.notify("mobile_app_phone", "driveway camera offline")
    assert error is None
    assert calls[0][1] == "/services/notify/mobile_app_phone"
    assert calls[0][2]["message"] == "driveway camera offline"


def test_notify_without_a_service_is_reported():
    assert "no notify service" in hass.notify("", "anything")
