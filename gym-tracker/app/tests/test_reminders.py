from datetime import datetime, timedelta

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


def test_weighin_reminder_can_run_daily(conn, set_options, fake_ha_server):
    set_options(notify_service=NOTIFY, weighin_reminder_enabled=True,
                weighin_reminder_weekday="daily", weighin_reminder_time="08:00")

    # A Wednesday and a Thursday: both fire, where a weekday setting wouldn't.
    gymapp._weighin_reminder_tick(datetime(2026, 7, 22, 8, 30), conn)
    assert len(fake_ha_server) == 1
    assert "weekly" not in fake_ha_server[0]["body"]["message"].lower()

    gymapp._weighin_reminder_tick(datetime(2026, 7, 23, 8, 30), conn)
    assert len(fake_ha_server) == 2


def test_a_daily_weighin_reminder_still_skips_a_day_already_logged(conn, set_options, fake_ha_server):
    set_options(notify_service=NOTIFY, weighin_reminder_enabled=True,
                weighin_reminder_weekday="daily", weighin_reminder_time="08:00")
    conn.execute("INSERT INTO weight_logs (ts, weight_kg, ts_exact) VALUES (?, 100.0, 1)",
                 ("2026-07-22T07:15:00",))
    conn.commit()

    gymapp._weighin_reminder_tick(datetime(2026, 7, 22, 8, 30), conn)
    assert fake_ha_server == []
    # ...and still only asks once on a day it does fire.
    gymapp._weighin_reminder_tick(datetime(2026, 7, 23, 8, 30), conn)
    gymapp._weighin_reminder_tick(datetime(2026, 7, 23, 9, 30), conn)
    assert len(fake_ha_server) == 1


def test_a_weekday_weighin_reminder_is_unaffected(conn, set_options, fake_ha_server):
    set_options(notify_service=NOTIFY, weighin_reminder_enabled=True,
                weighin_reminder_weekday="sunday", weighin_reminder_time="08:00")
    gymapp._weighin_reminder_tick(datetime(2026, 7, 22, 8, 30), conn)  # a Wednesday
    assert fake_ha_server == []
    gymapp._weighin_reminder_tick(datetime(2026, 7, 26, 8, 30), conn)  # a Sunday
    assert len(fake_ha_server) == 1
    assert "weekly" in fake_ha_server[0]["body"]["message"].lower()


# --- Daily stoic quote -----------------------------------------------------


def test_quote_sends_at_configured_time(conn, set_options, fake_ha_server):
    set_options(notify_service=NOTIFY, stoic_quote_enabled=True, stoic_quote_time="07:00")
    gymapp._stoic_quote_tick(datetime(2026, 7, 22, 7, 0), conn)
    assert len(fake_ha_server) == 1
    assert fake_ha_server[0]["path"] == f"/services/notify/{NOTIFY}"
    text, author = gymapp.STOIC_QUOTES[0]
    assert text in fake_ha_server[0]["body"]["message"]
    assert author in fake_ha_server[0]["body"]["message"]
    assert gymapp._get_app_state(conn, "stoic_quote_last_sent") == "2026-07-22"


def test_quote_not_before_time(conn, set_options, fake_ha_server):
    set_options(notify_service=NOTIFY, stoic_quote_enabled=True, stoic_quote_time="07:00")
    gymapp._stoic_quote_tick(datetime(2026, 7, 22, 6, 59), conn)
    assert fake_ha_server == []
    assert gymapp._get_app_state(conn, "stoic_quote_last_sent") is None


def test_quote_once_per_day(conn, set_options, fake_ha_server):
    set_options(notify_service=NOTIFY, stoic_quote_enabled=True, stoic_quote_time="07:00")
    gymapp._stoic_quote_tick(datetime(2026, 7, 22, 7, 0), conn)
    gymapp._stoic_quote_tick(datetime(2026, 7, 22, 9, 30), conn)
    assert len(fake_ha_server) == 1


