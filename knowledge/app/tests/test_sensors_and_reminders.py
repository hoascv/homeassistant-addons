"""What the add-on says to Home Assistant: four sensors and one daily reminder.

The rest of the suite covers what happens inside the database. This covers the
only part that leaves it — and the part with the least forgiving failure mode,
because a reminder that fires twice, or a sensor whose state is a Python repr,
is wrong on somebody's phone rather than in a test.

Core is stubbed at `urlopen` rather than at `_ha_api`, so the URL, method and
payload actually built are what gets asserted.
"""
import io
import json
import urllib.error
from datetime import date, datetime, timedelta

import pytest

import app as knowledgeapp
import importer
from conftest import make_pack


@pytest.fixture(autouse=True)
def _supervisor_token(monkeypatch):
    monkeypatch.setattr(knowledgeapp, "SUPERVISOR_TOKEN", "test-token")


@pytest.fixture
def core(monkeypatch):
    """Capture every Core API call instead of making one."""
    calls = []

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            self.close()
            return False

    def fake_urlopen(req, data=None, timeout=None):
        calls.append({
            "url": req.full_url,
            "method": req.get_method(),
            "payload": json.loads(data) if data else None,
        })
        return _Response(b"{}")

    monkeypatch.setattr(knowledgeapp.urllib.request, "urlopen", fake_urlopen)
    return calls


def _topic_with_material(conn, name="Kubernetes", titles=("Pods", "Services")):
    topic_id = knowledgeapp.create_topic(conn, name)
    knowledgeapp.apply_pack(conn, topic_id, importer.normalise(make_pack(name, titles)))
    return topic_id


def _sensor(calls, entity):
    matches = [c for c in calls if c["url"].endswith(f"/states/{entity}")]
    assert matches, f"{entity} was never pushed"
    return matches[-1]["payload"]


# --- the Core call layer ------------------------------------------------------


def test_no_supervisor_token_is_a_reason_not_a_crash(monkeypatch):
    """Every dev run outside Supervisor takes this path."""
    monkeypatch.setattr(knowledgeapp, "SUPERVISOR_TOKEN", "")
    body, err = knowledgeapp._ha_api("/states/sensor.x")
    assert body is None
    assert "SUPERVISOR_TOKEN not set" in err


def test_a_sensor_push_is_a_post_with_state_and_attributes(core):
    knowledgeapp.push_sensor("sensor.x", "5", {"friendly_name": "X"})
    assert core[0]["method"] == "POST"
    assert core[0]["url"].endswith("/states/sensor.x")
    assert core[0]["payload"] == {"state": "5", "attributes": {"friendly_name": "X"}}


def test_a_core_error_is_returned_not_raised(monkeypatch):
    """This is called from the background loop; an exception here would end it."""
    def boom(req, data=None, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b"bad token"))

    monkeypatch.setattr(knowledgeapp.urllib.request, "urlopen", boom)
    body, err = knowledgeapp.push_sensor("sensor.x", "1", {})
    assert body is None
    assert err.startswith("HTTP 401")


