"""The capture loop, driven by a fake camera rather than a real RTSP server.

The throttles are the whole point of this module — get one wrong and the add-on
still works, just at ten times the CPU or with a hundred times the rows. So each
one is tested for what it *prevents*, not only for what it allows.
"""
import os
import threading
import time

import cv2
import numpy as np
import pytest

import capture
import detector

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


class FakeCapture:
    """Stands in for cv2.VideoCapture: hands out a fixed list of frames, then
    reports the stream ended."""

    def __init__(self, frames, fail_after=None, opened=True):
        self.frames = frames
        self.fail_after = fail_after
        self._opened = opened
        self.index = 0
        self.released = False

    def isOpened(self):  # noqa: N802 - matching cv2's API
        return self._opened

    def read(self):
        if self.fail_after is not None and self.index >= self.fail_after:
            return False, None
        if self.index >= len(self.frames):
            return False, None
        frame = self.frames[self.index]
        self.index += 1
        return True, frame

    def release(self):
        self.released = True


class CountingDetector:
    """A detector that records how often it was asked, which is the number the
    motion gate exists to keep down."""

    def __init__(self, result=None):
        self.calls = 0
        self.result = result if result is not None else [
            {"label": "person", "confidence": 0.9, "box": [1, 2, 3, 4]}
        ]
        self.last_latency_ms = 5.0

    def detect(self, frame, confidence=0.6, labels=None):
        self.calls += 1
        return list(self.result), None


@pytest.fixture
def street():
    return cv2.imread(os.path.join(FIXTURES, "street.jpg"))


@pytest.fixture
def street_later():
    return cv2.imread(os.path.join(FIXTURES, "street_later.jpg"))


def _run_worker(worker, timeout=5):
    """Run until the fake stream is exhausted and the worker settles."""
    worker.start()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if worker.frames_seen and worker.state == "error":
            break
        time.sleep(0.02)
    worker.stop()
    worker.join(timeout=timeout)


# --- the motion gate, which is the CPU argument --------------------------------


def test_a_still_scene_never_reaches_the_detector(street):
    """The claim the whole design rests on. Twenty identical frames must cost
    twenty cheap comparisons and zero forward passes."""
    det = CountingDetector()
    seen = []
    worker = capture.CameraWorker(
        "drive", "fake://", det, lambda *a: seen.append(a),
        max_fps=0,  # no sampling throttle: isolate the gate
        capture_factory=lambda url: FakeCapture([street] * 20),
    )
    _run_worker(worker)

    assert worker.frames_considered == 20, "the sampler interfered with this test"
    # The first frame has no baseline and always passes — a camera pointed at a
    # parked car should report it rather than wait for it to move. The other
    # nineteen are identical and must not reach the model.
    assert det.calls == 1, f"the detector ran {det.calls} times on a still scene"
    assert len(seen) == 1


def test_real_movement_does_reach_the_detector(street, street_later):
    """The other half — a gate that never opens is not a saving."""
    det = CountingDetector()
    seen = []
    worker = capture.CameraWorker(
        "drive", "fake://", det, lambda *a: seen.append(a),
        max_fps=0,
        capture_factory=lambda url: FakeCapture([street, street_later]),
    )
    _run_worker(worker)

    assert det.calls >= 1
    assert seen, "a moving scene produced no detections"
    assert seen[0][0] == "drive"


def test_the_gate_threshold_is_respected(street, street_later):
    """Set it above what real movement scores and nothing gets through — which
    is how someone tunes out a swaying tree."""
    det = CountingDetector()
    worker = capture.CameraWorker(
        "drive", "fake://", det, lambda *a: None,
        max_fps=0, motion_threshold=0.99,
        capture_factory=lambda url: FakeCapture([street, street_later]),
    )
    _run_worker(worker)
    # Only the first frame, which has no baseline to compare against. The real
    # movement that follows scores ~0.054 and is correctly held back.
    assert det.calls == 1


# --- sampling ------------------------------------------------------------------


