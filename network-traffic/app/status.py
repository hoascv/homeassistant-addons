"""The file the Add-on Watchdog reads.

Same convention as detection-hub's hass.py::write_status: a probe of this
add-on's own dashboard can only ever ask "does the page answer", which is
exactly as blind to a dead tcpdump process as detection-hub's page is to a
dead camera thread. So the fact worth knowing — is the capture actually
running, is the upload backlog growing — is written here instead, for the
watchdog to read on its own schedule.
"""
from __future__ import annotations

import json
import os
import time

STATUS_DIR = os.environ.get("PIPELINE_STATUS_DIR", "/share/pipeline-status")
SLUG = "network-traffic"


def write_status(ok, detail, metrics=None, status_dir=None):
    """Never raises: status reporting failing is not a reason to take the
    add-on down, and /share may legitimately be read-only or absent outside
    Supervisor.
    """
    directory = status_dir or STATUS_DIR
    try:
        os.makedirs(directory, exist_ok=True)
        payload = {
            "slug": SLUG,
            "kind": "collector",
            "ok": bool(ok),
            "detail": str(detail or ""),
            "updated_at": int(time.time()),
            "metrics": metrics or {},
        }
        target = os.path.join(directory, f"{SLUG}.json")
        # Written via a temporary file and renamed: the watchdog reads this on
        # its own schedule, and a half-written file is a parse error it would
        # report as a fault that was never actually there.
        tmp = f"{target}.tmp"
        with open(tmp, "w") as handle:
            json.dump(payload, handle)
        os.replace(tmp, target)
        return None
    except OSError as exc:
        return str(exc)