def test_a_transport_failure_is_returned_not_raised(monkeypatch):
    def boom(req, data=None, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(knowledgeapp.urllib.request, "urlopen", boom)
    _, err = knowledgeapp.push_sensor("sensor.x", "1", {})
    assert "connection refused" in err


# --- notifications ------------------------------------------------------------


def test_no_notify_service_configured_is_a_refusal_not_a_call(core, options):
    options(notify_service="")
    sent, err = knowledgeapp.send_notification("hello")
    assert sent is False
    assert "no notify service" in err
    assert core == []


def test_a_notification_calls_the_configured_service(core, options):
    options(notify_service="mobile_app_pixel")
    sent, err = knowledgeapp.send_notification("hello", title="Knowledge")

    assert (sent, err) == (True, None)
    assert core[0]["url"].endswith("/services/notify/mobile_app_pixel")
    assert core[0]["payload"] == {"message": "hello", "title": "Knowledge"}


def test_notify_services_are_listed_from_the_notify_domain(monkeypatch, options):
    """The config UI offers these, so picking the wrong domain would present a
    list of things that are not notifiers."""
    body = json.dumps([
        {"domain": "light", "services": {"turn_on": {}}},
        {"domain": "notify", "services": {"mobile_app_pixel": {}, "persistent_notification": {}}},
    ]).encode()

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            self.close()
            return False

    monkeypatch.setattr(knowledgeapp.urllib.request, "urlopen",
                        lambda req, data=None, timeout=None: _Response(body))
    services, err = knowledgeapp.get_notify_services()
    assert err is None
    assert services == ["mobile_app_pixel", "persistent_notification"]


def test_no_notify_domain_at_all_is_an_empty_list(monkeypatch):
    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            self.close()
            return False

    monkeypatch.setattr(knowledgeapp.urllib.request, "urlopen",
                        lambda req, data=None, timeout=None: _Response(b'[{"domain": "light"}]'))
    assert knowledgeapp.get_notify_services() == ([], None)


# --- the sensors --------------------------------------------------------------


def test_the_four_sensors_are_published_every_pass(conn, options, core):
    _topic_with_material(conn)
    knowledgeapp.publish_sensors(conn, date(2026, 8, 23))
    pushed = {c["url"].rsplit("/", 1)[-1] for c in core}
    assert pushed == {
        "sensor.knowledge_today",
        "sensor.knowledge_streak",
        "sensor.knowledge_cards_due",
        "sensor.knowledge_material_left",
    }


def test_todays_sensor_carries_the_subtopic_and_its_progress(conn, options, core):
    _topic_with_material(conn)
    knowledgeapp.publish_sensors(conn, date(2026, 8, 23))
    payload = _sensor(core, "sensor.knowledge_today")

    assert payload["state"] == "Pods"
    assert payload["attributes"]["topic"] == "Kubernetes"
    assert payload["attributes"]["completed"] is False
    assert payload["attributes"]["answered"] == 0
    assert payload["attributes"]["questions"] > 0


def test_a_day_with_nothing_scheduled_says_so_rather_than_going_blank(conn, options, core):
    """An empty state would read as "unavailable" in Home Assistant, which is
    indistinguishable from the add-on being down."""
    knowledgeapp.publish_sensors(conn, date(2026, 8, 23))
    payload = _sensor(core, "sensor.knowledge_today")
    assert payload["state"] == "nothing scheduled"
    assert payload["attributes"]["reason"] == "no material left to serve"


def test_a_long_title_is_truncated_to_what_a_state_can_hold(conn, options, core):
    """Home Assistant rejects a state over 255 characters outright, and the
    sensor would simply not appear."""
    _topic_with_material(conn, titles=("T" * 400,))
    knowledgeapp.publish_sensors(conn, date(2026, 8, 23))
    assert len(_sensor(core, "sensor.knowledge_today")["state"]) == 255


def test_more_than_one_lesson_a_day_lists_the_rest(conn, options, core):
    """The state holds one title; dropping the others silently would under-report
    the day's work."""
    options(lessons_per_day=2)
    _topic_with_material(conn, titles=("Pods", "Services", "Ingress"))
    knowledgeapp.publish_sensors(conn, date(2026, 8, 23))
    attrs = _sensor(core, "sensor.knowledge_today")["attributes"]
    assert attrs["also_today"] == ["Services"]


def test_the_streak_sensor_is_a_measurement_in_days(conn, options, core):
    """state_class is what makes the recorder keep long-term statistics; without
    it the history is only recent states."""
    _topic_with_material(conn)
    knowledgeapp.publish_sensors(conn, date(2026, 8, 23))
    attrs = _sensor(core, "sensor.knowledge_streak")["attributes"]
    assert attrs["unit_of_measurement"] == "days"
    assert attrs["state_class"] == "measurement"


def test_the_cards_sensor_carries_due_and_total(conn, options, core):
    _topic_with_material(conn)
    knowledgeapp.publish_sensors(conn, date(2026, 8, 23))
    payload = _sensor(core, "sensor.knowledge_cards_due")
    assert payload["attributes"]["cards_total"] >= payload["state"]


def test_material_left_flags_the_topic_that_is_running_out(conn, options, core):
    """This is the sensor the whole add-on depends on: it never calls an LLM, so
    running out of material is silent unless something says so."""
    options(low_material_threshold=30)
    _topic_with_material(conn)
    knowledgeapp.publish_sensors(conn, date(2026, 8, 23))
    attrs = _sensor(core, "sensor.knowledge_material_left")["attributes"]
    assert attrs["running_low"] is True
    assert attrs["topic"] == "Kubernetes"


def test_material_left_names_no_topic_while_there_is_plenty(conn, options, core):
    options(low_material_threshold=0)
    _topic_with_material(conn)
    knowledgeapp.publish_sensors(conn, date(2026, 8, 23))
    attrs = _sensor(core, "sensor.knowledge_material_left")["attributes"]
    assert attrs["running_low"] is False
    assert attrs["topic"] is None


def test_material_left_is_zero_with_no_topics_at_all(conn, options, core):
    assert knowledgeapp._max_days_left(conn) == 0


# --- the daily reminder -------------------------------------------------------


def _at(hour, minute=0, day=date(2026, 8, 23)):
    return datetime(day.year, day.month, day.day, hour, minute)


def test_no_reminder_while_the_setting_is_off(conn, options, core):
    options(daily_reminder_enabled=False, notify_service="mobile_app_pixel")
    assert knowledgeapp.maybe_send_reminder(conn, _at(20)) is False
    assert core == []


def test_no_reminder_without_a_notify_service(conn, options, core):
    """Enabled but unconfigured is a common half-finished state; it must not
    become an error every tick."""
    options(daily_reminder_enabled=True, notify_service="")
    assert knowledgeapp.maybe_send_reminder(conn, _at(20)) is False
    assert core == []


def test_no_reminder_before_the_configured_time(conn, options, core):
    options(daily_reminder_enabled=True, notify_service="phone", daily_reminder_time="18:00")
    _topic_with_material(conn)
    assert knowledgeapp.maybe_send_reminder(conn, _at(17, 59)) is False
    assert core == []


def test_the_reminder_names_todays_subtopics(conn, options, core):
    options(daily_reminder_enabled=True, notify_service="phone", daily_reminder_time="18:00")
    _topic_with_material(conn)
    assert knowledgeapp.maybe_send_reminder(conn, _at(18, 0)) is True

    message = [c for c in core if "/services/notify/" in c["url"]][0]["payload"]["message"]
    assert message.startswith("Today: Pods")


def test_the_reminder_is_sent_once_a_day_not_once_a_tick(conn, options, core):
    """The background loop runs on a short timer. Without the day marker this
    would notify every tick from 18:00 until midnight."""
    options(daily_reminder_enabled=True, notify_service="phone", daily_reminder_time="18:00")
    _topic_with_material(conn)

    assert knowledgeapp.maybe_send_reminder(conn, _at(18, 0)) is True
    assert knowledgeapp.maybe_send_reminder(conn, _at(18, 1)) is False
    assert knowledgeapp.maybe_send_reminder(conn, _at(23, 59)) is False
    assert len([c for c in core if "/services/notify/" in c["url"]]) == 1


def test_the_next_day_reminds_again(conn, options, core):
    options(daily_reminder_enabled=True, notify_service="phone", daily_reminder_time="18:00")
    _topic_with_material(conn, titles=("Pods", "Services", "Ingress"))

    assert knowledgeapp.maybe_send_reminder(conn, _at(18, 0)) is True
    tomorrow = date(2026, 8, 23) + timedelta(days=1)
    assert knowledgeapp.maybe_send_reminder(conn, _at(18, 0, tomorrow)) is True


def test_the_reminder_mentions_cards_due(conn, options, core):
    options(daily_reminder_enabled=True, notify_service="phone", daily_reminder_time="06:00")
    _topic_with_material(conn)
    today = date(2026, 8, 23)
    conn.execute("UPDATE cards SET due_on = ?", (today.isoformat(),))
    conn.commit()

    knowledgeapp.maybe_send_reminder(conn, _at(7, 0))
    message = [c for c in core if "/services/notify/" in c["url"]][0]["payload"]["message"]
    assert "cards due" in message


def test_running_out_of_material_is_itself_the_reminder(conn, options, core):
    """The failure mode this add-on has to announce: with no material left there
    is no lesson, so silence would look exactly like a normal quiet day."""
    options(daily_reminder_enabled=True, notify_service="phone",
            daily_reminder_time="06:00", low_material_threshold=30)
    topic_id = knowledgeapp.create_topic(conn, "Kubernetes")
    knowledgeapp.apply_pack(
        conn, topic_id,
        importer.normalise(make_pack("Kubernetes", ("Pods",), with_material=False)),
    )

    assert knowledgeapp.maybe_send_reminder(conn, _at(7, 0)) is True
    message = [c for c in core if "/services/notify/" in c["url"]][0]["payload"]["message"]
    assert "run out of material" in message


def test_nothing_to_say_and_nothing_wrong_sends_nothing(conn, options, core):
    """No topics subscribed at all. A daily "no lesson today" would train the
    reader to ignore the channel."""
    options(daily_reminder_enabled=True, notify_service="phone", daily_reminder_time="06:00")
    assert knowledgeapp.maybe_send_reminder(conn, _at(7, 0)) is False
    assert core == []


# --- the reminder clock -------------------------------------------------------


def test_a_valid_time_is_parsed():
    assert knowledgeapp._parse_hhmm("07:30").hour == 7
    assert knowledgeapp._parse_hhmm("07:30").minute == 30


@pytest.mark.parametrize("value", ["", "nonsense", "25", None, "12:xx"])
def test_an_unparsable_time_falls_back_to_six_in_the_evening(value):
    """A typo in the config should move the reminder, not stop it — and not
    raise on every tick of the background loop either."""
    assert knowledgeapp._parse_hhmm(value).hour == 18
