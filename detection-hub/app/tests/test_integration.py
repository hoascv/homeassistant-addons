"""The seams between modules, where each half is tested and the join is not.

A detection has to reach three places from one call: the database, the change
feed, and Home Assistant. Each of those works in isolation; these check that the
recording path actually drives all three.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import cv2
import numpy as np
import pytest

import app as hub
import capture
import hass
import store


@pytest.fixture
def ha_calls(monkeypatch):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            calls.append({"path": self.path, "body": json.loads(body) if body else None})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(hass, "HA_API_BASE", f"http://127.0.0.1:{server.server_address[1]}")
    monkeypatch.setattr(hass, "SUPERVISOR_TOKEN", "test-token")
    yield calls
    server.shutdown()
    thread.join(timeout=2)


def _frame():
    return np.full((120, 160, 3), 90, dtype=np.uint8)


DETECTIONS = [{"label": "person", "confidence": 0.88, "box": [1, 2, 3, 4]}]


# --- the recording path -------------------------------------------------------


def test_recording_stores_fires_and_feeds(client, db_path, set_options, ha_calls):
    """One call, three destinations."""
    set_options(ha_events_enabled=True)
    conn = store.connect(db_path, actor="user")
    try:
        snapshot_id, detection_ids = hub.record(
            conn, "drive", DETECTIONS, _frame(), kind="rtsp"
        )
    finally:
        conn.close()

    # 1. stored, with its image — and the row ids come back, which is what lets
    # identification name this detection a few frames later.
    assert snapshot_id
    assert detection_ids == [1]
    check = store.connect(db_path)
    assert check.execute("SELECT COUNT(*) FROM detections").fetchone()[0] == 1
    assert check.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1

    # 2. in the change feed, attributed to the camera thread
    feed = store.changes(check)["changes"]
    assert any(c["table"] == "detections" and c["op"] == "I" for c in feed)

    # 3. announced to Home Assistant
    events = [c for c in ha_calls if c["path"] == "/events/detection_hub_detection"]
    assert len(events) == 1
    assert events[0]["body"]["camera"] == "drive"
    assert events[0]["body"]["snapshot_id"] == snapshot_id
    check.close()


def test_one_event_per_detection_not_per_frame(client, db_path, set_options, ha_calls):
    set_options(ha_events_enabled=True)
    conn = store.connect(db_path, actor="user")
    try:
        hub.record(conn, "drive", DETECTIONS * 3, _frame())
    finally:
        conn.close()

    events = [c for c in ha_calls if c["path"].startswith("/events/")]
    assert len(events) == 3


def test_events_can_be_turned_off(client, db_path, set_options, ha_calls):
    """Someone who only wants the lakehouse should not be paying for an HTTP
    call per detection."""
    set_options(ha_events_enabled=False)
    conn = store.connect(db_path, actor="user")
    try:
        hub.record(conn, "drive", DETECTIONS, _frame())
    finally:
        conn.close()

    assert [c for c in ha_calls if c["path"].startswith("/events/")] == []
    check = store.connect(db_path)
    assert check.execute("SELECT COUNT(*) FROM detections").fetchone()[0] == 1
    check.close()


def test_a_dead_home_assistant_does_not_lose_the_detection(
    client, db_path, set_options, monkeypatch
):
    """The row matters more than the announcement. If HA is unreachable the
    detection is still stored and still reaches the lakehouse."""
    set_options(ha_events_enabled=True)
    monkeypatch.setattr(hass, "SUPERVISOR_TOKEN", "t")
    monkeypatch.setattr(hass, "HA_API_BASE", "http://127.0.0.1:1")

    conn = store.connect(db_path, actor="user")
    try:
        hub.record(conn, "drive", DETECTIONS, _frame())
    finally:
        conn.close()

    check = store.connect(db_path)
    assert check.execute("SELECT COUNT(*) FROM detections").fetchone()[0] == 1
    check.close()


# --- camera thread to database ------------------------------------------------


def test_a_camera_thread_writes_through_its_own_connection(
    client, db_path, set_options, ha_calls
):
    """sqlite3 connections have thread affinity. A camera thread borrowing a
    request's connection is the classic way this breaks in production and never
    in tests."""
    set_options(ha_events_enabled=False)
    hub._on_camera_detections("drive", DETECTIONS, _frame())

    check = store.connect(db_path)
    row = check.execute("SELECT * FROM detections").fetchone()
    assert row["camera"] == "drive"
    # Attributed to the camera, not to a user — the lakehouse keeps the
    # distinction and an automated row is not someone editing something.
    assert store.changes(check)["changes"][-1]["actor"] == "camera"
    check.close()


def test_camera_rows_merge_stored_history_with_live_state(client, db_path, set_options):
    """The threads know liveness, the database knows history, and neither alone
    answers "is the driveway camera working"."""
    set_options(ha_events_enabled=False)
    hub._on_camera_detections("drive", DETECTIONS, _frame())

    rows = hub._camera_rows()
    drive = next(r for r in rows if r["id"] == "drive")
    assert drive["frames_detected"] == 1        # from the database
    assert "alive" in drive                     # from the thread view


# --- the status file the watchdog reads ---------------------------------------


def test_publish_writes_a_status_file_the_watchdog_can_age(
    client, db_path, set_options, tmp_path, monkeypatch
):
    monkeypatch.setattr(hass, "STATUS_DIR", str(tmp_path))
    set_options(ha_sensors_enabled=False)

    conn = store.connect(db_path, actor="automation")
    try:
        hub._publish(conn)
    finally:
        conn.close()

    with open(tmp_path / "detection-hub.json") as handle:
        payload = json.load(handle)
    assert isinstance(payload["updated_at"], int)
    assert payload["ok"] is True
    assert "no cameras configured" in payload["detail"]


def test_a_configured_camera_that_is_not_streaming_reports_not_ok(
    client, db_path, set_options, tmp_path, monkeypatch
):
    """The failure an HTTP probe cannot see: the web UI answers perfectly well
    while a dead thread watches nothing. This is why the status file exists."""
    monkeypatch.setattr(hass, "STATUS_DIR", str(tmp_path))
    set_options(ha_sensors_enabled=False, cameras="drive = rtsp://nowhere/stream")

    conn = store.connect(db_path, actor="automation")
    try:
        hub._publish(conn)
    finally:
        conn.close()

    with open(tmp_path / "detection-hub.json") as handle:
        payload = json.load(handle)
    assert payload["ok"] is False
    assert "0/1 cameras streaming" in payload["detail"]


def test_a_broken_detector_reports_not_ok(
    client, db_path, set_options, tmp_path, monkeypatch
):
    monkeypatch.setattr(hass, "STATUS_DIR", str(tmp_path))
    set_options(ha_sensors_enabled=False, model_path=str(tmp_path / "absent.onnx"))
    hub.get_detector().load()

    conn = store.connect(db_path, actor="automation")
    try:
        hub._publish(conn)
    finally:
        conn.close()

    with open(tmp_path / "detection-hub.json") as handle:
        payload = json.load(handle)
    assert payload["ok"] is False
    assert "detector unavailable" in payload["detail"]


# --- configuration reaches the capture layer ----------------------------------


def test_camera_options_are_parsed_and_clamped(client, set_options):
    set_options(
        cameras="drive = rtsp://x/1\ngarden = rtsp://y/2",
        max_fps=99, motion_threshold=5, cooldown_seconds=-4,
    )
    assert [c["id"] for c in hub.get_cameras()] == ["drive", "garden"]
    options = hub.get_capture_options()
    assert options["max_fps"] == 30          # clamped to the schema's ceiling
    assert options["motion_threshold"] == 1
    assert options["cooldown_seconds"] == 0


def test_nonsense_capture_options_fall_back(client, set_options):
    set_options(max_fps="fast", motion_threshold=None, cooldown_seconds="soon")
    options = hub.get_capture_options()
    assert options == {"max_fps": 2.0, "motion_threshold": 0.005,
                       "cooldown_seconds": 30, "collapse_repeats": True}


# --- identifying a person, end to end -----------------------------------------


def _enrolled(db_path, name="Alice", vector=None):
    """A person with one print, written the way the enrolment route writes it."""
    import faces

    values = vector if vector is not None else [1.0] + [0.0] * 127
    conn = store.connect(db_path, actor="user")
    try:
        person_id = store.add_person(conn, name)
        store.add_face_print(conn, person_id,
                             faces.pack_embedding(np.array(values, dtype=np.float32)),
                             faces.RECOGNISER_NAME, "snapshot")
        conn.commit()
        return person_id
    finally:
        conn.close()


def _found(vector):
    return {"usable": True, "embedding": np.array(vector, dtype=np.float32),
            "face_box": [0, 0, 70, 70], "face_width": 70, "detect_score": 0.9,
            "aligned": None, "reason": None}


def test_a_recognised_face_names_the_detection_and_tells_home_assistant(
        db_path, set_options, ha_calls):
    """The whole feature in one path: a face becomes a name on the row that was
    written when the person arrived, and an event an automation can act on."""
    set_options(identify_people=True, ha_events_enabled=True)
    person_id = _enrolled(db_path)

    conn = store.connect(db_path, actor="camera")
    try:
        ids = store.record_detections(conn, "drive", DETECTIONS)
        conn.commit()
    finally:
        conn.close()

    assert hub._on_face_candidate("drive", ids[0], _found([1.0] + [0.0] * 127), False) is True

    check = store.connect(db_path)
    try:
        row = store.recent_detections(check)[0]
        assert row["person"] == "Alice"
        assert row["face_state"] == "matched"
        assert row["person_score"] > 0.99
    finally:
        check.close()

    events = [c for c in ha_calls if c["path"] == "/events/detection_hub_identified"]
    assert len(events) == 1
    assert events[0]["body"]["person"] == "Alice"
    assert events[0]["body"]["person_id"] == person_id
    assert events[0]["body"]["state"] == "matched"


def test_a_stranger_is_recorded_as_unrecognised_and_still_announced(
        db_path, set_options, ha_calls):
    """The event fires on the unknown case too, because "somebody I don't know at
    3am" is the automation worth having."""
    set_options(identify_people=True, ha_events_enabled=True)
    _enrolled(db_path)

    conn = store.connect(db_path, actor="camera")
    try:
        ids = store.record_detections(conn, "drive", DETECTIONS)
        conn.commit()
    finally:
        conn.close()

    # Orthogonal to the enrolled vector: cosine 0, nowhere near any threshold.
    hub._on_face_candidate("drive", ids[0], _found([0.0, 1.0] + [0.0] * 126), False)

    check = store.connect(db_path)
    try:
        row = store.recent_detections(check)[0]
        assert row["person"] is None, "a stranger was given somebody's name"
        assert row["face_state"] == "unknown"
        assert row["person_score"] is not None, "the score it did reach is the tuning signal"
    finally:
        check.close()

    events = [c for c in ha_calls if c["path"] == "/events/detection_hub_identified"]
    assert events[0]["body"]["person"] is None
    assert events[0]["body"]["state"] == "unknown"


