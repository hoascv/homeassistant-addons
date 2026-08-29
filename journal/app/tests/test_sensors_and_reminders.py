"""The only things this add-on says to Home Assistant, and the one property
that matters most about them: neither can carry a word of the journal.

Everything else here is encrypted at rest under a key derived from a password
the add-on never stores. A sensor attribute or a notification body would undo
that quietly and completely — Home Assistant records every state change, graphs
it, and includes it in every backup in the clear. So these tests assert the
absence of content as hard as they assert the presence of counts.

Core is stubbed at `urlopen` so the payload actually sent is what gets checked.
"""
import io
import json
import urllib.error
from datetime import date, datetime, timedelta

import pytest

import app as journalapp
import store
from conftest import PASSWORD, an_entry


@pytest.fixture(autouse=True)
def _supervisor_token(monkeypatch):
    monkeypatch.setattr(journalapp, "SUPERVISOR_TOKEN", "test-token")


@pytest.fixture
def core(monkeypatch):
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

    monkeypatch.setattr(journalapp.urllib.request, "urlopen", fake_urlopen)
    return calls


SECRET = "the thing I would not want on a dashboard"


def _write_entry(conn, key, day, text=SECRET):
    store.save_entry(conn, key, day.isoformat(), an_entry(text=text))
    conn.commit()


# --- the privacy property -----------------------------------------------------


def test_the_streak_sensor_carries_no_entry_text(conn, key, core):
    """The whole point. Every attribute is a count or a date; if this ever
    starts carrying content, it does so into the recorder and the backups."""
    _write_entry(conn, key, date(2026, 8, 23))
    journalapp.publish_sensors(conn, date(2026, 8, 23))

    body = json.dumps(core[0]["payload"])
    assert SECRET not in body
    assert "cycling" not in body, "not even a tag"


def test_the_sensor_is_published_without_a_key_at_all(conn, key, core):
    """publish_sensors is called from the background loop, which holds no key
    and never will — so it is incapable of decrypting anything even if a future
    change asked it to."""
    _write_entry(conn, key, date(2026, 8, 23))
    journalapp.SESSIONS.close_all()
    _, err = journalapp.publish_sensors(conn, date(2026, 8, 23))
    assert err is None
    assert core[0]["payload"]["state"] == 1


def test_the_reminder_message_says_only_that_the_day_is_unwritten(conn, key, options, core):
    """It could not quote an entry if it wanted to, and the message is written
    so that it never looks like it might."""
    options(daily_reminder_enabled=True, notify_service="phone", daily_reminder_time="21:00")
    _write_entry(conn, key, date(2026, 8, 22))

    journalapp.maybe_send_reminder(conn, datetime(2026, 8, 23, 21, 30))
    message = [c for c in core if "/services/notify/" in c["url"]][0]["payload"]["message"]
    assert SECRET not in message
    assert message.startswith("Nothing written today yet.")


# --- what the sensor does carry -----------------------------------------------


def test_the_state_is_the_streak_in_days(conn, key, core):
    for offset in range(3):
        _write_entry(conn, key, date(2026, 8, 23) - timedelta(days=offset))
    journalapp.publish_sensors(conn, date(2026, 8, 23))

    payload = core[0]["payload"]
    assert payload["state"] == 3
    assert payload["attributes"]["unit_of_measurement"] == "days"


def test_the_attributes_carry_the_counts_and_dates(conn, key, core):
    _write_entry(conn, key, date(2026, 8, 22))
    _write_entry(conn, key, date(2026, 8, 23))
    journalapp.publish_sensors(conn, date(2026, 8, 23))

    attrs = core[0]["payload"]["attributes"]
    assert attrs["entries"] == 2
    assert attrs["last_entry_on"] == "2026-08-23"
    assert attrs["written_today"] is True
    assert attrs["longest_streak"] >= 2


def test_the_attributes_count_active_and_finished_goals(conn, key, core):
    store.create_goal(conn, key, "Ride 2000 km")
    conn.commit()
    journalapp.publish_sensors(conn, date(2026, 8, 23))
    attrs = core[0]["payload"]["attributes"]
    assert attrs["goals_active"] == 1
    assert attrs["goals_done"] == 0


def test_the_attributes_say_how_many_sessions_are_unlocked(conn, key, core):
    """Worth surfacing: an unlocked journal is one that auto-lock has not yet
    reclaimed, and that is a fact about the machine, not about the contents."""
    journalapp.SESSIONS.close_all()
    journalapp.publish_sensors(conn, date(2026, 8, 23))
    assert core[0]["payload"]["attributes"]["unlocked_sessions"] == 0


def test_an_empty_journal_publishes_zeroes_rather_than_nothing(conn, core):
    """A missing sensor is indistinguishable from a stopped add-on."""
    journalapp.publish_sensors(conn, date(2026, 8, 23))
    payload = core[0]["payload"]
    assert payload["state"] == 0
    assert payload["attributes"]["entries"] == 0
    assert payload["attributes"]["last_entry_on"] is None


# --- the Core call layer ------------------------------------------------------


def test_no_supervisor_token_is_a_reason_not_a_crash(conn, monkeypatch):
    monkeypatch.setattr(journalapp, "SUPERVISOR_TOKEN", "")
    _, err = journalapp.push_sensor("sensor.x", 1, {})
    assert "SUPERVISOR_TOKEN not set" in err


def test_a_core_error_is_returned_not_raised(monkeypatch):
    """This is called from the background loop; raising here would end it."""
    def boom(req, data=None, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b"bad token"))

    monkeypatch.setattr(journalapp.urllib.request, "urlopen", boom)
    _, err = journalapp.push_sensor("sensor.x", 1, {})
    assert err.startswith("HTTP 401")