def test_every_frame_is_read_even_though_few_are_considered(street, street_later):
    """The decoder must be drained or it backs up and starts handing over stale
    frames. Sampling means considering fewer, not reading fewer."""
    det = CountingDetector()
    frames = [street, street_later] * 15
    worker = capture.CameraWorker(
        "drive", "fake://", det, lambda *a: None,
        max_fps=1,  # aggressive throttle
        capture_factory=lambda url: FakeCapture(frames),
    )
    _run_worker(worker)

    assert worker.frames_seen == 30, "frames were dropped instead of drained"
    assert worker.frames_considered < 30, "the sampling throttle did nothing"


# --- presence tracking: one event per object, not one per frame ---------------


def _det(label="car", box=(100, 100, 40, 30)):
    return {"label": label, "confidence": 0.9, "box": list(box)}


def test_iou_is_one_for_identical_boxes():
    assert capture._iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0


def test_iou_is_zero_for_disjoint_boxes():
    assert capture._iou([0, 0, 10, 10], [100, 100, 10, 10]) == 0.0


def test_iou_is_partial_for_overlap():
    # two 10x10 boxes offset by 5 in x: intersection 5x10=50, union 150.
    assert capture._iou([0, 0, 10, 10], [5, 0, 10, 10]) == pytest.approx(50 / 150)


def test_a_stationary_object_is_reported_once():
    """The reported bug: a parked car re-detected whenever the light shifts must
    log once, not on every frame the gate opens."""
    t = capture.PresenceTracker(absent_seconds=30)
    first = t.reconcile("drive", [_det()], now=0)
    assert len(first) == 1, "the arrival should be reported"
    # Re-detected, same spot, over the next few minutes — all suppressed.
    for when in (35, 90, 200, 400):
        assert t.reconcile("drive", [_det()], now=when) == []


def test_a_slightly_drifting_box_is_still_the_same_object():
    """Detector jitter nudges a parked car's box a few pixels; that is not a new
    car."""
    t = capture.PresenceTracker(absent_seconds=30)
    t.reconcile("drive", [_det(box=(100, 100, 40, 30))], now=0)
    assert t.reconcile("drive", [_det(box=(103, 98, 41, 30))], now=10) == []


def test_an_object_in_a_new_place_is_a_new_event():
    """A second car on the far side of the drive is news even while the first
    sits there."""
    t = capture.PresenceTracker(absent_seconds=30)
    t.reconcile("drive", [_det(box=(100, 100, 40, 30))], now=0)
    fresh = t.reconcile(
        "drive", [_det(box=(100, 100, 40, 30)), _det(box=(500, 100, 40, 30))], now=5
    )
    assert len(fresh) == 1 and fresh[0]["box"] == [500, 100, 40, 30]


def test_an_object_that_leaves_and_returns_is_logged_again():
    """Once it has been absent from analysed frames past the grace, its return
    is a fresh arrival."""
    t = capture.PresenceTracker(absent_seconds=30)
    t.reconcile("drive", [_det()], now=0)
    # Frames with other activity but no car: it is absent, and after the grace
    # the track is dropped.
    t.reconcile("drive", [_det(label="person", box=(0, 0, 20, 40))], now=10)
    t.reconcile("drive", [_det(label="person", box=(0, 0, 20, 40))], now=50)

    returning = t.reconcile("drive", [_det()], now=60)
    assert len(returning) == 1
    # Compared field by field rather than whole-dict: an arrival now also carries
    # the track_id identification needs, and that is not part of what the caller
    # asked about.
    assert returning[0]["label"] == "car"
    assert returning[0]["box"] == _det()["box"]
    assert returning[0]["track_id"] is not None


def test_a_single_missed_frame_does_not_count_as_leaving():
    """Occlusion or a dropped detection for one frame must not fabricate a
    departure and a re-arrival."""
    t = capture.PresenceTracker(absent_seconds=30)
    t.reconcile("drive", [_det()], now=0)
    t.reconcile("drive", [], now=5)               # briefly not seen
    assert t.reconcile("drive", [_det()], now=10) == []  # same car, still here


def test_cameras_are_tracked_independently():
    t = capture.PresenceTracker(absent_seconds=30)
    assert len(t.reconcile("drive", [_det()], now=0)) == 1
    assert len(t.reconcile("garden", [_det()], now=0)) == 1  # different camera


