"""Knowledge — a topic a day, with no internet.

Subscribe to topics; each day the add-on serves the next subtopic from that
topic's syllabus with a briefing, a quiz, short-answer questions, a practical
task, and flashcards on a spaced-repetition schedule.

The unusual part is where the material comes from. This add-on never calls a
language model: it has no API key, no provider, and makes no outbound request
except to Home Assistant's own Supervisor. Instead it *writes a prompt*, you
run that prompt against whatever assistant you have wherever you have signal,
and you paste the reply back. One paste carries a whole syllabus plus a
fortnight of material, so the thing keeps working on a boat, on a plane, or
behind a firewall that has never heard of an LLM vendor — see prompts.py for
the outbound half and importer.py for the inbound half.
"""
import hmac
import html
import json
import os
import signal
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, time as dtime, timedelta

from flask import Flask, Response, g, jsonify, render_template, request

import importer
import markdown
import prompts
import srs

APP_VERSION = "1.1.0"  # keep in sync with the "version" field in config.yaml

DB_PATH = os.environ.get("KNOWLEDGE_DB_PATH", "/data/knowledge.db")
OPTIONS_PATH = os.environ.get("KNOWLEDGE_OPTIONS_PATH", "/data/options.json")

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
CORE_API = "http://supervisor/core/api"

# The loop only has to notice a clock reaching a reminder time and roll the
# day over at midnight; a minute of granularity is plenty for both.
BACKGROUND_TICK_SECONDS = 60

app = Flask(__name__)


def _log(msg):
    print(f"[Knowledge] {datetime.now().isoformat()} {msg}", flush=True)


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


def _today():
    """Local calendar date. Naive on purpose: Supervisor gives the container
    Home Assistant's own timezone, and "which day is it" here means the day
    the person in front of it is having, not UTC's."""
    return date.today()


def _read_options():
    try:
        with open(OPTIONS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# --- Access control (per-user allowlist over the ingress user-ID header) ---
# Same policy as the other add-ons in this repository: ingress passes the
# authenticated Home Assistant user's ID, which is the only thing that can
# narrow access, and a bearer token covers the direct port for anything
# outside Home Assistant.
INGRESS_USER_ID_HEADER = "X-Remote-User-ID"


def get_allowed_user_ids():
    raw = _read_options().get("restrict_to_user_ids", "") or ""
    return {uid.strip() for uid in raw.replace("\n", ",").replace(" ", ",").split(",") if uid.strip()}


def get_api_token():
    return (_read_options().get("api_token") or "").strip()


def _request_has_api_token():
    token = get_api_token()
    if not token:
        return False
    header = request.headers.get("Authorization", "")
    presented = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not presented:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), token.encode("utf-8"))


@app.before_request
def _enforce_access():
    if _request_has_api_token():
        return None

    user_id = request.headers.get(INGRESS_USER_ID_HEADER)
    if not user_id:
        return Response(
            json.dumps(
                {
                    "error": "unauthorized",
                    "detail": (
                        "This port requires a bearer token. Set api_token in the add-on's "
                        "Configuration tab and send it as 'Authorization: Bearer <token>'. "
                        "Requests through Home Assistant's ingress do not need one."
                    ),
                }
            ),
            status=401,
            mimetype="application/json",
            headers={"WWW-Authenticate": "Bearer"},
        )

    allowed = get_allowed_user_ids()
    if not allowed or user_id in allowed:
        return None
    return Response(_access_denied_html(user_id), status=403, mimetype="text/html")


def _access_denied_html(user_id):
    shown = html.escape(user_id) if user_id else "(unknown — not opened through Home Assistant)"
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Knowledge — access restricted</title>"
        "<style>body{font-family:system-ui,sans-serif;background:#111;color:#eee;"
        "display:flex;min-height:100vh;margin:0;align-items:center;justify-content:center;padding:1.5rem}"
        ".card{max-width:26rem;text-align:center;line-height:1.5}</style></head><body><div class='card'>"
        "<h1>Not your study plan</h1>"
        "<p>This add-on is restricted to specific Home Assistant users.</p>"
        f"<p style='opacity:.6;font-size:.85rem'>Your user id: <code>{shown}</code></p>"
        "</div></body></html>"
    )


# --- Database ---


