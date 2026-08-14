"""Shared pytest fixtures for the Pulse Runner Flask app.

`pytest.ini` adds `app/` to `sys.path` (via `pythonpath`), so `import app`
resolves to `app/app.py` — the same module the container runs.
"""
import json

import pytest

import app as pulseapp


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "pulse.db")
    monkeypatch.setattr(pulseapp, "DB_PATH", path)
    pulseapp.init_db()
    return path


@pytest.fixture
def options_path(tmp_path, monkeypatch):
    path = str(tmp_path / "options.json")
    monkeypatch.setattr(pulseapp, "OPTIONS_PATH", path)
    return path


@pytest.fixture
def audio_dir(tmp_path, monkeypatch):
    path = str(tmp_path / "audio")
    monkeypatch.setattr(pulseapp, "AUDIO_DIR", path)
    return path


@pytest.fixture
def set_options(options_path):
    """Write the add-on's options.json for the current test, e.g.
    `set_options(restrict_to_user_ids="abc,def")`."""

    def _set(**overrides):
        with open(options_path, "w") as f:
            json.dump(overrides, f)

    return _set


@pytest.fixture
def client(db_path, options_path):
    """A browser arriving through Home Assistant's ingress proxy — the only
    door this add-on has, since it publishes no port."""
    pulseapp.app.testing = True
    with pulseapp.app.test_client() as test_client:
        test_client.environ_base["HTTP_X_REMOTE_USER_ID"] = "test-ingress-user"
        yield test_client


@pytest.fixture
def conn(db_path):
    """A standalone sqlite3 connection, for exercising internal helpers
    outside of a Flask request — the same connection type the background
    loop uses in production."""
    c = pulseapp._db_connect_standalone()
    yield c
    c.close()


@pytest.fixture
def level(conn):
    """A user-owned (non-official) level, for CRUD/ownership tests."""
    objects = [
        {"type": "block", "x": 0, "y": 0, "w": 10, "h": 1},
        {"type": "spike", "x": 5, "y": 1, "w": 1, "h": 1},
    ]
    cur = conn.execute(
        "INSERT INTO levels "
        "(name, author, created_by, scroll_speed, start_mode, background, "
        " length_units, objects_json, is_official, sort_order) "
        "VALUES (?, ?, ?, 8, 'cube', 'grid-blue', 20, ?, 0, 0)",
        ("Test Level", "test-ingress-user", "test-ingress-user", json.dumps(objects)),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM levels WHERE id = ?", (cur.lastrowid,)).fetchone()
    return row
