"""The HTTP surface: what it refuses while locked, and what it does once open."""
from datetime import date

import store
from conftest import PASSWORD, an_entry

# Every route that touches a person's words. The point of listing them here is
# that a new one added without the @unlocked decorator fails this file rather
# than shipping a hole.
PROTECTED = [
    ("get", "/api/entry"),
    ("put", "/api/entry"),
    ("get", "/api/calendar"),
    ("get", "/api/search?q=x"),
    ("get", "/api/sections"),
    ("put", "/api/sections"),
    ("get", "/api/goals"),
    ("post", "/api/goals"),
    ("patch", "/api/goals/whatever"),
    ("delete", "/api/goals/whatever"),
    ("get", "/api/goals/whatever/timeline"),
    ("get", "/api/export"),
    ("post", "/api/password"),
]


def test_the_page_needs_no_password_but_carries_nothing(client):
    """The shell has to load before there is a session — it is what asks for
    the password. It must not carry an entry with it."""
    res = client.get("/")
    assert res.status_code == 200
    assert b"Locked" in res.data


def test_ingress_is_the_only_door(client):
    """No ingress user header means the request did not come through Home
    Assistant. There is no token to fall back on, by design."""
    client.environ_base.pop("HTTP_X_REMOTE_USER_ID")
    res = client.get("/api/state")
    assert res.status_code == 401


def test_a_restricted_user_is_turned_away(client, options):
    options(restrict_to_user_ids="someone-else")
    assert client.get("/api/state").status_code == 403


def test_every_content_route_refuses_while_locked(unlocked_client):
    unlocked_client.post("/api/lock")
    for method, path in PROTECTED:
        res = getattr(unlocked_client, method)(path, json={})
        assert res.status_code == 401, f"{method.upper()} {path} answered {res.status_code} while locked"
        assert res.get_json()["error"] == "locked"


def test_state_works_while_locked_and_says_so(client):
    body = client.get("/api/state").get_json()
    assert body["vault_exists"] is False
    assert body["unlocked"] is False
    assert "stats" in body


def test_state_while_locked_carries_no_content(unlocked_client, conn):
    unlocked_client.put("/api/entry", json={"day": "2026-08-29", **an_entry("something private")})
    unlocked_client.post("/api/lock")
    assert "private" not in unlocked_client.get("/api/state").get_data(as_text=True)


# --- The lock ---


def test_setting_a_password_unlocks_in_the_same_step(client):
    res = client.post("/api/vault", json={"password": PASSWORD})
    assert res.status_code == 200
    assert res.get_json()["token"]


def test_a_short_password_is_refused(client):
    assert client.post("/api/vault", json={"password": "abc"}).status_code == 400


def test_the_wrong_password_gets_a_401_and_no_token(client):
    client.post("/api/vault", json={"password": PASSWORD})
    res = client.post("/api/unlock", json={"password": "not it"})
    assert res.status_code == 401
    assert "token" not in res.get_json()


def test_repeated_wrong_guesses_start_getting_429(client):
    client.post("/api/vault", json={"password": PASSWORD})
    codes = [client.post("/api/unlock", json={"password": "nope"}).status_code for _ in range(12)]
    assert 429 in codes


def test_locking_ends_every_session(unlocked_client):
    """Two devices, one padlock: locking from anywhere locks the journal, not
    just the tab that asked."""
    second = unlocked_client.post("/api/unlock", json={"password": PASSWORD}).get_json()["token"]
    unlocked_client.post("/api/lock")
    res = unlocked_client.get("/api/entry", headers={"X-Journal-Session": second})
    assert res.status_code == 401


def test_a_made_up_token_is_not_a_session(client):
    client.post("/api/vault", json={"password": PASSWORD})
    res = client.get("/api/entry", headers={"X-Journal-Session": "made-up"})
    assert res.status_code == 401