def _connect(path=DB_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _db_connect_standalone():
    """A connection for the background thread — Flask's `g` belongs to a
    request, and the loop has none."""
    return _connect()


def get_db():
    if "db" not in g:
        g.db = _connect()
    return g.db


@app.teardown_appcontext
def _close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS topics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        goal TEXT,
        level TEXT NOT NULL DEFAULT 'intermediate',
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        last_served_on TEXT
    )
    """,
    # A subtopic is one day's worth. `imported_at` is the marker that its
    # material has actually landed: a syllabus arrives complete but its
    # material arrives a pack at a time, and only a subtopic with material
    # can be served.
    """
    CREATE TABLE IF NOT EXISTS subtopics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic_id INTEGER NOT NULL,
        position INTEGER NOT NULL,
        title TEXT NOT NULL,
        summary TEXT,
        briefing TEXT,
        practical_task TEXT,
        imported_at TEXT,
        UNIQUE (topic_id, position)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subtopic_id INTEGER NOT NULL,
        position INTEGER NOT NULL,
        kind TEXT NOT NULL,              -- 'mcq' | 'short'
        question TEXT NOT NULL,
        choices_json TEXT,               -- mcq only
        answer_index INTEGER,            -- mcq only
        model_answer TEXT,               -- short only
        explanation TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subtopic_id INTEGER NOT NULL,
        front TEXT NOT NULL,
        back TEXT NOT NULL,
        due_on TEXT,                     -- NULL until the subtopic is served
        interval_days INTEGER NOT NULL DEFAULT 0,
        ease REAL NOT NULL DEFAULT 2.5,
        repetitions INTEGER NOT NULL DEFAULT 0,
        lapses INTEGER NOT NULL DEFAULT 0,
        last_reviewed_at TEXT
    )
    """,
    # One subtopic is served on exactly one day, ever — that uniqueness is
    # what "every day is a new subtopic" means, enforced rather than assumed.
    """
    CREATE TABLE IF NOT EXISTS lessons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day TEXT NOT NULL,
        topic_id INTEGER NOT NULL,
        subtopic_id INTEGER NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        completed_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS answers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lesson_id INTEGER NOT NULL,
        question_id INTEGER NOT NULL,
        answered_at TEXT NOT NULL,
        chosen_index INTEGER,
        self_grade TEXT,                 -- short only: got_it | partly | missed
        response_text TEXT,              -- short only: what you actually wrote
        correct INTEGER,
        UNIQUE (lesson_id, question_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        card_id INTEGER NOT NULL,
        reviewed_at TEXT NOT NULL,
        grade TEXT NOT NULL,
        interval_days INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS imports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        imported_at TEXT NOT NULL,
        topic_id INTEGER,
        subtopics_added INTEGER NOT NULL DEFAULT 0,
        material_added INTEGER NOT NULL DEFAULT 0,
        questions_added INTEGER NOT NULL DEFAULT 0,
        cards_added INTEGER NOT NULL DEFAULT 0,
        warnings_json TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_state (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_subtopics_topic ON subtopics (topic_id, position)",
    "CREATE INDEX IF NOT EXISTS idx_questions_subtopic ON questions (subtopic_id, position)",
    "CREATE INDEX IF NOT EXISTS idx_cards_due ON cards (due_on)",
    "CREATE INDEX IF NOT EXISTS idx_lessons_day ON lessons (day)",
)


def init_db(path=DB_PATH):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = _connect(path)
    for statement in SCHEMA:
        conn.execute(statement)
    conn.commit()
    conn.close()


def _get_app_state(conn, key, default=None):
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def _set_app_state(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)", (key, value))


# --- Options ---

LEVELS = ("beginner", "intermediate", "advanced")

# Kept identical to config.yaml's default. Supervisor writes options.json from
# that file, so this fallback only applies outside Home Assistant — but a
# fallback that quietly differs from the shipped default is a difference nobody
# would think to look for.
DEFAULT_STARTER_TOPICS = (
    "Apache Spark: read and debug a slow job on my own cluster, "
    "Apache Airflow: write DAGs I trust to run unattended"
)


def get_config(options=None):
    options = _read_options() if options is None else options

    def _int(key, default, low, high):
        try:
            return max(low, min(high, int(options.get(key, default))))
        except (TypeError, ValueError):
            return default

    level = (options.get("default_level") or "intermediate").strip().lower()
    return {
        "lessons_per_day": _int("lessons_per_day", 1, 1, 10),
        "syllabus_size": _int("syllabus_size", 24, 4, 100),
        "material_days": _int("material_days", 14, 1, 60),
        "quiz_questions": _int("quiz_questions", 6, 1, 20),
        "short_questions": _int("short_answer_questions", 3, 0, 10),
        "flashcards": _int("flashcards_per_subtopic", 8, 0, 30),
        "cards_per_day": _int("cards_per_day", 20, 1, 200),
        "low_material_threshold": _int("low_material_threshold", 3, 0, 30),
        "default_level": level if level in LEVELS else "intermediate",
        "starter_topics": options.get("starter_topics", DEFAULT_STARTER_TOPICS),
        "notify_service": (options.get("notify_service") or "").strip(),
        "reminder_enabled": bool(options.get("daily_reminder_enabled", False)),
        "reminder_time": (options.get("daily_reminder_time") or "18:00").strip(),
    }


def _counts(cfg):
    return {"quiz": cfg["quiz_questions"], "short": cfg["short_questions"], "cards": cfg["flashcards"]}


# --- Topics and syllabus ---


def create_topic(conn, name, goal=None, level=None):
    name = (name or "").strip()
    if not name:
        raise ValueError("a topic needs a name")
    existing = conn.execute("SELECT id FROM topics WHERE lower(name) = lower(?)", (name,)).fetchone()
    if existing:
        raise ValueError(f"already subscribed to {name!r}")
    level = (level or get_config()["default_level"]).strip().lower()
    cur = conn.execute(
        "INSERT INTO topics (name, goal, level, active, created_at) VALUES (?, ?, ?, 1, ?)",
        (name, (goal or "").strip() or None, level if level in LEVELS else "intermediate", _now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def parse_starter_topics(raw):
    """`"Apache Spark: debug a slow job, Apache Airflow"` -> [(name, goal), ...].

    Comma separates topics, an optional colon separates a topic from what you
    want out of it — the same free-text goal the Subscribe form takes, since it
    is what most visibly steers the syllabus you get back.
    """
    out = []
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, _, goal = chunk.partition(":")
        name, goal = name.strip(), goal.strip()
        if name:
            out.append((name, goal or None))
    return out


def seed_starter_topics(conn, options=None):
    """Subscribe to the configured starter topics, once, on a fresh install.

    An add-on that opens on an empty screen has to explain itself before it can
    be used; one that opens with two topics already listed has a "Get a prompt"
    button to press. Guarded by a flag rather than by the topic table being
    empty, so deleting them is permanent — re-adding what someone deliberately
    removed would be the more annoying failure.
    """
    if _get_app_state(conn, "starter_topics_seeded"):
        return []
    cfg = get_config(options)
    created = []
    for name, goal in parse_starter_topics(cfg["starter_topics"]):
        try:
            create_topic(conn, name, goal, cfg["default_level"])
            created.append(name)
        except ValueError:
            pass  # already subscribed — nothing to do, and not an error
    _set_app_state(conn, "starter_topics_seeded", _now_iso())
    conn.commit()
    if created:
        _log(f"subscribed to starter topics: {', '.join(created)}")
    return created


def delete_topic(conn, topic_id):
    """Remove a topic and everything downstream of it.

    Done by hand rather than with ON DELETE CASCADE: SQLite enforces foreign
    keys only when `PRAGMA foreign_keys` is on per connection, so a cascade
    declared in the schema would be silently inert on any connection that
    forgot it — the worst kind of correct-looking code.
    """
    subtopic_ids = [r["id"] for r in conn.execute("SELECT id FROM subtopics WHERE topic_id = ?", (topic_id,))]
    lesson_ids = [r["id"] for r in conn.execute("SELECT id FROM lessons WHERE topic_id = ?", (topic_id,))]
    if subtopic_ids:
        marks = ",".join("?" * len(subtopic_ids))
        card_ids = [r["id"] for r in conn.execute(f"SELECT id FROM cards WHERE subtopic_id IN ({marks})", subtopic_ids)]
        if card_ids:
            conn.execute(f"DELETE FROM reviews WHERE card_id IN ({','.join('?' * len(card_ids))})", card_ids)
        conn.execute(f"DELETE FROM cards WHERE subtopic_id IN ({marks})", subtopic_ids)
        conn.execute(f"DELETE FROM questions WHERE subtopic_id IN ({marks})", subtopic_ids)
    if lesson_ids:
        conn.execute(f"DELETE FROM answers WHERE lesson_id IN ({','.join('?' * len(lesson_ids))})", lesson_ids)
    conn.execute("DELETE FROM lessons WHERE topic_id = ?", (topic_id,))
    conn.execute("DELETE FROM subtopics WHERE topic_id = ?", (topic_id,))
    conn.execute("DELETE FROM topics WHERE id = ?", (topic_id,))
    conn.commit()


def topic_row(conn, topic_id):
    return conn.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone()


def syllabus_titles(conn, topic_id):
    return [r["title"] for r in conn.execute(
        "SELECT title FROM subtopics WHERE topic_id = ? ORDER BY position", (topic_id,)
    )]


def pending_titles(conn, topic_id, limit=None):
    """Subtopics in the syllabus that have no material yet — exactly what a
    refill prompt should ask for."""
    rows = conn.execute(
        "SELECT title FROM subtopics WHERE topic_id = ? AND imported_at IS NULL ORDER BY position",
        (topic_id,),
    ).fetchall()
    titles = [r["title"] for r in rows]
    return titles[:limit] if limit else titles


def topic_progress(conn, topic_id):
    row = conn.execute(
        """
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN imported_at IS NOT NULL THEN 1 ELSE 0 END) AS with_material,
          SUM(CASE WHEN l.id IS NOT NULL THEN 1 ELSE 0 END) AS served
        FROM subtopics s LEFT JOIN lessons l ON l.subtopic_id = s.id
        WHERE s.topic_id = ?
        """,
        (topic_id,),
    ).fetchone()
    total = row["total"] or 0
    with_material = row["with_material"] or 0
    served = row["served"] or 0
    return {
        "subtopics_total": total,
        "subtopics_with_material": with_material,
        "subtopics_served": served,
        # The number of days this topic can still produce a lesson for — the
        # one figure that says whether you need to go and fetch a pack.
        "days_of_material_left": max(0, with_material - served),
    }


def topic_summary(conn, topic_id):
    topic = topic_row(conn, topic_id)
    if not topic:
        return None
    progress = topic_progress(conn, topic_id)
    return {
        "id": topic["id"],
        "name": topic["name"],
        "goal": topic["goal"],
        "level": topic["level"],
        "active": bool(topic["active"]),
        "created_at": topic["created_at"],
        "last_served_on": topic["last_served_on"],
        **progress,
    }


# --- Importing a pack ---


def apply_pack(conn, topic_id, pack):
    """Write a normalised pack (see importer.normalise) into the database.

    Syllabus entries are matched by title, so a refill can attach material to
    subtopics an earlier pack established, and re-importing the same pack
    updates in place instead of duplicating. Titles are the join key rather
    than positions because the assistant is told to echo titles verbatim but
    is not asked to keep any numbering.
    """
    added_subtopics = added_material = added_questions = added_cards = 0

    by_title = {
        r["title"].strip().lower(): r
        for r in conn.execute("SELECT * FROM subtopics WHERE topic_id = ?", (topic_id,))
    }
    next_position = (
        conn.execute("SELECT COALESCE(MAX(position), 0) AS p FROM subtopics WHERE topic_id = ?", (topic_id,))
        .fetchone()["p"]
    )

    def ensure_subtopic(title, summary=None):
        nonlocal next_position, added_subtopics
        key = title.strip().lower()
        row = by_title.get(key)
        if row is not None:
            if summary and not row["summary"]:
                conn.execute("UPDATE subtopics SET summary = ? WHERE id = ?", (summary, row["id"]))
            return row["id"]
        next_position += 1
        cur = conn.execute(
            "INSERT INTO subtopics (topic_id, position, title, summary) VALUES (?, ?, ?, ?)",
            (topic_id, next_position, title, summary),
        )
        added_subtopics += 1
        by_title[key] = conn.execute("SELECT * FROM subtopics WHERE id = ?", (cur.lastrowid,)).fetchone()
        return cur.lastrowid

    for entry in pack["syllabus"]:
        ensure_subtopic(entry["title"], entry.get("summary"))

    for entry in pack["material"]:
        subtopic_id = ensure_subtopic(entry["title"], entry.get("summary"))
        already = conn.execute(
            "SELECT imported_at FROM subtopics WHERE id = ?", (subtopic_id,)
        ).fetchone()["imported_at"]
        if already:
            # Material for this subtopic is already here. Replacing it would
            # invalidate any answers already given against those questions,
            # so the older material wins and the newer is reported as skipped.
            pack["warnings"].append(f"kept the existing material for {entry['title']!r} (already imported)")
            continue

        conn.execute(
            "UPDATE subtopics SET briefing = ?, practical_task = ?, imported_at = ? WHERE id = ?",
            (entry["briefing"], entry["practical_task"], _now_iso(), subtopic_id),
        )
        added_material += 1
        for position, question in enumerate(entry["questions"], start=1):
            conn.execute(
                "INSERT INTO questions "
                "(subtopic_id, position, kind, question, choices_json, answer_index, model_answer, explanation) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    subtopic_id,
                    position,
                    question["kind"],
                    question["question"],
                    json.dumps(question["choices"]) if question["choices"] else None,
                    question["answer_index"],
                    question["model_answer"],
                    question["explanation"],
                ),
            )
            added_questions += 1
        for card in entry["cards"]:
            conn.execute(
                "INSERT INTO cards (subtopic_id, front, back) VALUES (?, ?, ?)",
                (subtopic_id, card["front"], card["back"]),
            )
            added_cards += 1

    conn.execute(
        "INSERT INTO imports "
        "(imported_at, topic_id, subtopics_added, material_added, questions_added, cards_added, warnings_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            _now_iso(),
            topic_id,
            added_subtopics,
            added_material,
            added_questions,
            added_cards,
            json.dumps(pack["warnings"]),
        ),
    )
    conn.commit()
    return {
        "subtopics_added": added_subtopics,
        "material_added": added_material,
        "questions_added": added_questions,
        "cards_added": added_cards,
        "warnings": pack["warnings"],
    }


# --- The daily lesson ---


def _next_subtopic_for(conn, topic_id):
    """The earliest subtopic that has material and has never been served.

    Position order, not import order: if a later pack fills in a subtopic
    that was skipped over, it takes its rightful place in the sequence the
    next day rather than being lost.
    """
    return conn.execute(
        """
        SELECT s.* FROM subtopics s
        LEFT JOIN lessons l ON l.subtopic_id = s.id
        WHERE s.topic_id = ? AND s.imported_at IS NOT NULL AND l.id IS NULL
        ORDER BY s.position LIMIT 1
        """,
        (topic_id,),
    ).fetchone()


def ensure_today(conn, today=None, cfg=None):
    """Make sure today's lessons exist, and return them.

    Idempotent — every caller (the page load, the background loop, the
    sensor push) runs it, and the day's lessons are created exactly once by
    whichever gets there first.
    """
    today = today or _today()
    cfg = cfg or get_config()
    day = today.isoformat()

    existing = conn.execute("SELECT * FROM lessons WHERE day = ? ORDER BY id", (day,)).fetchall()
    if len(existing) >= cfg["lessons_per_day"]:
        return existing

    used_topics = {r["topic_id"] for r in existing}
    while len(existing) < cfg["lessons_per_day"]:
        # Round-robin across subscribed topics: least recently served first,
        # so with several topics and one lesson a day they take turns instead
        # of the lowest id monopolising every morning.
        candidates = conn.execute(
            "SELECT * FROM topics WHERE active = 1 ORDER BY (last_served_on IS NOT NULL), last_served_on, id"
        ).fetchall()
        chosen = None
        for topic in candidates:
            if topic["id"] in used_topics and len(candidates) > len(used_topics):
                continue
            subtopic = _next_subtopic_for(conn, topic["id"])
            if subtopic:
                chosen = (topic, subtopic)
                break
        if not chosen:
            break

        topic, subtopic = chosen
        conn.execute(
            "INSERT INTO lessons (day, topic_id, subtopic_id, created_at) VALUES (?, ?, ?, ?)",
            (day, topic["id"], subtopic["id"], _now_iso()),
        )
        conn.execute("UPDATE topics SET last_served_on = ? WHERE id = ?", (day, topic["id"]))
        # The subtopic's flashcards enter the review queue with their lesson,
        # not at import: cards for material you have not studied yet would
        # otherwise flood the first review as unrecognisable trivia.
        conn.execute(
            "UPDATE cards SET due_on = ? WHERE subtopic_id = ? AND due_on IS NULL", (day, subtopic["id"])
        )
        used_topics.add(topic["id"])
        existing = conn.execute("SELECT * FROM lessons WHERE day = ? ORDER BY id", (day,)).fetchall()

    conn.commit()
    return existing


def question_rows(conn, subtopic_id, reveal=False, lesson_id=None):
    """A subtopic's questions as the UI needs them.

    `reveal` is False while the quiz is unanswered: the correct index and the
    explanation are withheld from the payload entirely rather than hidden in
    the page, because anything sent to the browser is answerable with the
    developer console, and a quiz you can read the answers off is not a quiz.
    """
    answers = {}
    if lesson_id is not None:
        answers = {
            r["question_id"]: r
            for r in conn.execute("SELECT * FROM answers WHERE lesson_id = ?", (lesson_id,))
        }
    out = []
    for row in conn.execute(
        "SELECT * FROM questions WHERE subtopic_id = ? ORDER BY position", (subtopic_id,)
    ):
        answer = answers.get(row["id"])
        answered = answer is not None
        item = {
            "id": row["id"],
            "kind": row["kind"],
            "question": row["question"],
            "choices": json.loads(row["choices_json"]) if row["choices_json"] else None,
            "answered": answered,
            "chosen_index": answer["chosen_index"] if answered else None,
            "self_grade": answer["self_grade"] if answered else None,
            "response_text": answer["response_text"] if answered else None,
            "correct": bool(answer["correct"]) if answered and answer["correct"] is not None else None,
        }
        if reveal or answered:
            item["answer_index"] = row["answer_index"]
            item["model_answer"] = row["model_answer"]
            item["explanation"] = row["explanation"]
        out.append(item)
    return out


def lesson_payload(conn, lesson):
    subtopic = conn.execute("SELECT * FROM subtopics WHERE id = ?", (lesson["subtopic_id"],)).fetchone()
    topic = topic_row(conn, lesson["topic_id"])
    questions = question_rows(conn, subtopic["id"], lesson_id=lesson["id"])
    answered = sum(1 for q in questions if q["answered"])
    graded = [q for q in questions if q["answered"] and q["correct"] is not None]
    return {
        "id": lesson["id"],
        "day": lesson["day"],
        "completed_at": lesson["completed_at"],
        "topic": {"id": topic["id"], "name": topic["name"]},
        "subtopic": {
            "id": subtopic["id"],
            "position": subtopic["position"],
            "title": subtopic["title"],
            "summary": subtopic["summary"],
            "briefing": subtopic["briefing"],
            # Rendered here rather than in the page: the briefing is whatever an
            # assistant produced, so turning it into HTML is security-sensitive
            # and belongs somewhere it can be unit-tested. The raw text is kept
            # alongside so nothing is lost if the renderer is ever wrong.
            "briefing_html": markdown.render(subtopic["briefing"]),
            "practical_task": subtopic["practical_task"],
            "practical_task_html": markdown.render(subtopic["practical_task"]),
        },
        "questions": questions,
        "answered_count": answered,
        "question_count": len(questions),
        "correct_count": sum(1 for q in graded if q["correct"]),
        "graded_count": len(graded),
    }


def grade_answer(conn, lesson_id, question_id, chosen_index=None, self_grade=None, response_text=None):
    question = conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
    if question is None:
        raise ValueError("no such question")
    lesson = conn.execute("SELECT * FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
    if lesson is None:
        raise ValueError("no such lesson")
    if question["subtopic_id"] != lesson["subtopic_id"]:
        raise ValueError("that question does not belong to that lesson")

    if question["kind"] == "mcq":
        if chosen_index is None:
            raise ValueError("a multiple-choice question needs chosen_index")
        correct = 1 if int(chosen_index) == question["answer_index"] else 0
        self_grade = None
        response_text = None
    else:
        if self_grade not in ("got_it", "partly", "missed"):
            raise ValueError("a short-answer question needs self_grade of got_it, partly or missed")
        # "partly" deliberately does not count as correct. The score is meant
        # to be a signal about what to revisit, and a half-remembered answer
        # is exactly what wants revisiting.
        correct = 1 if self_grade == "got_it" else 0
        chosen_index = None

    conn.execute(
        "INSERT OR REPLACE INTO answers "
        "(lesson_id, question_id, answered_at, chosen_index, self_grade, response_text, correct) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (lesson_id, question_id, _now_iso(), chosen_index, self_grade, (response_text or "").strip() or None, correct),
    )
    _maybe_complete(conn, lesson_id)
    conn.commit()
    return {
        "correct": bool(correct),
        "answer_index": question["answer_index"],
        "model_answer": question["model_answer"],
        "explanation": question["explanation"],
    }


def _maybe_complete(conn, lesson_id):
    """Mark a lesson done once every one of its questions has an answer."""
    lesson = conn.execute("SELECT * FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
    if lesson is None or lesson["completed_at"]:
        return
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM questions WHERE subtopic_id = ?", (lesson["subtopic_id"],)
    ).fetchone()["n"]
    answered = conn.execute(
        "SELECT COUNT(*) AS n FROM answers WHERE lesson_id = ?", (lesson_id,)
    ).fetchone()["n"]
    if total and answered >= total:
        conn.execute("UPDATE lessons SET completed_at = ? WHERE id = ?", (_now_iso(), lesson_id))


# --- Flashcard review ---


def due_cards(conn, today=None, limit=None):
    today = (today or _today()).isoformat()
    limit = limit or get_config()["cards_per_day"]
    return conn.execute(
        """
        SELECT c.*, s.title AS subtopic_title, t.name AS topic_name
        FROM cards c
        JOIN subtopics s ON s.id = c.subtopic_id
        JOIN topics t ON t.id = s.topic_id
        WHERE c.due_on IS NOT NULL AND c.due_on <= ?
        ORDER BY c.due_on, c.id
        LIMIT ?
        """,
        (today, limit),
    ).fetchall()


def review_card(conn, card_id, grade, today=None):
    today = today or _today()
    card = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    if card is None:
        raise ValueError("no such card")
    repetitions, interval, ease = srs.schedule(
        grade, card["repetitions"], card["interval_days"], card["ease"]
    )
    lapses = card["lapses"] + (1 if grade == "again" else 0)
    conn.execute(
        "UPDATE cards SET due_on = ?, interval_days = ?, ease = ?, repetitions = ?, lapses = ?, "
        "last_reviewed_at = ? WHERE id = ?",
        (srs.due_date(today, interval), interval, ease, repetitions, lapses, _now_iso(), card_id),
    )
    conn.execute(
        "INSERT INTO reviews (card_id, reviewed_at, grade, interval_days) VALUES (?, ?, ?, ?)",
        (card_id, _now_iso(), grade, interval),
    )
    conn.commit()
    return {"card_id": card_id, "interval_days": interval, "due_on": srs.due_date(today, interval), "ease": ease}


# --- Progress ---


def streak_days(conn, today=None):
    """Consecutive days up to today with at least one completed lesson.

    Today not being done yet does not break the streak — it is only broken
    once a whole day has passed with nothing completed, so the number does
    not spend every morning telling you that you have lost it.
    """
    today = today or _today()
    days = {
        r["day"]
        for r in conn.execute("SELECT DISTINCT day FROM lessons WHERE completed_at IS NOT NULL")
    }
    if not days:
        return 0
    cursor = today if today.isoformat() in days else today - timedelta(days=1)
    streak = 0
    while cursor.isoformat() in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def overall_stats(conn, today=None):
    today = today or _today()
    graded = conn.execute(
        "SELECT COUNT(*) AS n, SUM(correct) AS c FROM answers WHERE correct IS NOT NULL"
    ).fetchone()
    lessons_done = conn.execute(
        "SELECT COUNT(*) AS n FROM lessons WHERE completed_at IS NOT NULL"
    ).fetchone()["n"]
    cards_total = conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"]
    cards_due = conn.execute(
        "SELECT COUNT(*) AS n FROM cards WHERE due_on IS NOT NULL AND due_on <= ?", (today.isoformat(),)
    ).fetchone()["n"]
    answered = graded["n"] or 0
    correct = graded["c"] or 0
    return {
        "lessons_completed": lessons_done,
        "questions_answered": answered,
        "accuracy": round(correct / answered, 3) if answered else None,
        "streak_days": streak_days(conn, today),
        "cards_total": cards_total,
        "cards_due": cards_due,
    }


def material_warning(conn):
    """The topic closest to running out, if any is below the threshold."""
    cfg = get_config()
    worst = None
    for topic in conn.execute("SELECT * FROM topics WHERE active = 1"):
        left = topic_progress(conn, topic["id"])["days_of_material_left"]
        if left <= cfg["low_material_threshold"] and (worst is None or left < worst["days_left"]):
            worst = {"topic_id": topic["id"], "topic": topic["name"], "days_left": left}
    return worst


# --- Home Assistant sensors and notifications ---


def _ha_api(path, method="GET", payload=None, timeout=10):
    if not SUPERVISOR_TOKEN:
        return None, "SUPERVISOR_TOKEN not set (not running under Supervisor)"
    req = urllib.request.Request(f"{CORE_API}{path}", method=method)
    req.add_header("Authorization", f"Bearer {SUPERVISOR_TOKEN}")
    req.add_header("Content-Type", "application/json")
    data = json.dumps(payload).encode() if payload is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
            body = resp.read()
            return (json.loads(body) if body else None), None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}: {exc.read().decode('utf-8', 'ignore')[:200]}"
    except Exception as exc:  # noqa: BLE001 - a push failure must not kill the loop
        return None, str(exc)


def push_sensor(entity_id, state, attributes):
    return _ha_api(f"/states/{entity_id}", method="POST", payload={"state": state, "attributes": attributes})


def send_notification(message, title="Knowledge"):
    service = get_config()["notify_service"]
    if not service:
        return False, "no notify service configured"
    _, err = _ha_api(f"/services/notify/{service}", method="POST", payload={"message": message, "title": title})
    return err is None, err


def get_notify_services():
    data, err = _ha_api("/services")
    if err or not data:
        return [], err
    for entry in data:
        if entry.get("domain") == "notify":
            return sorted(entry.get("services", {}).keys()), None
    return [], None


def publish_sensors(conn, today=None):
    today = today or _today()
    lessons = ensure_today(conn, today)
    stats = overall_stats(conn, today)

    if lessons:
        payloads = [lesson_payload(conn, row) for row in lessons]
        first = payloads[0]
        push_sensor(
            "sensor.knowledge_today",
            first["subtopic"]["title"][:255],
            {
                "friendly_name": "Knowledge today",
                "icon": "mdi:school",
                "topic": first["topic"]["name"],
                "subtopic_number": first["subtopic"]["position"],
                "questions": first["question_count"],
                "answered": first["answered_count"],
                "completed": bool(first["completed_at"]),
                "also_today": [p["subtopic"]["title"] for p in payloads[1:]],
            },
        )
    else:
        push_sensor(
            "sensor.knowledge_today",
            "nothing scheduled",
            {"friendly_name": "Knowledge today", "icon": "mdi:school", "reason": "no material left to serve"},
        )

    push_sensor(
        "sensor.knowledge_streak",
        stats["streak_days"],
        {
            "friendly_name": "Knowledge streak",
            "icon": "mdi:fire",
            "unit_of_measurement": "days",
            "state_class": "measurement",
            "lessons_completed": stats["lessons_completed"],
            "accuracy": stats["accuracy"],
        },
    )
    push_sensor(
        "sensor.knowledge_cards_due",
        stats["cards_due"],
        {
            "friendly_name": "Knowledge cards due",
            "icon": "mdi:cards-outline",
            "unit_of_measurement": "cards",
            "state_class": "measurement",
            "cards_total": stats["cards_total"],
        },
    )
    warning = material_warning(conn)
    push_sensor(
        "sensor.knowledge_material_left",
        warning["days_left"] if warning else _max_days_left(conn),
        {
            "friendly_name": "Knowledge material left",
            "icon": "mdi:book-clock-outline",
            "unit_of_measurement": "days",
            "state_class": "measurement",
            "topic": warning["topic"] if warning else None,
            "running_low": bool(warning),
        },
    )


def _max_days_left(conn):
    rows = [topic_progress(conn, t["id"])["days_of_material_left"]
            for t in conn.execute("SELECT id FROM topics WHERE active = 1")]
    return min(rows) if rows else 0


def _parse_hhmm(value):
    try:
        hh, mm = str(value).split(":")
        return dtime(int(hh), int(mm))
    except (ValueError, AttributeError):
        return dtime(18, 0)


def maybe_send_reminder(conn, now=None):
    """Once a day, at the configured time, say what today's subtopic is."""
    cfg = get_config()
    if not cfg["reminder_enabled"] or not cfg["notify_service"]:
        return False
    now = now or datetime.now()
    today_iso = now.date().isoformat()
    if _get_app_state(conn, "reminder_last_sent") == today_iso:
        return False
    if now.time() < _parse_hhmm(cfg["reminder_time"]):
        return False

    lessons = ensure_today(conn, now.date(), cfg)
    if not lessons:
        warning = material_warning(conn)
        if warning:
            send_notification(
                f"No lesson today — {warning['topic']} has run out of material. "
                "Open Knowledge to copy a prompt for the next pack."
            )
            _set_app_state(conn, "reminder_last_sent", today_iso)
            conn.commit()
            return True
        return False

    payloads = [lesson_payload(conn, row) for row in lessons]
    titles = ", ".join(p["subtopic"]["title"] for p in payloads)
    message = f"Today: {titles}"
    stats = overall_stats(conn, now.date())
    if stats["cards_due"]:
        message += f" · {stats['cards_due']} cards due"
    warning = material_warning(conn)
    if warning:
        message += f" · {warning['topic']} has {warning['days_left']} days of material left"
    send_notification(message)
    _set_app_state(conn, "reminder_last_sent", today_iso)
    conn.commit()
    return True


# --- Background loop ---


def _background_loop():
    if not SUPERVISOR_TOKEN:
        _log("SUPERVISOR_TOKEN not set; sensor push and reminders disabled (local/dev mode)")
    while True:
        try:
            conn = _db_connect_standalone()
            try:
                ensure_today(conn)
                if SUPERVISOR_TOKEN:
                    maybe_send_reminder(conn)
                    publish_sensors(conn)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - keep the loop alive across any single failure
            app.logger.exception("background loop iteration failed")
        time.sleep(BACKGROUND_TICK_SECONDS)


# --- Routes: pages ---


@app.route("/")
def index():
    return render_template("index.html", app_version=APP_VERSION)


# --- Routes: topics ---


@app.route("/api/topics", methods=["GET", "POST"])
def api_topics():
    db = get_db()
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        try:
            topic_id = create_topic(db, body.get("name"), body.get("goal"), body.get("level"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(topic_summary(db, topic_id)), 201
    return jsonify([topic_summary(db, r["id"]) for r in db.execute("SELECT id FROM topics ORDER BY id")])


@app.route("/api/topics/<int:topic_id>", methods=["GET", "PATCH", "DELETE"])
def api_topic(topic_id):
    db = get_db()
    if topic_row(db, topic_id) is None:
        return jsonify({"error": "no such topic"}), 404
    if request.method == "DELETE":
        delete_topic(db, topic_id)
        return jsonify({"deleted": topic_id})
    if request.method == "PATCH":
        body = request.get_json(silent=True) or {}
        if "active" in body:
            db.execute("UPDATE topics SET active = ? WHERE id = ?", (1 if body["active"] else 0, topic_id))
        if "goal" in body:
            db.execute("UPDATE topics SET goal = ? WHERE id = ?", ((body["goal"] or "").strip() or None, topic_id))
        if "level" in body and (body["level"] or "").lower() in LEVELS:
            db.execute("UPDATE topics SET level = ? WHERE id = ?", (body["level"].lower(), topic_id))
        db.commit()
    return jsonify(topic_summary(db, topic_id))


@app.route("/api/topics/<int:topic_id>/prompt")
def api_topic_prompt(topic_id):
    """The prompt to take to an assistant — the whole outbound half of this
    add-on. `kind` picks which stage: the first pack, a refill for subtopics
    already in the syllabus, or an extension once the syllabus is finished.
    """
    db = get_db()
    topic = topic_row(db, topic_id)
    if topic is None:
        return jsonify({"error": "no such topic"}), 404
    cfg = get_config()
    kind = request.args.get("kind", "auto")
    titles = syllabus_titles(db, topic_id)
    pending = pending_titles(db, topic_id, limit=cfg["material_days"])

    if kind == "auto":
        kind = "new" if not titles else ("more" if pending else "extend")

    if kind == "new":
        text = prompts.new_topic_prompt(
            topic["name"], topic["goal"], topic["level"],
            syllabus_size=cfg["syllabus_size"], material_count=cfg["material_days"], counts=_counts(cfg),
        )
    elif kind == "more":
        if not pending:
            return jsonify({"error": "every subtopic in this syllabus already has material"}), 400
        text = prompts.more_material_prompt(
            topic["name"], titles, pending, topic["level"], counts=_counts(cfg)
        )
    elif kind == "extend":
        text = prompts.extend_syllabus_prompt(
            topic["name"], titles, extra=cfg["material_days"], level=topic["level"], counts=_counts(cfg)
        )
    else:
        return jsonify({"error": f"unknown prompt kind {kind!r}"}), 400

    return jsonify(
        {
            "kind": kind,
            "topic": topic["name"],
            "prompt": text,
            "filename": f"knowledge-{topic['name'].lower().replace(' ', '-')[:40]}-{kind}.txt",
            "covers": pending if kind == "more" else None,
        }
    )


# --- Routes: importing ---


@app.route("/api/import", methods=["POST"])
def api_import():
    """Take the assistant's reply — pasted text or an uploaded file — and
    load whatever of it is usable."""
    db = get_db()
    if request.files.get("file") is not None:
        upload = request.files["file"]
        text = upload.read().decode("utf-8", "replace")
        topic_id = request.form.get("topic_id", type=int)
    else:
        body = request.get_json(silent=True) or {}
        text = body.get("text") or ""
        topic_id = body.get("topic_id")

    if not topic_id:
        return jsonify({"error": "topic_id is required — packs are imported into a topic you subscribed to"}), 400
    topic = topic_row(db, int(topic_id))
    if topic is None:
        return jsonify({"error": "no such topic"}), 404

    try:
        pack = importer.parse(text, expected_topic=topic["name"])
    except importer.PackError as exc:
        return jsonify({"error": str(exc)}), 400

    report = apply_pack(db, topic["id"], pack)
    ensure_today(db)
    _log(
        f"import into {topic['name']!r}: +{report['subtopics_added']} subtopics, "
        f"+{report['material_added']} with material, +{report['questions_added']} questions, "
        f"+{report['cards_added']} cards, {len(report['warnings'])} warnings"
    )
    return jsonify({**report, "topic": topic_summary(db, topic["id"])})


@app.route("/api/import/preview", methods=["POST"])
def api_import_preview():
    """Parse without writing — lets the page say what a paste contains
    before it commits, which matters when the reply took real effort to get."""
    body = request.get_json(silent=True) or {}
    try:
        pack = importer.parse(body.get("text") or "")
    except importer.PackError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify(
        {
            "ok": True,
            "topic": pack["topic"],
            "syllabus_count": len(pack["syllabus"]),
            "material_count": len(pack["material"]),
            "question_count": sum(len(m["questions"]) for m in pack["material"]),
            "card_count": sum(len(m["cards"]) for m in pack["material"]),
            "warnings": pack["warnings"],
        }
    )


# --- Routes: the daily lesson ---


@app.route("/api/summary")
def api_summary():
    db = get_db()
    today = _today()
    lessons = ensure_today(db, today)
    return jsonify(
        {
            "app_version": APP_VERSION,
            "today": today.isoformat(),
            "lessons": [lesson_payload(db, row) for row in lessons],
            "topics": [topic_summary(db, r["id"]) for r in db.execute("SELECT id FROM topics ORDER BY id")],
            "stats": overall_stats(db, today),
            "material_warning": material_warning(db),
            "cards_due": len(due_cards(db, today)),
            "config": {k: v for k, v in get_config().items() if k != "notify_service"},
        }
    )


@app.route("/api/answers", methods=["POST"])
def api_answer():
    db = get_db()
    body = request.get_json(silent=True) or {}
    try:
        result = grade_answer(
            db,
            int(body.get("lesson_id")),
            int(body.get("question_id")),
            chosen_index=body.get("chosen_index"),
            self_grade=body.get("self_grade"),
            response_text=body.get("response_text"),
        )
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400
    lesson = db.execute("SELECT * FROM lessons WHERE id = ?", (int(body["lesson_id"]),)).fetchone()
    return jsonify({**result, "lesson": lesson_payload(db, lesson)})


@app.route("/api/questions/<int:question_id>/reveal")
def api_reveal_question(question_id):
    """The model answer for a short-answer question, fetched at the moment the
    learner asks for it.

    Withheld from the lesson payload for the same reason the quiz's answer key
    is: anything sent to the browser is readable before you have tried. Self
    grading only means something if the model answer arrives after your own.
    """
    db = get_db()
    row = db.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
    if row is None:
        return jsonify({"error": "no such question"}), 404
    if row["kind"] != "short":
        return jsonify({"error": "only short-answer questions are revealed this way"}), 400
    return jsonify({"model_answer": row["model_answer"], "explanation": row["explanation"]})


@app.route("/api/lessons/<int:lesson_id>/complete", methods=["POST"])
def api_complete_lesson(lesson_id):
    db = get_db()
    lesson = db.execute("SELECT * FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
    if lesson is None:
        return jsonify({"error": "no such lesson"}), 404
    if not lesson["completed_at"]:
        db.execute("UPDATE lessons SET completed_at = ? WHERE id = ?", (_now_iso(), lesson_id))
        db.commit()
    lesson = db.execute("SELECT * FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
    return jsonify(lesson_payload(db, lesson))


@app.route("/api/history")
def api_history():
    db = get_db()
    days = min(365, max(1, request.args.get("days", default=30, type=int) or 30))
    since = (_today() - timedelta(days=days - 1)).isoformat()
    rows = db.execute(
        """
        SELECT l.day, l.completed_at, s.title, t.name AS topic,
               (SELECT COUNT(*) FROM answers a WHERE a.lesson_id = l.id) AS answered,
               (SELECT COALESCE(SUM(a.correct), 0) FROM answers a WHERE a.lesson_id = l.id) AS correct
        FROM lessons l
        JOIN subtopics s ON s.id = l.subtopic_id
        JOIN topics t ON t.id = l.topic_id
        WHERE l.day >= ? ORDER BY l.day DESC, l.id DESC
        """,
        (since,),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


# --- Routes: flashcards ---


@app.route("/api/cards/due")
def api_cards_due():
    db = get_db()
    rows = due_cards(db)
    return jsonify(
        [
            {
                "id": r["id"],
                "front": r["front"],
                "back": r["back"],
                "topic": r["topic_name"],
                "subtopic": r["subtopic_title"],
                "repetitions": r["repetitions"],
            }
            for r in rows
        ]
    )


@app.route("/api/cards/<int:card_id>/review", methods=["POST"])
def api_review_card(card_id):
    db = get_db()
    body = request.get_json(silent=True) or {}
    grade = (body.get("grade") or "").strip().lower()
    try:
        return jsonify(review_card(db, card_id, grade))
    except (srs.UnknownGrade, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400


# --- Routes: diagnostics ---


@app.route("/api/notify-services")
def api_notify_services():
    services, err = get_notify_services()
    return jsonify({"services": services, "error": err})


TRACKED_TABLES = (
    "topics", "subtopics", "questions", "cards", "lessons", "answers", "reviews", "imports",
)


@app.route("/api/stats")
def api_stats():
    db = get_db()

    def count(table):
        return db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]

    try:
        size = os.path.getsize(DB_PATH)
    except OSError:
        size = None
    return jsonify(
        {
            "app_version": APP_VERSION,
            "db_path": DB_PATH,
            "db_bytes": size,
            "counts": {table: count(table) for table in TRACKED_TABLES},
            "last_import": _get_app_state(db, "last_import"),
            "supervisor": bool(SUPERVISOR_TOKEN),
        }
    )


@app.route("/api/backup")
def api_backup():
    db = get_db()

    def rows(table):
        return [dict(r) for r in db.execute(f"SELECT * FROM {table}")]

    return jsonify(
        {
            "app_version": APP_VERSION,
            "taken_at": _now_iso(),
            "tables": {table: rows(table) for table in TRACKED_TABLES},
        }
    )


# --- Shutdown + entrypoint ---


def _handle_shutdown_signal(signum, _frame):
    _log(f"received signal {signum}, shutting down")
    sys.exit(0)


if __name__ == "__main__":
    from waitress import serve

    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    init_db()
    _log(f"starting Knowledge {APP_VERSION}")
    _startup_conn = _db_connect_standalone()
    try:
        seed_starter_topics(_startup_conn)
    finally:
        _startup_conn.close()
    threading.Thread(target=_background_loop, daemon=True).start()
    port = int(os.environ.get("KNOWLEDGE_PORT", "8099"))
    _log(f"serving on 0.0.0.0:{port} (waitress)")
    serve(app, host="0.0.0.0", port=port)
