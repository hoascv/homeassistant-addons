"""Storing packs, grading answers, progress, and the HTTP surface."""
import json
from datetime import date, timedelta

import pytest

import app as knowledgeapp
import importer
from conftest import make_pack


def _topic_with_material(conn, name="Kubernetes", titles=("Pods", "Services")):
    topic_id = knowledgeapp.create_topic(conn, name)
    knowledgeapp.apply_pack(conn, topic_id, importer.normalise(make_pack(name, titles)))
    return topic_id


# --- Topics ---


def test_create_topic_rejects_a_duplicate_name_case_insensitively(conn):
    knowledgeapp.create_topic(conn, "Kubernetes")
    with pytest.raises(ValueError):
        knowledgeapp.create_topic(conn, "kubernetes")


def test_create_topic_rejects_an_empty_name(conn):
    with pytest.raises(ValueError):
        knowledgeapp.create_topic(conn, "   ")


def test_delete_topic_removes_everything_downstream(conn):
    topic_id = _topic_with_material(conn)
    knowledgeapp.ensure_today(conn, date(2026, 8, 23))
    knowledgeapp.delete_topic(conn, topic_id)
    for table in ("topics", "subtopics", "questions", "cards", "lessons"):
        assert conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 0


# --- Applying packs ---


def test_apply_pack_reports_what_it_stored(conn):
    topic_id = knowledgeapp.create_topic(conn, "Kubernetes")
    report = knowledgeapp.apply_pack(conn, topic_id, importer.normalise(make_pack()))
    assert report["subtopics_added"] == 2
    assert report["material_added"] == 2
    assert report["questions_added"] == 4  # one mcq + one short, per subtopic
    assert report["cards_added"] == 2


def test_a_refill_attaches_material_to_existing_syllabus_entries(conn):
    topic_id = knowledgeapp.create_topic(conn, "Kubernetes")
    knowledgeapp.apply_pack(conn, topic_id, importer.normalise({
        "syllabus": [{"title": "Pods"}, {"title": "Services"}],
    }))
    report = knowledgeapp.apply_pack(conn, topic_id, importer.normalise({
        "material": [{"title": "Services", "briefing": "About services."}],
    }))
    assert report["subtopics_added"] == 0  # matched the existing entry by title
    assert report["material_added"] == 1
    assert knowledgeapp.topic_progress(conn, topic_id)["subtopics_total"] == 2


def test_titles_match_regardless_of_case_and_spacing(conn):
    topic_id = knowledgeapp.create_topic(conn, "Kubernetes")
    knowledgeapp.apply_pack(conn, topic_id, importer.normalise({"syllabus": [{"title": "Pods"}]}))
    knowledgeapp.apply_pack(conn, topic_id, importer.normalise({
        "material": [{"title": "  pods  ", "briefing": "b"}],
    }))
    assert knowledgeapp.topic_progress(conn, topic_id)["subtopics_total"] == 1


def test_reimporting_the_same_pack_does_not_duplicate_or_overwrite(conn):
    """Re-pasting is a thing people do. It must not double the questions, and
    it must not replace material that answers already point at."""
    topic_id = knowledgeapp.create_topic(conn, "Kubernetes")
    knowledgeapp.apply_pack(conn, topic_id, importer.normalise(make_pack()))
    before = conn.execute("SELECT COUNT(*) AS n FROM questions").fetchone()["n"]
    report = knowledgeapp.apply_pack(conn, topic_id, importer.normalise(make_pack()))
    after = conn.execute("SELECT COUNT(*) AS n FROM questions").fetchone()["n"]
    assert before == after
    assert report["material_added"] == 0
    assert any("already imported" in w for w in report["warnings"])


def test_material_for_an_unknown_title_creates_a_subtopic_at_the_end(conn):
    topic_id = knowledgeapp.create_topic(conn, "Kubernetes")
    knowledgeapp.apply_pack(conn, topic_id, importer.normalise({"syllabus": [{"title": "Pods"}]}))
    knowledgeapp.apply_pack(conn, topic_id, importer.normalise({
        "material": [{"title": "Ingress", "briefing": "b"}],
    }))
    positions = [
        (r["position"], r["title"])
        for r in conn.execute("SELECT position, title FROM subtopics ORDER BY position")
    ]
    assert positions == [(1, "Pods"), (2, "Ingress")]


