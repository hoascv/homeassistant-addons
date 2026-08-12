"""Talking to Home Assistant, and to the Add-on Watchdog.

Three outward channels, and they are not interchangeable:

- **Events** for "a person just appeared". A detection is something that
  happened at an instant; modelling it as a state means an automation has to
  watch for a change and can miss two in a row. This add-on is the first thing
  in the repository to fire events rather than only set states.
- **States** for "how many today", "what was the last thing seen". These are
  what a dashboard shows and what the recorder keeps statistics for.
- **A status file** for the watchdog, because the thing most worth knowing —
  is the capture thread still alive — is exactly what an HTTP probe cannot see.
  The web UI answers perfectly well while a dead camera thread watches nothing,
  which is the failure the whole convention was written after.

stdlib urllib only, no requests: the dependency budget here is Flask, waitress
and opencv.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
HA_API_BASE = "http://supervisor/core/api"

STATUS_DIR = os.environ.get("PIPELINE_STATUS_DIR", "/share/pipeline-status")
SLUG = "detection-hub"

EVENT_TYPE = "detection_hub_detection"
# Who, as opposed to what. Separate because it is a separate fact arriving later
# — see fire_identified.
IDENTIFIED_EVENT_TYPE = "detection_hub_identified"


def api_request(method, path, payload=None, timeout=5):
    """(result, error) — never raises.

    A failure to reach Home Assistant must not break detection: the camera keeps
    watching and the rows keep landing, whether or not anyone was told.
    """
    if not SUPERVISOR_TOKEN:
        return None, "SUPERVISOR_TOKEN not set (not running under Supervisor)"
    req = urllib.request.Request(f"{HA_API_BASE}{path}", method=method)
    req.add_header("Authorization", f"Bearer {SUPERVISOR_TOKEN}")
    req.add_header("Content-Type", "application/json")
    data = json.dumps(payload).encode() if payload is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
            body = resp.read()
            return (json.loads(body) if body else None), None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}: {exc.read().decode('utf-8', 'ignore')[:200]}"
    except urllib.error.URLError as exc:
        return None, f"connection error: {exc.reason}"
    except Exception as exc:  # noqa: BLE001 - never let this reach a camera thread
        return None, str(exc)


def push_state(entity_id, state, attributes=None):
    _, error = api_request(
        "POST", f"/states/{entity_id}", {"state": state, "attributes": attributes or {}}
    )
    return error


def fire_event(camera, detection, snapshot_id=None, event_type=EVENT_TYPE):
    """Announce one detection.

    The payload is flat and small on purpose — an automation template reads
    `trigger.event.data.label`, and nesting that behind another key buys nothing
    but a longer template.
    """
    return api_request(
        "POST",
        f"/events/{event_type}",
        {
            "camera": camera,
            "label": detection["label"],
            "confidence": detection["confidence"],
            "box": detection["box"],
            "snapshot_id": snapshot_id,
            # Which named area it was in, or null. This is what lets an
            # automation fire on "a person on the Porch" rather than on a whole
            # camera — the reason zones carry names at all.
            "zone": detection.get("zone"),
        },
    )[1]


def fire_identified(camera, detection_id, person, person_id, score, state,
                    event_type=IDENTIFIED_EVENT_TYPE):
    """Announce who a person was — including when the answer is "nobody I know".

    A separate event from `fire_event` rather than a richer detection payload,
    because they are two different facts arriving at two different times. The
    detection fires on arrival, before any face has been seen; waiting for a name
    would delay every detection by up to `face_attempts` frames, and attaching a
    name to it later would mean the event lied when it was sent.

    Fired on the unknown case too, and that is the point: "an unrecognised person
    at 3am" is the automation worth having, and it is only expressible if the
    event exists.
    """
    return api_request(
        "POST",
        f"/events/{event_type}",
        {
            "camera": camera,
            "detection_id": detection_id,
            "person": person,
            "person_id": person_id,
            "score": score,
            "state": state,
        },
    )[1]


def notify(service, message, title="Detection Hub"):
    """Send a push notification through a notify.* service, or report why not.

    Same shape the trackers use: the service is stored without the `notify.`
    prefix, because that is how a Home Assistant user names it and re-adding the
    prefix here would only invite double-prefix mistakes.
    """
    service = (service or "").strip()
    if not service:
        return "no notify service configured"
    return api_request(
        "POST", f"/services/notify/{service}", {"message": message, "title": title}
    )[1]


def camera_snapshot(entity_id, timeout=10):
    """A still from a Home Assistant camera entity, as JPEG bytes.

    This is the `camera_proxy` endpoint rather than the WebSocket stream: one
    frame on demand is all this add-on wants, and it costs a single request.
    """
    if not SUPERVISOR_TOKEN:
        return None, "SUPERVISOR_TOKEN not set (not running under Supervisor)"
    req = urllib.request.Request(f"{HA_API_BASE}/camera_proxy/{entity_id}")
    req.add_header("Authorization", f"Bearer {SUPERVISOR_TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def _entity_suffix(name):
    """A camera name as an entity-id fragment: lowercase, underscores only."""
    return "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_")


def publish_people_sensors(prefix, person_totals, last):
    """Who has been recognised. Returns a list of errors, not an exception.

    Deliberately not a `binary_sensor.<person>_home` each. A camera sees an
    arrival; it never sees a departure, so a presence sensor built on this would
    be wrong for most of the day — and inventing a state the system cannot
    observe is the one thing this add-on does not do.
    """
    errors = []

    def push(entity, state, attrs):
        error = push_state(entity, state, attrs)
        if error:
            errors.append(f"{entity}: {error}")

    push(
        f"sensor.{prefix}_people_identified_today",
        sum(person_totals.values()),
        {
            "friendly_name": "People identified today",
            "icon": "mdi:account-check",
            "unit_of_measurement": "identifications",
            "state_class": "measurement",
            "by_person": person_totals,
        },
    )
    push(
        f"sensor.{prefix}_last_person",
        (last or {}).get("name") or "never",
        {
            "friendly_name": "Last person identified",
            "icon": "mdi:account",
            "camera": (last or {}).get("camera"),
            "score": (last or {}).get("person_score"),
            "at": (last or {}).get("detected_at"),
        },
    )
    return errors


def publish_sensors(prefix, totals, cameras, detector_status):
    """The states a dashboard reads. Returns a list of errors, not an exception.

    Numeric sensors carry `state_class: measurement`, which is what makes the
    recorder keep long-term statistics rather than only recent states — without
    it the history is there for a day and then gone.
    """
    errors = []

    def push(entity, state, attrs):
        error = push_state(entity, state, attrs)
        if error:
            errors.append(f"{entity}: {error}")

    push(
        f"sensor.{prefix}_detections_today",
        sum(totals.values()),
        {
            "friendly_name": "Detections today",
            "icon": "mdi:eye-check",
            "unit_of_measurement": "detections",
            "state_class": "measurement",
            "by_label": totals,
        },
    )

    ok = sum(1 for c in cameras if c.get("state") == "ok" and c.get("alive"))
    push(
        f"sensor.{prefix}_cameras_online",
        ok,
        {
            "friendly_name": "Cameras online",
            "icon": "mdi:cctv",
            "unit_of_measurement": "cameras",
            "state_class": "measurement",
            "configured": len(cameras),
            "detector_error": detector_status.get("error"),
        },
    )

    for camera in cameras:
        suffix = _entity_suffix(camera["id"])
        push(
            f"sensor.{prefix}_{suffix}_last_seen",
            camera.get("last_detection_at") or "never",
            {
                "friendly_name": f"{camera['id']} last detection",
                "icon": "mdi:motion-sensor",
                "state": camera.get("state"),
                "detail": camera.get("detail"),
                "frames_seen": camera.get("frames_seen"),
                "frames_detected": camera.get("frames_detected"),
            },
        )
        # A camera that is up but seeing nothing, and a camera that is down,
        # are different situations and an automation should be able to tell
        # them apart without parsing a detail string.
        push(
            f"binary_sensor.{prefix}_{suffix}_online",
            "on" if camera.get("state") == "ok" and camera.get("alive") else "off",
            {
                "friendly_name": f"{camera['id']} online",
                "device_class": "connectivity",
                "detail": camera.get("detail"),
            },
        )
    return errors


def write_status(ok, detail, metrics=None, status_dir=None):
    """The file the Add-on Watchdog reads.

    `updated_at` must be epoch seconds — the watchdog discards a report it
    cannot age, and treats anything over an hour old as failed regardless of
    what `ok` says.

    Never raises. Status reporting failing is not a reason to take the add-on
    down, and /share may legitimately be read-only or absent in development.
    """
    directory = status_dir or STATUS_DIR
    try:
        os.makedirs(directory, exist_ok=True)
        payload = {
            "slug": SLUG,
            "kind": "detector",
            "ok": bool(ok),
            "detail": str(detail or ""),
            "updated_at": int(time.time()),
            "metrics": metrics or {},
        }
        target = os.path.join(directory, f"{SLUG}.json")
        # Written via a temporary file and renamed: the watchdog reads this on
        # its own schedule and a half-written file is a parse error it would
        # report as a fault.
        temporary = f"{target}.tmp"
        with open(temporary, "w") as handle:
            json.dump(payload, handle)
        os.replace(temporary, target)
        return None
    except OSError as exc:
        return str(exc)