def test_quote_is_new_each_day_and_wraps(conn, set_options, fake_ha_server):
    set_options(notify_service=NOTIFY, stoic_quote_enabled=True, stoic_quote_time="07:00")
    total = len(gymapp.STOIC_QUOTES)
    for i in range(total + 1):
        gymapp._set_app_state(conn, "stoic_quote_last_sent", "")
        gymapp._stoic_quote_tick(datetime(2026, 7, 22, 7, 0) + timedelta(days=i), conn)
    sent = [m["body"]["message"] for m in fake_ha_server]
    # every quote used once before any repeats, then it comes round again
    assert len(set(sent[:total])) == total
    assert sent[total] == sent[0]


def test_quote_disabled(conn, set_options, fake_ha_server):
    set_options(notify_service=NOTIFY, stoic_quote_enabled=False, stoic_quote_time="07:00")
    gymapp._stoic_quote_tick(datetime(2026, 7, 22, 7, 0), conn)
    assert fake_ha_server == []


def test_quote_no_service(conn, set_options, fake_ha_server):
    set_options(notify_service="", stoic_quote_enabled=True, stoic_quote_time="07:00")
    gymapp._stoic_quote_tick(datetime(2026, 7, 22, 7, 0), conn)
    assert fake_ha_server == []


def test_quote_defaults_on_at_7am(conn, set_options, fake_ha_server):
    set_options(notify_service=NOTIFY)
    cfg = gymapp.get_reminders_config()
    assert cfg["quote_enabled"] is True
    assert cfg["quote_time"] == "07:00"


# --- the quote list itself ----------------------------------------------------
#
# It is shared by the celebration toast and the daily notification, so its
# constraints are the tighter of the two: readable at a glance on a lock screen.


def test_no_quote_is_repeated():
    """A list with a duplicate in it shows that one twice as often, and on a
    daily notification somebody notices within the fortnight."""
    texts = [text.strip().lower() for text, _ in gymapp.STOIC_QUOTES]
    assert len(texts) == len(set(texts))


def test_every_quote_is_short_enough_to_glance_at():
    """It has to survive a toast and a phone notification. Past roughly a
    hundred characters the notification truncates and the toast wraps to three
    lines, which is longer than the toast is on screen for."""
    for text, _ in gymapp.STOIC_QUOTES:
        assert len(text) <= 100, f"too long ({len(text)}): {text}"


def test_every_quote_is_attributed():
    for text, author in gymapp.STOIC_QUOTES:
        assert text.strip() and author.strip(), f"unattributed: {text!r}"


def test_the_list_is_long_enough_that_it_does_not_feel_like_a_loop():
    """Sent daily. A dozen quotes is a fortnight before it repeats, which is
    short enough that it reads as a gimmick rather than as a habit."""
    assert len(gymapp.STOIC_QUOTES) >= 30


def test_it_is_not_one_author_wearing_three_names():
    """A mix, so the daily quote does not become a Marcus Aurelius calendar."""
    authors = {author for _, author in gymapp.STOIC_QUOTES}
    assert len(authors) >= 4
    most = max(sum(1 for _, a in gymapp.STOIC_QUOTES if a == author) for author in authors)
    assert most <= len(gymapp.STOIC_QUOTES) * 0.5


def test_the_famous_misattributions_are_absent():
    """Each of these is circulated as Stoic and is not. A wrong attribution in
    a daily notification is a small lie repeated every day, and the fix after
    somebody notices is worse than the care beforehand."""
    joined = " ".join(text.lower() for text, _ in gymapp.STOIC_QUOTES)
    for fake in ("preparation meets opportunity",   # not Seneca
                 "gem cannot be polished",          # not Seneca, not Confucius
                 "the best revenge is massive"):    # Sinatra, via the internet
        assert fake not in joined, f"misattributed quote present: {fake}"
