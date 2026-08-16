"""Shared pytest fixtures for the Electricity Tracker Flask app.

`pytest.ini` adds `app/` to `sys.path` (via `pythonpath`), so `import app`
resolves to `app/app.py` — the same module the container runs.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import app as electricityapp


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """Every test starts as if the add-on just started: no Supervisor token
    (local/dev mode), and no cached Eloverblik access token from a previous
    test."""
    monkeypatch.setattr(electricityapp, "SUPERVISOR_TOKEN", None)
    monkeypatch.setattr(
        electricityapp,
        "_eloverblik_token_cache",
        {"refresh_token": None, "access_token": None, "expires_at": 0.0},
    )
    monkeypatch.setattr(electricityapp, "_saveeye_latest", {"payload": None, "received_at": None})
    monkeypatch.setattr(electricityapp, "_saveeye_status", {"connected": False, "detail": None})


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "electricity.db")
    monkeypatch.setattr(electricityapp, "DB_PATH", path)
    electricityapp.init_db()
    return path


@pytest.fixture
def options_path(tmp_path, monkeypatch):
    path = str(tmp_path / "options.json")
    monkeypatch.setattr(electricityapp, "OPTIONS_PATH", path)
    return path


@pytest.fixture
def set_options(options_path):
    def _set(**overrides):
        with open(options_path, "w") as f:
            json.dump(overrides, f)

    return _set


@pytest.fixture
def client(db_path, options_path):
    """A browser arriving through Home Assistant's ingress proxy."""
    electricityapp.app.testing = True
    with electricityapp.app.test_client() as test_client:
        test_client.environ_base["HTTP_X_REMOTE_USER_ID"] = "test-ingress-user"
        yield test_client


@pytest.fixture
def direct_client(db_path, options_path):
    """A caller on the published port: no ingress header, nothing but
    whatever it sends itself."""
    electricityapp.app.testing = True
    with electricityapp.app.test_client() as test_client:
        yield test_client


@pytest.fixture
def conn(db_path):
    c = electricityapp._db_connect_standalone()
    yield c
    c.close()


@pytest.fixture
def fake_ha_server(monkeypatch):
    """A minimal local HTTP server standing in for Home Assistant's
    Supervisor Core API, so sensor pushes can be exercised without a real
    Home Assistant. Yields a list appended to on every request."""
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

        def log_message(self, *args, **kwargs):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    monkeypatch.setattr(electricityapp, "CORE_API", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(electricityapp, "SUPERVISOR_TOKEN", "test-token")

    yield calls

    server.shutdown()
    thread.join(timeout=2)