# --- Grading ---


def test_a_right_answer_is_marked_correct(conn):
    _topic_with_material(conn)
    lesson = knowledgeapp.ensure_today(conn, date(2026, 8, 23))[0]
    question = knowledgeapp.question_rows(conn, lesson["subtopic_id"])[0]
    result = knowledgeapp.grade_answer(conn, lesson["id"], question["id"], chosen_index=1)
    assert result["correct"] is True


def test_a_wrong_answer_still_reveals_the_explanation(conn):
    _topic_with_material(conn)
    lesson = knowledgeapp.ensure_today(conn, date(2026, 8, 23))[0]
    question = knowledgeapp.question_rows(conn, lesson["subtopic_id"])[0]
    result = knowledgeapp.grade_answer(conn, lesson["id"], question["id"], chosen_index=0)
    assert result["correct"] is False
    assert result["explanation"] == "Because it is."


def test_partly_does_not_count_as_correct(conn):
    _topic_with_material(conn)
    lesson = knowledgeapp.ensure_today(conn, date(2026, 8, 23))[0]
    short = [q for q in knowledgeapp.question_rows(conn, lesson["subtopic_id"]) if q["kind"] == "short"][0]
    assert knowledgeapp.grade_answer(conn, lesson["id"], short["id"], self_grade="partly")["correct"] is False
    assert knowledgeapp.grade_answer(conn, lesson["id"], short["id"], self_grade="got_it")["correct"] is True


def test_a_short_answer_needs_a_valid_self_grade(conn):
    _topic_with_material(conn)
    lesson = knowledgeapp.ensure_today(conn, date(2026, 8, 23))[0]
    short = [q for q in knowledgeapp.question_rows(conn, lesson["subtopic_id"]) if q["kind"] == "short"][0]
    with pytest.raises(ValueError):
        knowledgeapp.grade_answer(conn, lesson["id"], short["id"], self_grade="brilliant")


def test_a_question_from_another_lesson_is_refused(conn):
    _topic_with_material(conn)
    first = knowledgeapp.ensure_today(conn, date(2026, 8, 23))[0]
    second = knowledgeapp.ensure_today(conn, date(2026, 8, 24))[0]
    other_question = knowledgeapp.question_rows(conn, second["subtopic_id"])[0]
    with pytest.raises(ValueError):
        knowledgeapp.grade_answer(conn, first["id"], other_question["id"], chosen_index=0)


def test_answering_every_question_completes_the_lesson(conn):
    _topic_with_material(conn)
    lesson = knowledgeapp.ensure_today(conn, date(2026, 8, 23))[0]
    questions = knowledgeapp.question_rows(conn, lesson["subtopic_id"])
    for question in questions:
        if question["kind"] == "mcq":
            knowledgeapp.grade_answer(conn, lesson["id"], question["id"], chosen_index=1)
        else:
            knowledgeapp.grade_answer(conn, lesson["id"], question["id"], self_grade="got_it")
    refreshed = conn.execute("SELECT * FROM lessons WHERE id = ?", (lesson["id"],)).fetchone()
    assert refreshed["completed_at"] is not None


def test_answers_are_replaced_not_duplicated(conn):
    _topic_with_material(conn)
    lesson = knowledgeapp.ensure_today(conn, date(2026, 8, 23))[0]
    question = knowledgeapp.question_rows(conn, lesson["subtopic_id"])[0]
    knowledgeapp.grade_answer(conn, lesson["id"], question["id"], chosen_index=0)
    knowledgeapp.grade_answer(conn, lesson["id"], question["id"], chosen_index=1)
    assert conn.execute("SELECT COUNT(*) AS n FROM answers").fetchone()["n"] == 1


