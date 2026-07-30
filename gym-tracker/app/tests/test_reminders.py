from datetime import datetime

import app as gymapp

NOTIFY = "mobile_app_test"


def _complete_challenge(conn, day):
    for row in conn.execute("SELECT id FROM challenge_items WHERE archived = 0"):
        conn.execute(
            "INSERT OR IGNORE INTO challenge_completions (item_id, day) VALUES (?, ?)",
            (row["id"], day),
        )
    conn.commit()


# --- Challenge reminder ----------------------------------------------------


def test_challenge_reminder_sends_when_incomplete(conn, set_options, fake_ha_server):
    set_options(notify_service=NOTIFY, challenge_reminder_enabled=True, challenge_reminder_time="18:00")
    gymapp._challenge_reminder_tick(datetime(2026, 7, 22, 18, 30), conn)
    assert len(fake_ha_server) == 1
    assert fake_ha_server[0]["path"] == f"/services/notify/{NOTIFY}"
    assert "challenge" in fake_ha_server[0]["body"]["message"].lower()
    assert gymapp._get_app_state(conn, "challenge_reminder_last_sent") == "2026-07-22"


def test_challenge_reminder_not_before_time(conn, set_options, fake_ha_server):
    set_options(notify_service=NOTIFY, challenge_reminder_enabled=True, challenge_reminder_time="18:00")
    gymapp._challenge_reminder_tick(datetime(2026, 7, 22, 17, 0), conn)
    assert fake_ha_server == []
    assert gymapp._get_app_state(conn, "challenge_reminder_last_sent") is None


def test_challenge_reminder_once_per_day(conn, set_options, fake_ha_server):
    set_options(notify_service=NOTIFY, challenge_reminder_enabled=True, challenge_reminder_time="18:00")
    gymapp._challenge_reminder_tick(datetime(2026, 7, 22, 18, 30), conn)
    gymapp._challenge_reminder_tick(datetime(2026, 7, 22, 19, 30), conn)
    assert len(fake_ha_server) == 1


def test_challenge_reminder_skips_when_already_complete(conn, set_options, fake_ha_server):
    set_options(notify_service=NOTIFY, challenge_reminder_enabled=True, challenge_reminder_time="18:00")
    _complete_challenge(conn, "2026-07-22")
    gymapp._challenge_reminder_tick(datetime(2026, 7, 22, 18, 30), conn)
    assert fake_ha_server == []
    # still marked evaluated so it won't re-check later that day
    assert gymapp._get_app_state(conn, "challenge_reminder_last_sent") == "2026-07-22"


def test_challenge_reminder_disabled(conn, set_options, fake_ha_server):
    set_options(notify_service=NOTIFY, challenge_reminder_enabled=False, challenge_reminder_time="18:00")
    gymapp._challenge_reminder_tick(datetime(2026, 7, 22, 18, 30), conn)
    assert fake_ha_server == []


def test_challenge_reminder_no_service(conn, set_options, fake_ha_server):
    set_options(notify_service="", challenge_reminder_enabled=True, challenge_reminder_time="18:00")
    gymapp._challenge_reminder_tick(datetime(2026, 7, 22, 18, 30), conn)
    assert fake_ha_server == []


def test_challenge_guard_persists_across_restart(conn, set_options, fake_ha_server):
    set_options(notify_service=NOTIFY, challenge_reminder_enabled=True, challenge_reminder_time="18:00")
    now = datetime(2026, 7, 22, 18, 30)
    gymapp._challenge_reminder_tick(now, conn)
    assert len(fake_ha_server) == 1
    # A fresh standalone connection (as after a container restart) reads the
    # guard back from app_state and must not resend.
    conn2 = gymapp._db_connect_standalone()
    try:
        gymapp._challenge_reminder_tick(now, conn2)
    finally:
        conn2.close()
    assert len(fake_ha_server) == 1


# --- Weigh-in reminder -----------------------------------------------------


def test_weighin_reminder_sends_on_weekday(conn, set_options, fake_ha_server):
    now = datetime(2026, 7, 22, 8, 30)
    weekday = gymapp.WEEKDAYS[now.weekday()]
    set_options(
        notify_service=NOTIFY,
        weighin_reminder_enabled=True,
        weighin_reminder_weekday=weekday,
        weighin_reminder_time="08:00",
    )
    gymapp._weighin_reminder_tick(now, conn)
    assert len(fake_ha_server) == 1
    assert "weigh" in fake_ha_server[0]["body"]["message"].lower()