def test_labels_are_tracked_independently():
    """A car arriving where a person already stands is still news."""
    t = capture.PresenceTracker(absent_seconds=30)
    t.reconcile("drive", [_det(label="person", box=(100, 100, 40, 30))], now=0)
    fresh = t.reconcile(
        "drive",
        [_det(label="person", box=(100, 100, 40, 30)),
         _det(label="car", box=(100, 100, 40, 30))],
        now=5,
    )
    assert [d["label"] for d in fresh] == ["car"]


def test_a_parked_car_through_the_worker_logs_once(street, street_later):
    """End to end: the same frames that flooded the log now yield one event."""
    det = CountingDetector()
    seen = []
    worker = capture.CameraWorker(
        "drive", "fake://", det, lambda *a: seen.append(a),
        max_fps=0, presence=capture.PresenceTracker(absent_seconds=3600),
        capture_factory=lambda url: FakeCapture([street, street_later] * 30),
    )
    _run_worker(worker)

    assert det.calls > 1, "the detector should have run repeatedly"
    assert len(seen) == 1, f"presence tracking let {len(seen)} events through"


def test_disabling_collapse_logs_every_detection(street, street_later):
    """The toggle off: presence=None means no suppression, every detection
    through the gate is emitted."""
    det = CountingDetector()
    seen = []
    worker = capture.CameraWorker(
        "drive", "fake://", det, lambda *a: seen.append(a),
        max_fps=0, presence=None,
        capture_factory=lambda url: FakeCapture([street, street_later] * 5),
    )
    _run_worker(worker)

    assert len(seen) > 1, "with collapsing off, repeats should not be suppressed"


# --- failure handling ----------------------------------------------------------


def test_a_stream_that_will_not_open_is_reported_not_raised():
    worker = capture.CameraWorker(
        "drive", "rtsp://nowhere", CountingDetector(), lambda *a: None,
        capture_factory=lambda url: FakeCapture([], opened=False),
    )
    worker.start()
    time.sleep(0.3)
    worker.stop()
    worker.join(timeout=5)

    assert worker.state == "error"
    assert "could not open" in worker.detail


def test_the_capture_is_released_even_when_reading_fails(street):
    """A leaked VideoCapture holds an ffmpeg context and a socket; over a week
    of reconnects that is the whole file descriptor table."""
    captures = []

    def factory(url):
        cap = FakeCapture([street], fail_after=0)
        captures.append(cap)
        return cap

    worker = capture.CameraWorker(
        "drive", "fake://", CountingDetector(), lambda *a: None,
        capture_factory=factory,
    )
    worker.start()
    time.sleep(0.3)
    worker.stop()
    worker.join(timeout=5)

    assert captures and all(c.released for c in captures)


def test_a_storage_failure_does_not_kill_the_camera(street, street_later):
    """The next frame may well record fine, and a camera that stops watching
    because one write failed is a worse outcome than a missing row."""
    det = CountingDetector()

    def explode(*args):
        raise RuntimeError("disk full")

    worker = capture.CameraWorker(
        "drive", "fake://", det, explode,
        max_fps=0, log=lambda msg: None,
        capture_factory=lambda url: FakeCapture([street, street_later] * 3),
    )
    _run_worker(worker)

    assert worker.frames_seen == 6, "the loop stopped after a storage failure"


def test_a_detector_error_is_recorded_on_the_camera(street, street_later):
    """Observed while the stream is still healthy — otherwise the later
    "stream ended" overwrites it, which is correct but not what is being
    tested here."""
    class BrokenDetector:
        last_latency_ms = None

        def detect(self, frame, confidence=0.6, labels=None):
            return [], "model not found"

    worker = capture.CameraWorker(
        "drive", "fake://", BrokenDetector(), lambda *a: None,
        max_fps=0, log=lambda msg: None,
        capture_factory=lambda url: FakeCapture([street, street_later] * 200),
    )
    worker.start()
    deadline = time.time() + 5
    while time.time() < deadline and worker.state != "error":
        time.sleep(0.02)
    detail = worker.detail
    worker.stop()
    worker.join(timeout=5)

    assert "model not found" in detail