def test_unanswered_questions_do_not_leak_the_answer(conn):
    """The correct index is withheld from the payload entirely — hiding it in
    the page would leave it readable in the developer console."""
    _topic_with_material(conn)
    lesson = knowledgeapp.ensure_today(conn, date(2026, 8, 23))[0]
    questions = knowledgeapp.question_rows(conn, lesson["subtopic_id"], lesson_id=lesson["id"])
    assert "answer_index" not in questions[0]
    knowledgeapp.grade_answer(conn, lesson["id"], questions[0]["id"], chosen_index=0)
    after = knowledgeapp.question_rows(conn, lesson["subtopic_id"], lesson_id=lesson["id"])
    assert after[0]["answer_index"] == 1


# --- Flashcards ---


def test_reviewing_a_card_pushes_its_due_date_out(conn):
    _topic_with_material(conn)
    knowledgeapp.ensure_today(conn, date(2026, 8, 23))
    card = knowledgeapp.due_cards(conn, date(2026, 8, 23))[0]
    result = knowledgeapp.review_card(conn, card["id"], "good", today=date(2026, 8, 23))
    assert result["due_on"] == "2026-08-24"
    assert knowledgeapp.due_cards(conn, date(2026, 8, 23)) == []


def test_a_lapsed_card_stays_due_today(conn):
    _topic_with_material(conn)
    knowledgeapp.ensure_today(conn, date(2026, 8, 23))
    card = knowledgeapp.due_cards(conn, date(2026, 8, 23))[0]
    knowledgeapp.review_card(conn, card["id"], "again", today=date(2026, 8, 23))
    assert len(knowledgeapp.due_cards(conn, date(2026, 8, 23))) == 1
    assert conn.execute("SELECT lapses FROM cards WHERE id = ?", (card["id"],)).fetchone()["lapses"] == 1


def test_the_review_queue_is_capped(conn, options):
    options(cards_per_day=1)
    topic_id = knowledgeapp.create_topic(conn, "Kubernetes")
    knowledgeapp.apply_pack(conn, topic_id, importer.normalise({
        "material": [{"title": "A", "briefing": "b", "flashcards": [
            {"front": "1", "back": "1"}, {"front": "2", "back": "2"}, {"front": "3", "back": "3"},
        ]}],
    }))
    knowledgeapp.ensure_today(conn, date(2026, 8, 23))
    assert len(knowledgeapp.due_cards(conn, date(2026, 8, 23))) == 1


# --- Progress ---


def test_streak_counts_consecutive_completed_days(conn):
    _topic_with_material(conn, titles=("A", "B", "C"))
    for offset in range(3):
        day = date(2026, 8, 21) + timedelta(days=offset)
        lesson = knowledgeapp.ensure_today(conn, day)[-1]
        conn.execute("UPDATE lessons SET completed_at = ? WHERE id = ?", ("x", lesson["id"]))
    conn.commit()
    assert knowledgeapp.streak_days(conn, date(2026, 8, 23)) == 3


def test_todays_lesson_being_unfinished_does_not_break_the_streak(conn):
    _topic_with_material(conn, titles=("A", "B"))
    lesson = knowledgeapp.ensure_today(conn, date(2026, 8, 22))[0]
    conn.execute("UPDATE lessons SET completed_at = ? WHERE id = ?", ("x", lesson["id"]))
    conn.commit()
    knowledgeapp.ensure_today(conn, date(2026, 8, 23))  # today, untouched
    assert knowledgeapp.streak_days(conn, date(2026, 8, 23)) == 1


def test_a_missed_day_breaks_the_streak(conn):
    _topic_with_material(conn, titles=("A", "B"))
    lesson = knowledgeapp.ensure_today(conn, date(2026, 8, 20))[0]
    conn.execute("UPDATE lessons SET completed_at = ? WHERE id = ?", ("x", lesson["id"]))
    conn.commit()
    assert knowledgeapp.streak_days(conn, date(2026, 8, 23)) == 0


def test_material_warning_fires_below_the_threshold(conn, options):
    options(low_material_threshold=1)
    _topic_with_material(conn, titles=("A", "B"))
    assert knowledgeapp.material_warning(conn) is None  # two days left
    knowledgeapp.ensure_today(conn, date(2026, 8, 23))
    warning = knowledgeapp.material_warning(conn)
    assert warning["days_left"] == 1
    assert warning["topic"] == "Kubernetes"


