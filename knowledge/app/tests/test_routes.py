"""The HTTP surface the page and the watchdog talk to.

test_app.py covers the interesting routes — grading, importing, prompts — from
the inside. This covers the rest of the surface end to end: the status codes a
missing id produces, the query parameters that are attacker- or typo-supplied
and therefore have to be clamped rather than trusted, and `/api/stats`, which
is a contract with a different add-on rather than with this one's own page.
"""
from datetime import date, timedelta

import pytest

import app as knowledgeapp
import importer
from conftest import make_pack


def _topic_with_material(conn, name="Kubernetes", titles=("Pods", "Services")):
    topic_id = knowledgeapp.create_topic(conn, name)
    knowledgeapp.apply_pack(conn, topic_id, importer.normalise(make_pack(name, titles)))
    return topic_id


# --- topics -------------------------------------------------------------------


def test_a_topic_is_created_and_then_listed(client, conn):
    created = client.post("/api/topics", json={"name": "Rust", "goal": "Write a CLI"})
    assert created.status_code == 201
    assert created.get_json()["name"] == "Rust"

    listed = client.get("/api/topics").get_json()
    assert [t["name"] for t in listed] == ["Rust"]


def test_a_duplicate_topic_is_a_400_with_a_readable_reason(client, conn):
    client.post("/api/topics", json={"name": "Rust"})
    response = client.post("/api/topics", json={"name": "rust"})
    assert response.status_code == 400
    assert response.get_json()["error"]


def test_an_unnamed_topic_is_refused(client, conn):
    assert client.post("/api/topics", json={"name": "   "}).status_code == 400


def test_an_unknown_topic_is_a_404_on_every_method(client, conn):
    for call in (client.get, client.delete):
        assert call("/api/topics/999").status_code == 404
    assert client.patch("/api/topics/999", json={"active": False}).status_code == 404


def test_a_topic_can_be_paused_and_resumed(client, conn):
    """Pausing is how a subscription is stopped without losing its history, so
    it has to survive round-tripping rather than delete anything."""
    topic_id = _topic_with_material(conn)
    paused = client.patch(f"/api/topics/{topic_id}", json={"active": False}).get_json()
    assert paused["active"] is False
    resumed = client.patch(f"/api/topics/{topic_id}", json={"active": True}).get_json()
    assert resumed["active"] is True


def test_the_goal_and_level_can_be_edited(client, conn):
    topic_id = _topic_with_material(conn)
    body = client.patch(f"/api/topics/{topic_id}",
                        json={"goal": "  Pass the CKA  ", "level": "Beginner"}).get_json()
    assert body["goal"] == "Pass the CKA"
    assert body["level"] == "beginner"


def test_an_unrecognised_level_is_ignored_not_stored(client, conn):
    """The level steers the prompt text; a free-text value would quietly produce
    a prompt asking for material at a level that means nothing."""
    topic_id = _topic_with_material(conn)
    before = client.get(f"/api/topics/{topic_id}").get_json()["level"]
    after = client.patch(f"/api/topics/{topic_id}", json={"level": "wizard"}).get_json()
    assert after["level"] == before


def test_an_emptied_goal_becomes_null_not_an_empty_string(client, conn):
    topic_id = _topic_with_material(conn)
    client.patch(f"/api/topics/{topic_id}", json={"goal": "Something"})
    assert client.patch(f"/api/topics/{topic_id}", json={"goal": "   "}).get_json()["goal"] is None


def test_deleting_a_topic_reports_what_went(client, conn):
    topic_id = _topic_with_material(conn)
    assert client.delete(f"/api/topics/{topic_id}").get_json() == {"deleted": topic_id}
    assert client.get("/api/topics").get_json() == []


# --- lessons ------------------------------------------------------------------


def test_a_lesson_can_be_completed_before_every_question_is_answered(client, conn):
    """Reading the briefing and skipping the quiz is a legitimate day; the
    streak is about turning up."""
    _topic_with_material(conn)
    lesson = knowledgeapp.ensure_today(conn, date(2026, 8, 23))[0]
    body = client.post(f"/api/lessons/{lesson['id']}/complete").get_json()
    assert body["completed_at"] is not None