# --- the manager ---------------------------------------------------------------


def test_configure_starts_a_thread_per_camera(street):
    manager = capture.CaptureManager(
        CountingDetector(), lambda *a: None, log=lambda m: None
    )
    manager.configure(
        [{"id": "a", "url": "fake://a"}, {"id": "b", "url": "fake://b"}],
        capture_factory=lambda url: FakeCapture([street] * 3),
    )
    try:
        assert set(manager.workers) == {"a", "b"}
        assert all(w.is_alive() for w in manager.workers.values())
    finally:
        manager.stop()
    assert all(not w.is_alive() for w in manager.workers.values()) or not manager.workers


def test_reconfiguring_replaces_the_previous_set(street):
    manager = capture.CaptureManager(
        CountingDetector(), lambda *a: None, log=lambda m: None
    )
    factory = lambda url: FakeCapture([street] * 3)  # noqa: E731
    manager.configure([{"id": "a", "url": "fake://a"}], capture_factory=factory)
    manager.configure([{"id": "b", "url": "fake://b"}], capture_factory=factory)
    try:
        assert set(manager.workers) == {"b"}
    finally:
        manager.stop()


def test_a_camera_without_a_url_is_skipped():
    manager = capture.CaptureManager(
        CountingDetector(), lambda *a: None, log=lambda m: None
    )
    manager.configure([{"id": "a", "url": ""}])
    assert manager.workers == {}


def test_metrics_report_staleness_not_just_counts(street):
    """A thread that died while the web UI kept answering is the exact failure
    the status-file convention was written after; a frame count cannot show it."""
    manager = capture.CaptureManager(
        CountingDetector(), lambda *a: None, log=lambda m: None
    )
    manager.configure(
        [{"id": "a", "url": "fake://a"}],
        capture_factory=lambda url: FakeCapture([street] * 3),
    )
    time.sleep(0.3)
    try:
        metrics = manager.metrics()
        assert metrics["cameras"] == 1
        assert "seconds_since_last_frame" in metrics
        assert metrics["frames_seen"] >= 1
    finally:
        manager.stop()


def test_metrics_are_harmless_with_no_cameras():
    manager = capture.CaptureManager(CountingDetector(), lambda *a: None)
    assert manager.metrics() == {"cameras": 0}


# --- config parsing ------------------------------------------------------------


def test_cameras_are_parsed_one_per_line():
    parsed = capture.parse_cameras(
        "drive = rtsp://cam1/stream\ngarden=rtsp://user:pw@cam2:554/h264"
    )
    assert parsed == [
        {"id": "drive", "url": "rtsp://cam1/stream"},
        {"id": "garden", "url": "rtsp://user:pw@cam2:554/h264"},
    ]


def test_blank_lines_and_comments_are_ignored():
    parsed = capture.parse_cameras("\n# a comment\n\ndrive = rtsp://x\n")
    assert parsed == [{"id": "drive", "url": "rtsp://x"}]


def test_one_malformed_line_does_not_lose_the_others():
    """A typo in one camera should not silently stop the rest from being
    watched — which is what failing the whole parse would do."""
    parsed = capture.parse_cameras("drive = rtsp://x\nthis line is wrong\ngarden = rtsp://y")
    assert [c["id"] for c in parsed] == ["drive", "garden"]


def test_a_duplicate_name_is_dropped_rather_than_shadowing():
    parsed = capture.parse_cameras("drive = rtsp://first\ndrive = rtsp://second")
    assert parsed == [{"id": "drive", "url": "rtsp://first"}]


def test_urls_containing_equals_survive():
    """Query strings in RTSP URLs are common on cheap cameras."""
    parsed = capture.parse_cameras("cam = rtsp://host/stream?channel=1&sub=0")
    assert parsed[0]["url"] == "rtsp://host/stream?channel=1&sub=0"


def test_rtsp_is_forced_over_tcp():
    """UDP over wifi yields torn frames, which the motion gate reads as movement
    and the detector then wakes for, all night."""
    assert "rtsp_transport;tcp" in os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"]