# --- Prompts ---


def test_prompt_kind_follows_the_state_of_the_topic(conn, client):
    topic_id = knowledgeapp.create_topic(conn, "Kubernetes")
    assert client.get(f"/api/topics/{topic_id}/prompt").get_json()["kind"] == "new"

    knowledgeapp.apply_pack(conn, topic_id, importer.normalise({
        "syllabus": [{"title": "Pods"}, {"title": "Services"}],
        "material": [{"title": "Pods", "briefing": "b"}],
    }))
    assert client.get(f"/api/topics/{topic_id}/prompt").get_json()["kind"] == "more"

    knowledgeapp.apply_pack(conn, topic_id, importer.normalise({
        "material": [{"title": "Services", "briefing": "b"}],
    }))
    assert client.get(f"/api/topics/{topic_id}/prompt").get_json()["kind"] == "extend"


def test_a_refill_prompt_names_only_the_subtopics_still_missing(conn, client):
    topic_id = knowledgeapp.create_topic(conn, "Kubernetes")
    knowledgeapp.apply_pack(conn, topic_id, importer.normalise({
        "syllabus": [{"title": "Pods"}, {"title": "Services"}],
        "material": [{"title": "Pods", "briefing": "b"}],
    }))
    data = client.get(f"/api/topics/{topic_id}/prompt?kind=more").get_json()
    assert data["covers"] == ["Services"]
    # The whole syllabus still goes in as context, so depth is right.
    assert "1. Pods" in data["prompt"] and "2. Services" in data["prompt"]


# --- HTTP surface ---


def test_ingress_header_is_required(conn, options):
    knowledgeapp.app.config.update(TESTING=True)
    with knowledgeapp.app.test_client() as bare:
        assert bare.get("/api/summary").status_code == 401


def test_summary_shape(conn, client):
    _topic_with_material(conn)
    data = client.get("/api/summary").get_json()
    assert data["lessons"][0]["subtopic"]["title"] == "Pods"
    assert data["topics"][0]["days_of_material_left"] == 1  # two subtopics, one served
    assert data["stats"]["streak_days"] == 0


def test_import_route_stores_a_pasted_reply(conn, client):
    topic_id = knowledgeapp.create_topic(conn, "Kubernetes")
    reply = "Here you go:\n```json\n" + json.dumps(make_pack()) + "\n```"
    report = client.post("/api/import", json={"topic_id": topic_id, "text": reply}).get_json()
    assert report["material_added"] == 2
    assert report["topic"]["subtopics_total"] == 2


def test_import_route_rejects_junk_with_a_usable_message(conn, client):
    topic_id = knowledgeapp.create_topic(conn, "Kubernetes")
    res = client.post("/api/import", json={"topic_id": topic_id, "text": "sorry, I can't"})
    assert res.status_code == 400
    assert "no JSON object" in res.get_json()["error"]


def test_import_route_needs_a_topic(conn, client):
    assert client.post("/api/import", json={"text": "{}"}).status_code == 400


def test_preview_does_not_write_anything(conn, client):
    knowledgeapp.create_topic(conn, "Kubernetes")
    res = client.post("/api/import/preview", json={"text": json.dumps(make_pack())})
    assert res.get_json()["material_count"] == 2
    assert conn.execute("SELECT COUNT(*) AS n FROM subtopics").fetchone()["n"] == 0


def test_answer_route_grades_and_returns_the_updated_lesson(conn, client):
    _topic_with_material(conn)
    summary = client.get("/api/summary").get_json()
    lesson = summary["lessons"][0]
    question = [q for q in lesson["questions"] if q["kind"] == "mcq"][0]
    res = client.post("/api/answers", json={
        "lesson_id": lesson["id"], "question_id": question["id"], "chosen_index": 1,
    }).get_json()
    assert res["correct"] is True
    assert res["lesson"]["correct_count"] == 1


def test_card_review_route_rejects_an_unknown_grade(conn, client):
    _topic_with_material(conn)
    client.get("/api/summary")
    card = knowledgeapp.due_cards(conn)[0]
    assert client.post(f"/api/cards/{card['id']}/review", json={"grade": "meh"}).status_code == 400