def test_a_transport_failure_is_returned_not_raised(monkeypatch):
    def boom(req, data=None, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(journalapp.urllib.request, "urlopen", boom)
    _, err = journalapp.push_sensor("sensor.x", 1, {})
    assert "connection refused" in err


def test_notify_services_are_listed_from_the_notify_domain(monkeypatch):
    body = json.dumps([
        {"domain": "light", "services": {"turn_on": {}}},
        {"domain": "notify", "services": {"mobile_app_pixel": {}}},
    ]).encode()

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            self.close()
            return False

    monkeypatch.setattr(journalapp.urllib.request, "urlopen",
                        lambda req, data=None, timeout=None: _Response(body))
    assert journalapp.get_notify_services() == (["mobile_app_pixel"], None)


def test_no_notify_domain_is_an_empty_list_not_an_error(monkeypatch):
    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            self.close()
            return False

    monkeypatch.setattr(journalapp.urllib.request, "urlopen",
                        lambda req, data=None, timeout=None: _Response(b'[{"domain": "light"}]'))
    assert journalapp.get_notify_services() == ([], None)


def test_no_notify_service_configured_is_a_refusal_not_a_call(options, core):
    options(notify_service="")
    sent, err = journalapp.send_notification("hello")
    assert sent is False
    assert "no notify service" in err
    assert core == []


def test_a_notification_calls_the_configured_service(options, core):
    options(notify_service="mobile_app_pixel")
    sent, err = journalapp.send_notification("hello")
    assert (sent, err) == (True, None)
    assert core[0]["url"].endswith("/services/notify/mobile_app_pixel")
    assert core[0]["payload"]["title"] == "Journal"


# --- the daily reminder -------------------------------------------------------


def test_no_reminder_while_the_setting_is_off(conn, options, core):
    options(daily_reminder_enabled=False, notify_service="phone")
    assert journalapp.maybe_send_reminder(conn, datetime(2026, 8, 23, 22, 0)) is False
    assert core == []


def test_no_reminder_without_a_notify_service(conn, options, core):
    options(daily_reminder_enabled=True, notify_service="")
    assert journalapp.maybe_send_reminder(conn, datetime(2026, 8, 23, 22, 0)) is False
    assert core == []


def test_no_reminder_before_the_configured_time(conn, options, core):
    options(daily_reminder_enabled=True, notify_service="phone", daily_reminder_time="21:00")
    assert journalapp.maybe_send_reminder(conn, datetime(2026, 8, 23, 20, 59)) is False
    assert core == []


def test_no_reminder_once_the_day_is_written(conn, key, options, core):
    """The nudge exists to catch an unwritten day; sending it anyway would make
    it noise within a week."""
    options(daily_reminder_enabled=True, notify_service="phone", daily_reminder_time="21:00")
    _write_entry(conn, key, date(2026, 8, 23))
    assert journalapp.maybe_send_reminder(conn, datetime(2026, 8, 23, 21, 30)) is False
    assert core == []


def test_the_reminder_mentions_a_streak_worth_keeping(conn, key, options, core):
    options(daily_reminder_enabled=True, notify_service="phone", daily_reminder_time="21:00")
    for offset in (1, 2, 3):
        _write_entry(conn, key, date(2026, 8, 23) - timedelta(days=offset))

    assert journalapp.maybe_send_reminder(conn, datetime(2026, 8, 23, 21, 30)) is True
    message = [c for c in core if "/services/notify/" in c["url"]][0]["payload"]["message"]
    assert "3-day streak" in message


def test_no_streak_yet_leaves_the_message_bare(conn, options, core):
    """"You are on a 0-day streak" would be a strange thing to be told."""
    options(daily_reminder_enabled=True, notify_service="phone", daily_reminder_time="21:00")
    assert journalapp.maybe_send_reminder(conn, datetime(2026, 8, 23, 21, 30)) is True
    message = [c for c in core if "/services/notify/" in c["url"]][0]["payload"]["message"]
    assert message == "Nothing written today yet."


def test_the_reminder_is_sent_once_a_day_not_once_a_tick(conn, options, core):
    """The background loop ticks continuously from the reminder time to
    midnight; without the day marker this is a notification a minute."""
    options(daily_reminder_enabled=True, notify_service="phone", daily_reminder_time="21:00")

    assert journalapp.maybe_send_reminder(conn, datetime(2026, 8, 23, 21, 0)) is True
    assert journalapp.maybe_send_reminder(conn, datetime(2026, 8, 23, 21, 1)) is False
    assert journalapp.maybe_send_reminder(conn, datetime(2026, 8, 23, 23, 59)) is False
    assert len([c for c in core if "/services/notify/" in c["url"]]) == 1


def test_the_next_day_reminds_again(conn, options, core):
    options(daily_reminder_enabled=True, notify_service="phone", daily_reminder_time="21:00")
    assert journalapp.maybe_send_reminder(conn, datetime(2026, 8, 23, 21, 0)) is True
    assert journalapp.maybe_send_reminder(conn, datetime(2026, 8, 24, 21, 0)) is True


# --- the reminder clock -------------------------------------------------------


def test_a_valid_time_is_parsed():
    parsed = journalapp._parse_hhmm("07:05")
    assert (parsed.hour, parsed.minute) == (7, 5)


@pytest.mark.parametrize("value", ["", "nonsense", "25", None, "12:xx"])
def test_an_unparsable_time_falls_back_to_nine_in_the_evening(value):
    """A typo in the config should move the reminder, not raise on every tick of
    the background loop."""
    assert journalapp._parse_hhmm(value).hour == 21
