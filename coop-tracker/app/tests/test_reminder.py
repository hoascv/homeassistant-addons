from datetime import datetime, timedelta

import app as coopapp


def test_eggs_overdue_true_when_never_collected(conn):
    assert coopapp._eggs_overdue(datetime.now(), conn, threshold_days=2) is True


def test_eggs_overdue_false_when_recent(client, conn):
    client.post("/api/log", json={"type": "egg", "count": 1})
    assert coopapp._eggs_overdue(datetime.now(), conn, threshold_days=2) is False


def test_eggs_overdue_true_when_stale(client, conn):
    old_ts = (datetime.now() - timedelta(days=5)).isoformat()
    client.post("/api/log", json={"type": "egg", "count": 1, "ts": old_ts})
    assert coopapp._eggs_overdue(datetime.now(), conn, threshold_days=2) is True


def test_reminder_does_not_fire_when_disabled(conn, options_path):
    coopapp._reminder_tick(datetime.now(), conn)
    assert coopapp._reminder_last_checked_date is None


def test_reminder_does_not_fire_before_check_time(conn, set_options):
    set_options(
        reminder_enabled=True, notify_service="mobile_app_phone", reminder_check_time="23:59"
    )
    coopapp._reminder_tick(datetime.now().replace(hour=8, minute=0), conn)
    assert coopapp._reminder_last_checked_date is None


def test_reminder_fires_once_per_day_when_overdue(conn, set_options, fake_ha_server):
    set_options(
        reminder_enabled=True,
        notify_service="mobile_app_phone",
        reminder_check_time="00:00",
        reminder_threshold_days=1,
    )
    now = datetime.now().replace(hour=12, minute=0)

    coopapp._reminder_tick(now, conn)
    assert coopapp._reminder_last_checked_date == now.date()
    notify_calls = [c for c in fake_ha_server if c["path"].startswith("/services/notify/")]
    assert len(notify_calls) == 1
    assert "check the coop" in notify_calls[0]["body"]["message"]

    # a second tick the same day must not send a duplicate
    coopapp._reminder_tick(now, conn)
    notify_calls = [c for c in fake_ha_server if c["path"].startswith("/services/notify/")]
    assert len(notify_calls) == 1


def test_reminder_skips_notification_when_eggs_not_overdue(client, conn, set_options, fake_ha_server):
    set_options(
        reminder_enabled=True,
        notify_service="mobile_app_phone",
        reminder_check_time="00:00",
        reminder_threshold_days=2,
    )
    client.post("/api/log", json={"type": "egg", "count": 1})

    coopapp._reminder_tick(datetime.now(), conn)
    notify_calls = [c for c in fake_ha_server if c["path"].startswith("/services/notify/")]
    assert notify_calls == []


def test_reminder_guard_survives_restart(conn, set_options, fake_ha_server, monkeypatch):
    set_options(
        reminder_enabled=True,
        notify_service="mobile_app_phone",
        reminder_check_time="00:00",
        reminder_threshold_days=1,
    )
    now = datetime.now().replace(hour=12, minute=0)

    coopapp._reminder_tick(now, conn)
    notify_calls = [c for c in fake_ha_server if c["path"].startswith("/services/notify/")]
    assert len(notify_calls) == 1

    # simulate an add-on restart: the in-memory guard is gone, but the
    # persisted app_state row must still suppress a same-day duplicate
    monkeypatch.setattr(coopapp, "_reminder_last_checked_date", None)
    coopapp._reminder_tick(now, conn)
    notify_calls = [c for c in fake_ha_server if c["path"].startswith("/services/notify/")]
    assert len(notify_calls) == 1


def test_reminder_fires_again_next_day_after_restart(conn, set_options, fake_ha_server):
    set_options(
        reminder_enabled=True,
        notify_service="mobile_app_phone",
        reminder_check_time="00:00",
        reminder_threshold_days=1,
    )
    yesterday = (datetime.now() - timedelta(days=1)).date()
    coopapp._set_app_state(conn, "reminder_last_checked_date", yesterday.isoformat())

    coopapp._reminder_tick(datetime.now().replace(hour=12, minute=0), conn)
    notify_calls = [c for c in fake_ha_server if c["path"].startswith("/services/notify/")]
    assert len(notify_calls) == 1
