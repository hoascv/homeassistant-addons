"""Shared fixtures for the Detection Hub Flask app.

`pytest.ini` adds `app/` to `sys.path` (via `pythonpath`), so `import app`
resolves to `app/app.py` — the same module the container runs.
"""
import json
import os

import pytest

import app as hub
import store

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """Every test starts as if the add-on just booted: no Supervisor token, so
    local/dev mode, and no detector or face identifier carried over from a
    previous test."""
    monkeypatch.setattr(hub, "SUPERVISOR_TOKEN", None)
    monkeypatch.setattr(hub, "_detector", None)
    monkeypatch.setattr(hub, "_face_identifier", None)
    monkeypatch.setattr(hub, "_object_embedder", None)
    # When each camera last kept a crop for review. Left standing, one test's
    # enqueue silences the next one's for ninety seconds — which reads as the
    # rate limit being broken rather than as state leaking.
    monkeypatch.setattr(hub, "_object_queue_last", {})


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """A throwaway database, initialised the way the container would."""
    path = str(tmp_path / "detections.db")
    monkeypatch.setattr(store, "DB_PATH", path)
    store.init_db(path)
    return path


@pytest.fixture
def options_path(tmp_path, monkeypatch):
    """Points at a file that does not exist yet — a freshly installed add-on
    before anything has been saved on the Configuration tab."""
    path = str(tmp_path / "options.json")
    monkeypatch.setattr(hub, "OPTIONS_PATH", path)
    return path


@pytest.fixture
def set_options(options_path):
    """Write the add-on's options.json for this test, e.g.
    `set_options(confidence=0.9, labels="person")`."""

    def _set(**overrides):
        with open(options_path, "w") as handle:
            json.dump(overrides, handle)

    return _set


@pytest.fixture
def client(options_path, db_path):
    """A browser arriving through Home Assistant's ingress proxy.

    The ingress user header is set on every request because that is how the app
    is actually reached. A request without it and without a bearer token is
    refused — tests that mean the published port use `direct_client`.
    """
    hub.app.testing = True
    with hub.app.test_client() as test_client:
        test_client.environ_base["HTTP_X_REMOTE_USER_ID"] = "test-ingress-user"
        yield test_client


@pytest.fixture
def direct_client(options_path, db_path):
    """A caller on the published port: no ingress header, nothing but what it
    sends itself."""
    hub.app.testing = True
    with hub.app.test_client() as test_client:
        yield test_client


@pytest.fixture
def street_jpeg():
    with open(os.path.join(FIXTURES, "street.jpg"), "rb") as handle:
        return handle.read()