def test_changing_the_password_keeps_the_session_open(unlocked_client):
    """Rotating the password should not throw the owner out of the journal
    they just proved they own."""
    unlocked_client.put("/api/entry", json={"day": "2026-08-29", **an_entry("before the change")})
    res = unlocked_client.post("/api/password", json={"old_password": PASSWORD, "new_password": "a whole new password"})
    assert res.status_code == 200
    body = unlocked_client.get("/api/entry?day=2026-08-29").get_json()
    assert body["entry"]["sections"][0]["text"] == "before the change"


def test_changing_with_the_wrong_current_password_is_refused(unlocked_client):
    res = unlocked_client.post("/api/password", json={"old_password": "wrong", "new_password": "a whole new password"})
    assert res.status_code == 401


# --- Writing and reading days ---


def test_an_entry_round_trips_over_http(unlocked_client):
    unlocked_client.put("/api/entry", json={"day": "2026-08-29", **an_entry("went to the coast", mood=5)})
    body = unlocked_client.get("/api/entry?day=2026-08-29").get_json()
    assert body["entry"]["sections"][0]["text"] == "went to the coast"
    assert body["entry"]["mood"] == 5
    assert body["day"] == "2026-08-29"


def test_a_day_with_nothing_in_it_comes_back_as_a_blank_form(unlocked_client):
    body = unlocked_client.get("/api/entry?day=2020-01-01").get_json()
    assert body["entry"] is None
    assert [section["key"] for section in body["sections"]] == [s["key"] for s in store.DEFAULT_SECTIONS]


def test_a_bad_date_is_a_400_not_a_500(unlocked_client):
    assert unlocked_client.get("/api/entry?day=yesterday").status_code == 400
    assert unlocked_client.put("/api/entry", json={"day": "yesterday"}).status_code == 400


def test_the_entry_view_carries_the_neighbours_that_have_something_in_them(unlocked_client):
    for day in ("2026-08-01", "2026-08-15", "2026-08-29"):
        unlocked_client.put("/api/entry", json={"day": day, **an_entry()})
    body = unlocked_client.get("/api/entry?day=2026-08-15").get_json()
    assert body["neighbours"] == {"previous_written": "2026-08-01", "next_written": "2026-08-29"}


def test_saving_an_empty_day_deletes_it(unlocked_client):
    unlocked_client.put("/api/entry", json={"day": "2026-08-29", **an_entry()})
    res = unlocked_client.put("/api/entry", json={"day": "2026-08-29", "sections": [], "tags": [], "goals": []})
    assert res.get_json()["deleted"] is True
    assert unlocked_client.get("/api/entry?day=2026-08-29").get_json()["entry"] is None


def test_search_over_http(unlocked_client):
    unlocked_client.put("/api/entry", json={"day": "2026-08-29", **an_entry("we drove to Skagen")})
    hits = unlocked_client.get("/api/search?q=skagen").get_json()["results"]
    assert [hit["day"] for hit in hits] == ["2026-08-29"]


def test_the_calendar_reports_the_days_in_the_window(unlocked_client):
    unlocked_client.put("/api/entry", json={"day": "2026-08-29", **an_entry(mood=3)})
    days = unlocked_client.get("/api/calendar?start=2026-08-01&end=2026-08-31").get_json()["days"]
    assert [d["day"] for d in days] == ["2026-08-29"]
    assert days[0]["mood"] == 3


# --- Sections and goals ---


def test_sections_can_be_replaced(unlocked_client):
    res = unlocked_client.put("/api/sections", json={"sections": [{"title": "One thing"}]})
    assert [section["title"] for section in res.get_json()["sections"]] == ["One thing"]
    assert [section["title"] for section in unlocked_client.get("/api/sections").get_json()["sections"]] == ["One thing"]


def test_sections_cannot_be_emptied(unlocked_client):
    assert unlocked_client.put("/api/sections", json={"sections": []}).status_code == 400


