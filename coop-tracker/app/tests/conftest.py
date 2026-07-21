"""Shared pytest fixtures for the Coop Tracker Flask app.

`pytest.ini` adds `app/` to `sys.path` (via `pythonpath`), so `import app`
resolves to `app/app.py` — the same module the container runs.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import app as coopapp


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """Every test starts as if the add-on just started: no reminder sent
    yet today, and no Supervisor token (i.e. local/dev mode) unless a test
    opts into `fake_ha_server`."""
    monkeypatch.setattr(coopapp, "_reminder_last_checked_date", None)
    monkeypatch.setattr(coopapp, "SUPERVISOR_TOKEN", None)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "coop.db")
    monkeypatch.setattr(coopapp, "DB_PATH", path)
    coopapp.init_db()
    return path


@pytest.fixture
def options_path(tmp_path, monkeypatch):
    path = str(tmp_path / "options.json")
    monkeypatch.setattr(coopapp, "OPTIONS_PATH", path)
    return path


@pytest.fixture
def set_options(options_path):
    """Write the add-on's options.json for the current test, e.g.
    `set_options(currency="USD", reminder_enabled=True)`."""

    def _set(**overrides):
        with open(options_path, "w") as f:
            json.dump(overrides, f)

    return _set


@pytest.fixture
def client(db_path, options_path):
    coopapp.app.testing = True
    with coopapp.app.test_client() as test_client:
        yield test_client


@pytest.fixture
def conn(db_path):
    """A standalone sqlite3 connection, for exercising internal helpers
    (_compute_summary, _reminder_tick, ...) outside of a Flask request —
    the same connection type the background loop uses in production."""
    c = coopapp._db_connect_standalone()
    yield c
    c.close()


@pytest.fixture
def fake_ha_server(monkeypatch):
    """A minimal local HTTP server standing in for Home Assistant's
    Supervisor Core API, so notification/sensor code can be exercised
    without a real Home Assistant instance. Yields a list that's appended
    to with every request received: [{"path": ..., "body": ...}, ...]."""
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

    monkeypatch.setattr(coopapp, "HA_API_BASE", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(coopapp, "SUPERVISOR_TOKEN", "test-token")

    yield calls

    server.shutdown()
    thread.join(timeout=2)


@pytest.fixture
def wait_until():
    """Poll a predicate until it's true, for asserting on work done by the
    background thread _push_ha_sensors_async spawns (the HA sensor push is
    fire-and-forget from the request's point of view — see app.py)."""

    def _wait(predicate, timeout=2.0, interval=0.02):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        return predicate()

    return _wait