def test_completing_a_lesson_twice_keeps_the_first_timestamp(client, conn):
    """Otherwise a second click moves the completion into a later day and the
    streak silently re-dates itself."""
    _topic_with_material(conn)
    lesson = knowledgeapp.ensure_today(conn, date(2026, 8, 23))[0]
    first = client.post(f"/api/lessons/{lesson['id']}/complete").get_json()["completed_at"]
    second = client.post(f"/api/lessons/{lesson['id']}/complete").get_json()["completed_at"]
    assert first == second


def test_completing_an_unknown_lesson_is_a_404(client, conn):
    assert client.post("/api/lessons/999/complete").status_code == 404


# --- history ------------------------------------------------------------------


def test_history_returns_a_row_per_lesson_with_its_score(client, conn):
    _topic_with_material(conn)
    knowledgeapp.ensure_today(conn, knowledgeapp._today())
    rows = client.get("/api/history").get_json()
    assert rows
    assert {"day", "title", "topic", "answered", "correct"} <= set(rows[0])


def test_history_is_clamped_to_a_year(client, conn):
    """`days` comes straight off the query string; an unbounded value would scan
    the whole table for nothing."""
    assert client.get("/api/history?days=100000").status_code == 200
    assert client.get("/api/history?days=-5").status_code == 200
    assert client.get("/api/history?days=notanumber").status_code == 200


# --- flashcards ---------------------------------------------------------------


def test_due_cards_carry_the_context_needed_to_grade_them(client, conn):
    _topic_with_material(conn)
    today = knowledgeapp._today().isoformat()
    conn.execute("UPDATE cards SET due_on = ?", (today,))
    conn.commit()

    cards = client.get("/api/cards/due").get_json()
    assert cards
    assert {"id", "front", "back", "topic", "subtopic", "repetitions"} <= set(cards[0])


def test_nothing_due_is_an_empty_list_not_an_error(client, conn):
    _topic_with_material(conn)
    conn.execute("UPDATE cards SET due_on = ?",
                 ((knowledgeapp._today() + timedelta(days=30)).isoformat(),))
    conn.commit()
    assert client.get("/api/cards/due").get_json() == []


def test_reviewing_an_unknown_card_is_refused(client, conn):
    """400 rather than 404: the route funnels both an unknown id and a bad grade
    through the same ValueError. Only the page calls this, and only with an id it
    was just handed, so the conflation costs nothing — but it is worth pinning,
    because the status is the part a caller would otherwise assume."""
    response = client.post("/api/cards/999/review", json={"grade": "good"})
    assert response.status_code == 400
    assert response.get_json()["error"]


# --- the watchdog's contract --------------------------------------------------


def test_stats_reports_a_count_for_every_tracked_table(client, conn):
    """Add-on Watchdog reads this to fill its Records column. A table added to
    TRACKED_TABLES and not answered here would leave that column wrong rather
    than obviously broken."""
    _topic_with_material(conn)
    body = client.get("/api/stats").get_json()
    assert set(body["counts"]) == set(knowledgeapp.TRACKED_TABLES)
    assert body["counts"]["topics"] == 1
    assert body["app_version"]


def test_stats_reports_the_database_size(client, conn):
    body = client.get("/api/stats").get_json()
    assert body["db_bytes"] is None or body["db_bytes"] > 0


def test_stats_survives_a_database_file_that_is_not_there(client, conn, monkeypatch):
    """The watchdog calls this every minute; an unreadable path should cost the
    size field, not the whole answer."""
    monkeypatch.setattr(knowledgeapp, "DB_PATH", "/nonexistent/knowledge.db")
    body = client.get("/api/stats").get_json()
    assert body["db_bytes"] is None
    assert body["counts"]["topics"] >= 0


def test_backup_carries_every_tracked_table(client, conn):
    """This is the whole restore path; a table missing here is data that does
    not come back, and nothing would say so at the time."""
    _topic_with_material(conn)
    body = client.get("/api/backup").get_json()
    assert set(body["tables"]) == set(knowledgeapp.TRACKED_TABLES)
    assert body["tables"]["topics"][0]["name"] == "Kubernetes"
    assert body["taken_at"]


# --- notify services ----------------------------------------------------------


def test_notify_services_route_answers_without_supervisor(client, conn, monkeypatch):
    """Local dev and a misconfigured install both land here; the config UI must
    still render rather than hang on an error."""
    monkeypatch.setattr(knowledgeapp, "SUPERVISOR_TOKEN", "")
    response = client.get("/api/notify-services")
    assert response.status_code == 200
    assert response.get_json()["services"] == []