def test_weighin_reminder_wrong_weekday(conn, set_options, fake_ha_server):
    now = datetime(2026, 7, 22, 8, 30)
    other = gymapp.WEEKDAYS[(now.weekday() + 1) % 7]
    set_options(
        notify_service=NOTIFY,
        weighin_reminder_enabled=True,
        weighin_reminder_weekday=other,
        weighin_reminder_time="08:00",
    )
    gymapp._weighin_reminder_tick(now, conn)
    assert fake_ha_server == []
    assert gymapp._get_app_state(conn, "weighin_reminder_last_sent") is None


def test_weighin_reminder_skips_if_weighed_in_today(conn, set_options, fake_ha_server):
    now = datetime(2026, 7, 22, 8, 30)
    weekday = gymapp.WEEKDAYS[now.weekday()]
    set_options(
        notify_service=NOTIFY,
        weighin_reminder_enabled=True,
        weighin_reminder_weekday=weekday,
        weighin_reminder_time="08:00",
    )
    conn.execute(
        "INSERT INTO weight_logs (ts, weight_kg) VALUES (?, ?)", ("2026-07-22T07:00:00", 100.0)
    )
    conn.commit()
    gymapp._weighin_reminder_tick(now, conn)
    assert fake_ha_server == []
    # marked evaluated so it won't nag again later the same day
    assert gymapp._get_app_state(conn, "weighin_reminder_last_sent") == "2026-07-22"


def test_weighin_reminder_once_per_day(conn, set_options, fake_ha_server):
    now = datetime(2026, 7, 22, 8, 30)
    weekday = gymapp.WEEKDAYS[now.weekday()]
    set_options(
        notify_service=NOTIFY,
        weighin_reminder_enabled=True,
        weighin_reminder_weekday=weekday,
        weighin_reminder_time="08:00",
    )
    gymapp._weighin_reminder_tick(now, conn)
    gymapp._weighin_reminder_tick(datetime(2026, 7, 22, 9, 0), conn)
    assert len(fake_ha_server) == 1


# --- Dev mode --------------------------------------------------------------


def test_background_loop_returns_without_token(db_path):
    # Autouse fixture leaves SUPERVISOR_TOKEN unset — the loop must return
    # immediately instead of spinning.
    gymapp._background_loop()


def test_reminders_endpoint_reports_config(client, set_options):
    set_options(
        notify_service=NOTIFY,
        challenge_reminder_enabled=True,
        challenge_reminder_time="19:00",
        weighin_reminder_enabled=True,
        weighin_reminder_weekday="monday",
        weighin_reminder_time="07:30",
    )
    data = client.get("/api/reminders").get_json()
    assert data["notify_service"] == NOTIFY
    assert data["challenge"]["enabled"] is True
    assert data["challenge"]["time"] == "19:00"
    assert data["weighin"]["weekday"] == "monday"
    assert data["weighin"]["time"] == "07:30"


def test_notify_test_endpoint(client, set_options, fake_ha_server):
    set_options(notify_service=NOTIFY)
    res = client.post("/api/notify-test", json={})
    assert res.status_code == 200
    assert len(fake_ha_server) == 1


def test_challenge_reminder_is_silent_on_a_rest_day(conn, set_options, fake_ha_server):
    set_options(notify_service=NOTIFY, challenge_reminder_enabled=True, challenge_reminder_time="18:00")
    # 22 July 2026 is a Wednesday; schedule the challenge for Mondays only.
    conn.execute("UPDATE challenges SET schedule_kind = 'weekdays', schedule_weekdays = '0'")
    conn.commit()

    gymapp._challenge_reminder_tick(datetime(2026, 7, 22, 18, 30), conn)
    assert fake_ha_server == []

    # ...and still fires on a day it is due.
    gymapp._set_app_state(conn, "challenge_reminder_last_sent", "")
    gymapp._challenge_reminder_tick(datetime(2026, 7, 20, 18, 30), conn)  # a Monday
    assert len(fake_ha_server) == 1
