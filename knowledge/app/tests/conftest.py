import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as knowledgeapp  # noqa: E402


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """A real SQLite database on disk with the real schema.

    On disk rather than :memory: because the code under test opens its own
    connections in places (the background loop), and an in-memory database
    would silently be a different database in each.
    """
    path = str(tmp_path / "knowledge.db")
    monkeypatch.setattr(knowledgeapp, "DB_PATH", path)
    knowledgeapp.init_db(path)
    connection = knowledgeapp._connect(path)
    yield connection
    connection.close()


@pytest.fixture
def options(monkeypatch, tmp_path):
    """Write add-on options the way Supervisor does, and point the app at them."""
    path = tmp_path / "options.json"
    monkeypatch.setattr(knowledgeapp, "OPTIONS_PATH", str(path))

    def _write(**values):
        import json

        path.write_text(json.dumps(values))
        return values

    _write()
    return _write


@pytest.fixture
def client(conn, options, monkeypatch):
    """A Flask test client with access control satisfied the ingress way."""
    knowledgeapp.app.config.update(TESTING=True)
    monkeypatch.setattr(knowledgeapp, "get_db", lambda: conn)
    with knowledgeapp.app.test_client() as test_client:
        test_client.environ_base["HTTP_X_REMOTE_USER_ID"] = "test-user"
        yield test_client


def make_pack(topic="Kubernetes", titles=("Pods", "Services"), with_material=True):
    """A pack shaped exactly like a well-behaved assistant would return one."""
    pack = {
        "topic": topic,
        "syllabus": [{"title": t, "summary": f"What {t} are"} for t in titles],
        "material": [],
    }
    if with_material:
        for title in titles:
            pack["material"].append(
                {
                    "title": title,
                    "briefing": f"A briefing about {title}.",
                    "practical_task": f"Go and try {title}.",
                    "quiz": [
                        {
                            "question": f"What is a {title}?",
                            "choices": ["Wrong", "Right", "Also wrong"],
                            "answer": 1,
                            "explanation": "Because it is.",
                        }
                    ],
                    "short_answer": [{"question": f"Explain {title}.", "model_answer": "It does the thing."}],
                    "flashcards": [{"front": title, "back": f"{title} definition"}],
                }
            )
    return pack
