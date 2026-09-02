"""End-to-end smoke tests: a real `python app.py` process, driven by a browser.

Run explicitly with `pytest app/tests_e2e` — the default run stays backend-only
via pytest.ini's testpaths, because these need a downloaded Chromium.

**Why these exist.** Every other front-end test here reads app.js as text and
asserts a substring is present. That proves a line was written; it proves
nothing about whether it runs. Electricity Tracker 1.18.0 shipped a call to a
helper that did not exist in that file, which threw during script evaluation
and stopped the whole script — every figure on the page showed a dash, and none
of the fifteen substring assertions over its app.js noticed, because none of
them opened the page.

So the assertion that matters most is `test_the_script_survives_being_loaded`:
no uncaught error, at all.
"""
import json
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
INGRESS_HEADERS = {"X-Remote-User-ID": "e2e-user"}
OPTIONS = {}


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def _data_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("e2e-data")


@pytest.fixture(scope="session")
def app_server(_data_dir):
    """The production entry point on a free port, with a throwaway database and
    no SUPERVISOR_TOKEN, so the background loop exits at once and nothing here
    reaches out to Home Assistant."""
    port = _free_port()
    env = os.environ.copy()
    env.pop("SUPERVISOR_TOKEN", None)
    env.update({
        "KNOWLEDGE_DB_PATH": str(_data_dir / "knowledge.db"),
        "KNOWLEDGE_OPTIONS_PATH": str(_data_dir / "options.json"),
        "KNOWLEDGE_PORT": str(port),
    })
    (_data_dir / "options.json").write_text(json.dumps(OPTIONS))

    proc = subprocess.Popen([sys.executable, "app.py"], cwd=APP_DIR, env=env)
    base_url = f"http://127.0.0.1:{port}"

    deadline = time.time() + 20
    request = urllib.request.Request(f"{base_url}/api/health", headers=INGRESS_HEADERS)
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(request, timeout=1):
                break
        except urllib.error.HTTPError:
            # An answer, even a 401, means it is listening. Caught separately
            # because HTTPError subclasses URLError, and swallowing it below
            # would make "up but refusing us" look like "not started".
            break
        except (urllib.error.URLError, ConnectionError):
            if proc.poll() is not None:
                raise RuntimeError(f"app.py exited early with code {proc.returncode}")
            time.sleep(0.1)
    else:
        proc.terminate()
        raise RuntimeError("app.py did not start serving within 20s")

    yield base_url
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Every request the browser makes has to look like it came through Home
    Assistant's ingress proxy — the document and the JSON calls alike."""
    return {**browser_context_args, "extra_http_headers": INGRESS_HEADERS}


@pytest.fixture
def page_errors(page):
    """Uncaught exceptions and console errors from the page. This is the
    fixture the suite exists for: a ReferenceError at the top of app.js is
    invisible to every other kind of test here and fatal to the page."""
    errors = []
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
    page.on("console", lambda msg: errors.append(f"console.{msg.type}: {msg.text}")
            if msg.type == "error" else None)
    return errors
