"""End-to-end smoke tests: a real `python app.py` process (waitress and
all), driven by Playwright. Run explicitly with `pytest app/tests_e2e` —
the default `pytest` run stays backend-only via pytest.ini's testpaths.
"""
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def _app_server_data_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("e2e-data")


@pytest.fixture(scope="session")
def app_server_options_path(_app_server_data_dir):
    """The exact path the running app_server reads via COOP_OPTIONS_PATH —
    options are read fresh on every request (no in-memory cache, see
    ARCHITECTURE.md §8), so a test can write this file directly to flip a
    feature flag without needing to restart the shared session server."""
    return str(_app_server_data_dir / "options.json")


@pytest.fixture(scope="session")
def app_server(_app_server_data_dir):
    """The actual production entry point (`python app.py` → waitress) on a
    free port, with a throwaway DB and no SUPERVISOR_TOKEN so the
    background loop exits immediately and nothing talks to Home Assistant."""
    data_dir = _app_server_data_dir
    port = _free_port()
    env = os.environ.copy()
    env.pop("SUPERVISOR_TOKEN", None)
    env.update(
        COOP_DB_PATH=str(data_dir / "coop.db"),
        COOP_OPTIONS_PATH=str(data_dir / "options.json"),
        COOP_PORT=str(port),
    )

    proc = subprocess.Popen([sys.executable, "app.py"], cwd=APP_DIR, env=env)
    base_url = f"http://127.0.0.1:{port}"

    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/api/summary", timeout=1):
                break
        except (urllib.error.URLError, ConnectionError):
            if proc.poll() is not None:
                raise RuntimeError(f"app.py exited early with code {proc.returncode}")
            time.sleep(0.1)
    else:
        proc.terminate()
        raise RuntimeError("app.py did not start serving within 15s")

    yield base_url

    proc.terminate()
    proc.wait(timeout=10)
