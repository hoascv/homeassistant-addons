"""End-to-end smoke tests: a real `python app.py` process, driven by a browser.

Run explicitly with `pytest app/tests_e2e` — the default run stays backend-only
via pytest.ini's testpaths, because these need a downloaded Chromium.

**Why these exist.** Every other front-end test in this add-on reads app.js as
text and asserts a substring is present. That proves a line was written; it
proves nothing about whether it runs. Version 1.18.0 shipped a call to an
`el()` helper that does not exist in this file, which threw during script
evaluation and stopped the whole script — every figure on the dashboard showed
a dash and no test noticed, because none of them opened the page.

So the one assertion that matters most here is the one in
`test_the_script_survives_being_loaded`: no uncaught error, at all.
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
    reaches out to Home Assistant, Eloverblik or Easee."""
    port = _free_port()
    env = os.environ.copy()
    env.pop("SUPERVISOR_TOKEN", None)
    env.update(
        ELECTRICITY_DB_PATH=str(_data_dir / "electricity.db"),
        ELECTRICITY_OPTIONS_PATH=str(_data_dir / "options.json"),
        ELECTRICITY_PORT=str(port),
    )

    # Easee turned on before the first request. The charging history card —
    # and the trips section inside it — hides itself when it is off, so
    # without this the browser cannot reach half of what is worth smoke
    # testing. No credentials that work: the cloud calls fail and are logged,
    # which is exactly the state of an add-on configured but offline.
    options = _data_dir / "options.json"
    options.write_text(json.dumps({
        "price_area": "DK2",
        "easee_enabled": True,
        "easee_username": "e2e@example.com",
        "easee_password": "not-a-real-password",
        "easee_charger_id": "EH000000",
    }))

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
    Assistant's ingress proxy — the document, the JSON calls and the images
    alike. Without it the app answers 401 to all of them and the suite fails
    with rendering errors that say nothing about authentication."""
    return {**browser_context_args, "extra_http_headers": INGRESS_HEADERS}


@pytest.fixture
def page_errors(page):
    """Uncaught exceptions and console errors from the page.

    This is the fixture the whole suite exists for. A ReferenceError at the top
    of app.js is invisible to every other kind of test in this repo and fatal
    to the page.
    """
    errors = []
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
    page.on("console", lambda msg: errors.append(f"console.{msg.type}: {msg.text}")
            if msg.type == "error" else None)
    return errors
