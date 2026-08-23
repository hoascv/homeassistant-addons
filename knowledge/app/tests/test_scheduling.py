"""Which subtopic is served on which day, and what happens at the edges:
several topics competing for one slot, material running out, a subtopic
arriving late, a day rolling over."""
from datetime import date, timedelta

import app as knowledgeapp
from conftest import make_pack

import importer


def _load(conn, topic_name, titles, with_material=True):
    topic_id = knowledgeapp.create_topic(conn, topic_name)
    pack = importer.normalise(make_pack(topic_name, titles, with_material))
    knowledgeapp.apply_pack(conn, topic_id, pack)
    return topic_id


def test_a_subtopic_is_served_once_and_only_once(conn):
    _load(conn, "Kubernetes", ["Pods", "Services"])
    day_one = knowledgeapp.ensure_today(conn, date(2026, 8, 23))
    day_two = knowledgeapp.ensure_today(conn, date(2026, 8, 24))
    assert len(day_one) == 1
    assert len(day_two) == 1
    assert day_one[0]["subtopic_id"] != day_two[0]["subtopic_id"]


def test_ensure_today_is_idempotent(conn):
    _load(conn, "Kubernetes", ["Pods", "Services"])
    first = knowledgeapp.ensure_today(conn, date(2026, 8, 23))
    again = knowledgeapp.ensure_today(conn, date(2026, 8, 23))
    assert [r["id"] for r in first] == [r["id"] for r in again]


def test_subtopics_are_served_in_syllabus_order(conn):
    _load(conn, "Kubernetes", ["One", "Two", "Three"])
    served = []
    for offset in range(3):
        lessons = knowledgeapp.ensure_today(conn, date(2026, 8, 23) + timedelta(days=offset))
        served.append(knowledgeapp.lesson_payload(conn, lessons[-1])["subtopic"]["title"])
    assert served == ["One", "Two", "Three"]


def test_topics_take_turns_when_one_lesson_a_day(conn, options):
    options(lessons_per_day=1)
    _load(conn, "Kubernetes", ["K1", "K2"])
    _load(conn, "Rust", ["R1", "R2"])
    seen = []
    for offset in range(4):
        lessons = knowledgeapp.ensure_today(conn, date(2026, 8, 23) + timedelta(days=offset))
        seen.append(knowledgeapp.lesson_payload(conn, lessons[-1])["topic"]["name"])
    # Least-recently-served first, so the two alternate rather than one
    # topic monopolising every day until it runs dry.
    assert seen == ["Kubernetes", "Rust", "Kubernetes", "Rust"]


def test_several_lessons_a_day_prefers_different_topics(conn, options):
    options(lessons_per_day=2)
    _load(conn, "Kubernetes", ["K1", "K2"])
    _load(conn, "Rust", ["R1", "R2"])
    lessons = knowledgeapp.ensure_today(conn, date(2026, 8, 23))
    names = {knowledgeapp.lesson_payload(conn, row)["topic"]["name"] for row in lessons}
    assert len(lessons) == 2
    assert names == {"Kubernetes", "Rust"}


def test_one_topic_can_fill_the_day_when_it_is_the_only_one(conn, options):
    options(lessons_per_day=2)
    _load(conn, "Kubernetes", ["K1", "K2", "K3"])
    lessons = knowledgeapp.ensure_today(conn, date(2026, 8, 23))
    assert len(lessons) == 2


def test_nothing_is_served_when_material_has_run_out(conn):
    _load(conn, "Kubernetes", ["Only"])
    knowledgeapp.ensure_today(conn, date(2026, 8, 23))
    assert knowledgeapp.ensure_today(conn, date(2026, 8, 24)) == []


def test_a_syllabus_without_material_serves_nothing(conn):
    _load(conn, "Kubernetes", ["Pods", "Services"], with_material=False)
    assert knowledgeapp.ensure_today(conn, date(2026, 8, 23)) == []
    assert knowledgeapp.topic_progress(conn, 1)["days_of_material_left"] == 0


def test_a_subtopic_filled_in_late_takes_its_place_in_order(conn):
    """Position order, not import order: material that arrives for an earlier
    subtopic is served before later ones that arrived first."""
    topic_id = knowledgeapp.create_topic(conn, "Kubernetes")
    # A syllabus of three, but only the third has material to begin with.
    knowledgeapp.apply_pack(conn, topic_id, importer.normalise({
        "syllabus": [{"title": "One"}, {"title": "Two"}, {"title": "Three"}],
        "material": [{"title": "Three", "briefing": "third"}],
    }))
    first = knowledgeapp.ensure_today(conn, date(2026, 8, 23))
    assert knowledgeapp.lesson_payload(conn, first[0])["subtopic"]["title"] == "Three"

    knowledgeapp.apply_pack(conn, topic_id, importer.normalise({
        "material": [{"title": "One", "briefing": "first"}],
    }))
    second = knowledgeapp.ensure_today(conn, date(2026, 8, 24))
    assert knowledgeapp.lesson_payload(conn, second[0])["subtopic"]["title"] == "One"


def test_a_paused_topic_is_not_served(conn):
    _load(conn, "Kubernetes", ["K1"])
    conn.execute("UPDATE topics SET active = 0")
    conn.commit()
    assert knowledgeapp.ensure_today(conn, date(2026, 8, 23)) == []


def test_serving_a_subtopic_puts_its_cards_in_the_review_queue(conn):
    _load(conn, "Kubernetes", ["Pods", "Services"])
    # Cards exist from the import but are not due until their lesson lands.
    assert conn.execute("SELECT COUNT(*) AS n FROM cards WHERE due_on IS NOT NULL").fetchone()["n"] == 0
    knowledgeapp.ensure_today(conn, date(2026, 8, 23))
    due = knowledgeapp.due_cards(conn, date(2026, 8, 23))
    assert len(due) == 1  # only the served subtopic's card