# --- logging: positive confirmation and readable failures ---------------------


def test_connecting_logs_a_positive_line(street):
    """A working camera used to produce only silence — the same as one that
    never started. The connected line is the confirmation frames are arriving."""
    logs = []
    worker = capture.CameraWorker(
        "drive", "fake://", CountingDetector(), lambda *a: None,
        max_fps=0, log=logs.append,
        capture_factory=lambda url: FakeCapture([street] * 3),
    )
    _run_worker(worker)
    assert any("connected" in line for line in logs), logs


def test_the_connected_line_reports_the_resolution(street):
    """The camera's RTSP SDP does not advertise dimensions on this hardware, so
    the capture properties are the only place the resolution surfaces."""
    logs = []

    class SizedCapture(FakeCapture):
        def get(self, prop):
            import cv2
            return {cv2.CAP_PROP_FRAME_WIDTH: 640,
                    cv2.CAP_PROP_FRAME_HEIGHT: 480}.get(prop, 0)

    worker = capture.CameraWorker(
        "drive", "fake://", CountingDetector(), lambda *a: None,
        max_fps=0, log=logs.append,
        capture_factory=lambda url: SizedCapture([street] * 2),
    )
    _run_worker(worker)
    assert any("640x480" in line for line in logs), logs


def test_a_failed_open_explains_itself(street):
    """The raw failure is 'could not open stream', which alongside an ffmpeg
    401/406 above it is unactionable. The message now names the likely causes —
    including that a rejected password shows as a protocol error on this
    hardware, the trail that misled everyone."""
    logs = []
    worker = capture.CameraWorker(
        "drive", "rtsp://nope", CountingDetector(), lambda *a: None,
        log=logs.append,
        capture_factory=lambda url: FakeCapture([], opened=False),
    )
    worker.start()
    import time as _t
    _t.sleep(0.3)
    worker.stop()
    worker.join(timeout=5)

    failure = next((l for l in logs if "could not open" in l), "")
    assert "credentials" in failure and "reachable" in failure, failure


def test_explain_passes_other_errors_through():
    assert "stream ended" in capture._explain(OSError("stream ended"))
    assert capture._explain(OSError("x" * 500)).__len__() <= 200


# --- identifying who a person is ----------------------------------------------


class FakeIdentifier:
    """Stands in for the face models: says what it was told to say, and counts
    how often it was asked — which is the number the retry budget exists to
    bound."""

    def __init__(self, results=()):
        # One entry per call: a dict for "found a usable face", None for "no
        # usable face this frame". Runs out to None.
        self.results = list(results)
        self.calls = 0

    def find_in_person(self, frame, box, min_pixels=60):
        self.calls += 1
        if self.calls <= len(self.results):
            return self.results[self.calls - 1]
        return None


def _face(score=0.9):
    return {"usable": True, "embedding": [score] * 128, "face_box": [1, 2, 60, 60],
            "face_width": 60, "detect_score": score, "aligned": None, "reason": None}


def _worker(identifier, on_identity, presence=None, **kwargs):
    """A worker wired for identification, with recording stubbed to hand back
    row ids the way the real recorder does.

    `motion_threshold=0` because these tests feed identical blank frames and the
    gate would otherwise stop every frame after the first — see
    test_retries_only_happen_on_frames_that_pass_the_motion_gate, which is about
    that interaction rather than around it.
    """
    written = []

    def record(camera, detections, frame):
        ids = [100 + len(written) + i for i in range(len(detections))]
        written.extend(ids)
        return ids

    kwargs.setdefault("motion_threshold", 0)
    worker = capture.CameraWorker(
        "drive", "rtsp://x", CountingDetector(), record,
        presence=capture.PresenceTracker() if presence is None else presence,
        identifier=identifier, on_identity=on_identity, **kwargs,
    )
    return worker