def test_model_answer_is_only_available_from_the_reveal_route(conn, client):
    """The lesson payload must not carry it — the reveal is what makes self
    grading a comparison rather than a look-up."""
    _topic_with_material(conn)
    lesson = client.get("/api/summary").get_json()["lessons"][0]
    short = [q for q in lesson["questions"] if q["kind"] == "short"][0]
    assert "model_answer" not in short

    revealed = client.get(f"/api/questions/{short['id']}/reveal").get_json()
    assert revealed["model_answer"] == "It does the thing."


def test_reveal_route_refuses_a_quiz_question(conn, client):
    _topic_with_material(conn)
    lesson = client.get("/api/summary").get_json()["lessons"][0]
    mcq = [q for q in lesson["questions"] if q["kind"] == "mcq"][0]
    assert client.get(f"/api/questions/{mcq['id']}/reveal").status_code == 400


# --- Starter topics ---


def test_parse_starter_topics_reads_names_and_goals():
    assert knowledgeapp.parse_starter_topics("Apache Spark: debug a slow job, Apache Airflow") == [
        ("Apache Spark", "debug a slow job"),
        ("Apache Airflow", None),
    ]


def test_parse_starter_topics_tolerates_blanks_and_spacing():
    assert knowledgeapp.parse_starter_topics("  , A ,, B  ,") == [("A", None), ("B", None)]
    assert knowledgeapp.parse_starter_topics("") == []
    assert knowledgeapp.parse_starter_topics(None) == []


def test_seeding_subscribes_to_the_configured_starters(conn, options):
    options(starter_topics="Apache Spark: run it, Apache Airflow")
    created = knowledgeapp.seed_starter_topics(conn)
    assert created == ["Apache Spark", "Apache Airflow"]
    names = [r["name"] for r in conn.execute("SELECT name FROM topics ORDER BY id")]
    assert names == ["Apache Spark", "Apache Airflow"]
    assert conn.execute("SELECT goal FROM topics WHERE name = 'Apache Spark'").fetchone()["goal"] == "run it"


def test_seeding_happens_only_once(conn, options):
    """Deleting a starter topic has to be permanent — putting it back on the
    next restart would be worse than never adding it."""
    options(starter_topics="Apache Spark")
    knowledgeapp.seed_starter_topics(conn)
    knowledgeapp.delete_topic(conn, 1)
    assert knowledgeapp.seed_starter_topics(conn) == []
    assert conn.execute("SELECT COUNT(*) AS n FROM topics").fetchone()["n"] == 0


def test_seeding_skips_a_topic_that_already_exists(conn, options):
    options(starter_topics="Apache Spark")
    knowledgeapp.create_topic(conn, "apache spark")
    assert knowledgeapp.seed_starter_topics(conn) == []
    assert conn.execute("SELECT COUNT(*) AS n FROM topics").fetchone()["n"] == 1


def test_seeding_with_no_starters_configured_does_nothing(conn, options):
    options(starter_topics="")
    assert knowledgeapp.seed_starter_topics(conn) == []


def test_the_shipped_default_starter_topics_match_config_yaml():
    """The in-code fallback and config.yaml's default are two copies of one
    value; this is what stops them drifting apart unnoticed."""
    import pathlib
    import re

    config = pathlib.Path(__file__).resolve().parents[2] / "config.yaml"
    line = re.search(r'^  starter_topics: "(.*)"$', config.read_text(), re.MULTILINE)
    assert line, "starter_topics missing from config.yaml"
    assert line.group(1) == knowledgeapp.DEFAULT_STARTER_TOPICS


def test_the_shipped_default_names_spark_and_airflow():
    names = [name for name, _ in knowledgeapp.parse_starter_topics(knowledgeapp.DEFAULT_STARTER_TOPICS)]
    assert names == ["Apache Spark", "Apache Airflow"]
    assert all(goal for _, goal in knowledgeapp.parse_starter_topics(knowledgeapp.DEFAULT_STARTER_TOPICS))