def test_a_goal_can_be_added_checked_in_against_and_closed(unlocked_client):
    goal_id = unlocked_client.post("/api/goals", json={"title": "Sail the Limfjord"}).get_json()["id"]
    unlocked_client.put("/api/entry", json={
        "day": "2026-08-29",
        **an_entry(goals=[{"id": goal_id, "note": "booked a course", "moved": True}]),
    })
    timeline = unlocked_client.get(f"/api/goals/{goal_id}/timeline").get_json()["timeline"]
    assert timeline[0]["note"] == "booked a course"

    unlocked_client.patch(f"/api/goals/{goal_id}", json={"status": "done"})
    assert unlocked_client.get("/api/goals").get_json()["goals"][0]["status"] == "done"


def test_a_goal_without_a_title_is_refused(unlocked_client):
    assert unlocked_client.post("/api/goals", json={"title": "  "}).status_code == 400


def test_the_day_view_brings_the_goals_with_it(unlocked_client):
    """One request draws the whole day: entry, sections, goals, past years."""
    unlocked_client.post("/api/goals", json={"title": "Sail the Limfjord"})
    body = unlocked_client.get("/api/entry?day=2026-08-29").get_json()
    assert body["goals"][0]["title"] == "Sail the Limfjord"
    assert "on_this_day" in body


# --- Export ---


def test_the_export_is_a_download_of_everything(unlocked_client):
    unlocked_client.put("/api/entry", json={"day": "2026-08-29", **an_entry("for the export")})
    res = unlocked_client.get("/api/export")
    assert "attachment" in res.headers["Content-Disposition"]
    assert b"for the export" in res.data


# --- What Home Assistant is told ---


def test_the_sensor_payload_is_counts_only(unlocked_client, conn, monkeypatch):
    """The background loop has no key and must never grow one. This asserts on
    what would actually be posted to Home Assistant's state machine, where it
    would be recorded and backed up in the clear."""
    unlocked_client.put("/api/entry", json={"day": date.today().isoformat(), **an_entry("something private")})
    posted = {}

    import app as journalapp

    def fake_push(entity_id, state, attributes):
        posted.update({"entity_id": entity_id, "state": state, "attributes": attributes})
        return None, None

    monkeypatch.setattr(journalapp, "push_sensor", fake_push)
    journalapp.publish_sensors(conn, date.today())

    assert posted["entity_id"] == "sensor.journal_streak"
    assert posted["state"] == 1
    assert "private" not in repr(posted)
    assert posted["attributes"]["written_today"] is True


def test_the_reminder_says_nothing_about_what_was_written(unlocked_client, conn, options, monkeypatch):
    import app as journalapp

    options(daily_reminder_enabled=True, notify_service="mobile_app_test", daily_reminder_time="00:00")
    sent = []
    monkeypatch.setattr(journalapp, "send_notification", lambda message, **kw: sent.append(message) or (True, None))

    journalapp.maybe_send_reminder(conn)
    assert sent and "Nothing written today" in sent[0]


def test_no_reminder_once_the_day_is_written(unlocked_client, conn, options, monkeypatch):
    import app as journalapp

    options(daily_reminder_enabled=True, notify_service="mobile_app_test", daily_reminder_time="00:00")
    unlocked_client.put("/api/entry", json={"day": date.today().isoformat(), **an_entry()})
    sent = []
    monkeypatch.setattr(journalapp, "send_notification", lambda message, **kw: sent.append(message) or (True, None))

    assert journalapp.maybe_send_reminder(conn) is False
    assert sent == []


def test_the_reminder_goes_out_once_a_day(unlocked_client, conn, options, monkeypatch):
    import app as journalapp

    options(daily_reminder_enabled=True, notify_service="mobile_app_test", daily_reminder_time="00:00")
    sent = []
    monkeypatch.setattr(journalapp, "send_notification", lambda message, **kw: sent.append(message) or (True, None))

    journalapp.maybe_send_reminder(conn)
    journalapp.maybe_send_reminder(conn)
    assert len(sent) == 1