def test_a_person_whose_face_was_never_visible_says_exactly_that(db_path, set_options):
    """Distinct from "unrecognised": no face at all is a camera problem, an
    unrecognised face is an enrolment problem, and they have different fixes."""
    set_options(identify_people=True)
    conn = store.connect(db_path, actor="camera")
    try:
        ids = store.record_detections(conn, "drive", DETECTIONS)
        conn.commit()
    finally:
        conn.close()

    # Not final: still looking, so nothing is written yet.
    assert hub._on_face_candidate("drive", ids[0], None, False) is False
    check = store.connect(db_path)
    try:
        assert store.recent_detections(check)[0]["face_state"] is None
    finally:
        check.close()

    # Final attempt: the terminal state is recorded, once.
    assert hub._on_face_candidate("drive", ids[0], None, True) is True
    check = store.connect(db_path)
    try:
        assert store.recent_detections(check)[0]["face_state"] == "no_face"
    finally:
        check.close()


def test_a_close_call_between_two_people_names_neither(db_path, set_options):
    """The margin rule, end to end. Two people who score almost the same is a
    coin flip, and this add-on does not flip coins with somebody's name."""
    set_options(identify_people=True, face_match_threshold=0.2, face_margin=0.05)
    _enrolled(db_path, "Alice", [1.0, 0.35] + [0.0] * 126)
    _enrolled(db_path, "Bob", [1.0, 0.36] + [0.0] * 126)

    conn = store.connect(db_path, actor="camera")
    try:
        ids = store.record_detections(conn, "drive", DETECTIONS)
        conn.commit()
    finally:
        conn.close()

    hub._on_face_candidate("drive", ids[0], _found([1.0] + [0.0] * 127), True)

    check = store.connect(db_path)
    try:
        row = store.recent_detections(check)[0]
        assert row["person"] is None
        assert row["face_state"] == "unknown"
    finally:
        check.close()


def test_the_threshold_is_read_fresh_so_tuning_does_not_need_a_restart(
        db_path, set_options):
    """The number an operator actually turns. Making them restart to try 0.42
    instead of 0.45 turns a two-minute experiment into an afternoon."""
    set_options(identify_people=True, face_match_threshold=0.9)
    _enrolled(db_path, "Alice", [1.0, 1.0] + [0.0] * 126)

    conn = store.connect(db_path, actor="camera")
    try:
        ids = store.record_detections(conn, "drive", DETECTIONS * 2)
        conn.commit()
    finally:
        conn.close()

    # cosine here is ~0.707: below 0.9, above 0.5.
    hub._on_face_candidate("drive", ids[0], _found([1.0] + [0.0] * 127), True)
    set_options(identify_people=True, face_match_threshold=0.5)
    hub._on_face_candidate("drive", ids[1], _found([1.0] + [0.0] * 127), True)

    check = store.connect(db_path)
    try:
        rows = {r["id"]: r for r in store.recent_detections(check)}
        assert rows[ids[0]]["face_state"] == "unknown"
        assert rows[ids[1]]["person"] == "Alice"
    finally:
        check.close()
