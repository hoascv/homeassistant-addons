"""Shared pytest fixtures for the Goal Tracker Flask app.

`pytest.ini` adds `app/` to `sys.path` (via `pythonpath`), so `import app`
resolves to `app/app.py` — the same module the container runs.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import app as gymapp


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """Every test starts as if the add-on just started: no Supervisor token
    (i.e. local/dev mode) unless a test opts into `fake_ha_server`."""
    monkeypatch.setattr(gymapp, "SUPERVISOR_TOKEN", None)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "gym.db")
    monkeypatch.setattr(gymapp, "DB_PATH", path)
    gymapp.init_db()
    return path


@pytest.fixture
def options_path(tmp_path, monkeypatch):
    path = str(tmp_path / "options.json")
    monkeypatch.setattr(gymapp, "OPTIONS_PATH", path)
    return path


@pytest.fixture
def set_options(options_path):
    """Write the add-on's options.json for the current test, e.g.
    `set_options(notify_service="x", challenge_reminder_enabled=True)`."""

    def _set(**overrides):
        with open(options_path, "w") as f:
            json.dump(overrides, f)

    return _set


@pytest.fixture
def client(db_path, options_path):
    """A browser arriving through Home Assistant's ingress proxy.

    The ingress user header is set for every request because that is how the app
    is actually reached — the published port is the exception, not the norm, and
    since 1.4x a request without this header and without a bearer token is
    refused. Tests that mean the published port use `direct_client`.
    """
    gymapp.app.testing = True
    with gymapp.app.test_client() as test_client:
        test_client.environ_base["HTTP_X_REMOTE_USER_ID"] = "test-ingress-user"
        yield test_client


@pytest.fixture
def direct_client(db_path, options_path):
    """A caller on the published port: no ingress header, no session, nothing
    but whatever it sends itself."""
    gymapp.app.testing = True
    with gymapp.app.test_client() as test_client:
        yield test_client


@pytest.fixture
def conn(db_path):
    """A standalone sqlite3 connection, for exercising internal helpers
    (reminder ticks, streaks, ...) outside of a Flask request — the same
    connection type the background loop uses in production."""
    c = gymapp._db_connect_standalone()
    yield c
    c.close()


@pytest.fixture
def fake_ha_server(monkeypatch):
    """A minimal local HTTP server standing in for Home Assistant's
    Supervisor Core API, so notification code can be exercised without a
    real Home Assistant. Yields a list appended to on every request:
    [{"path": ..., "body": ...}, ...]."""
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            calls.append({"path": self.path, "body": json.loads(body) if body else None})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"[]")

        def log_message(self, *args, **kwargs):
            pass  # keep test output quiet

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    monkeypatch.setattr(gymapp, "HA_API_BASE", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(gymapp, "SUPERVISOR_TOKEN", "test-token")

    yield calls

    server.shutdown()
    thread.join(timeout=2)