def test_retries_only_happen_on_frames_that_pass_the_motion_gate():
    """A consequence worth knowing rather than discovering. Identification runs
    inside the detection pass, so a person who arrives and then stands perfectly
    still stops producing frames to look at. In practice somebody walking up to a
    door is moving the whole way, which is when the retries happen — but it means
    the budget is an upper bound, not a promise."""
    calls = []
    identifier = FakeIdentifier([None, _face()])
    worker = _worker(identifier, lambda *args: calls.append(args) or True,
                     motion_threshold=0.5)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)

    for _ in range(5):
        worker._consider(frame)

    assert identifier.calls == 1, "a frozen scene kept paying for face passes"
    assert calls == [], "and nothing was written on the strength of one look"


def test_a_person_is_identified_on_the_frame_they_arrive():
    calls = []
    identifier = FakeIdentifier([_face()])
    worker = _worker(identifier, lambda *args: calls.append(args) or True)

    worker._consider(np.zeros((100, 100, 3), dtype=np.uint8))

    assert len(calls) == 1
    camera, detection_id, found, final = calls[0]
    assert camera == "drive"
    assert detection_id == 100, "the pass must name the row the arrival wrote"
    assert found["usable"] is True


def test_a_face_that_appears_later_still_names_the_arrival_row():
    """The reason tracks have identity at all. A person is logged the moment
    they appear, which is usually before they turn towards the camera — without
    the retry they could never be named."""
    calls = []
    identifier = FakeIdentifier([None, None, _face()])
    worker = _worker(identifier, lambda *args: calls.append(args) or True)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)

    for _ in range(3):
        worker._consider(frame)

    assert identifier.calls == 3, "it must keep looking after the arrival frame"
    assert len(calls) == 1, "only the frame that found a face writes"
    assert calls[0][1] == 100, "and it updates the row written on arrival"


def test_looking_stops_once_the_budget_runs_out():
    """A person who never shows their face costs a fixed number of crops for the
    whole visit, not one per frame for as long as they stand there."""
    calls = []
    identifier = FakeIdentifier([])
    worker = _worker(identifier, lambda *args: calls.append(args) or True,
                     face_attempts=3)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)

    for _ in range(10):
        worker._consider(frame)

    assert identifier.calls == 3, "the budget was not respected"
    assert len(calls) == 1, "exactly one write, recording that looking is over"
    assert calls[0][3] is True, "and it is flagged as the final attempt"


def test_nothing_is_written_while_there_is_still_budget_left():
    """Otherwise a person standing still puts one row-update per frame into the
    change feed, which is the exact noise collapse_repeats exists to prevent."""
    calls = []
    worker = _worker(FakeIdentifier([]), lambda *args: calls.append(args) or True,
                     face_attempts=5)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)

    worker._consider(frame)
    worker._consider(frame)
    assert calls == [], "wrote before it had finished looking"


def test_a_resolved_person_is_not_looked_at_again():
    identifier = FakeIdentifier([_face(), _face(), _face()])
    worker = _worker(identifier, lambda *args: True)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)

    for _ in range(5):
        worker._consider(frame)

    assert identifier.calls == 1, "kept paying for somebody already identified"


def test_an_identifier_that_raises_does_not_end_the_camera_thread():
    """Same rule as recording: watching the camera outlives any one frame."""
    class Exploding:
        def find_in_person(self, *args, **kwargs):
            raise RuntimeError("model died")

    logged = []
    worker = _worker(Exploding(), lambda *args: True, log=logged.append)
    worker._consider(np.zeros((100, 100, 3), dtype=np.uint8))

    assert any("could not identify" in line for line in logged)


def test_without_a_tracker_identification_still_gets_one_look():
    """collapse_repeats off means no tracks, so there is nothing to retry
    against. Silently doing nothing under a supported setting would be a trap."""
    calls = []
    identifier = FakeIdentifier([_face()])
    worker = _worker(identifier, lambda *args: calls.append(args) or True,
                     presence=None)
    worker.presence = None
    worker._consider(np.zeros((100, 100, 3), dtype=np.uint8))

    assert len(calls) == 1
    assert calls[0][3] is True, "one look, so it is always the final one"


def test_identification_is_skipped_entirely_when_it_is_off():
    """The cost of this feature to somebody not using it is one `if`."""
    worker = _worker(None, None)
    worker._consider(np.zeros((100, 100, 3), dtype=np.uint8))  # must not raise
