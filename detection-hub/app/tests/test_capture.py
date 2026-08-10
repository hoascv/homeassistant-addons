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


# --- the cooldown --------------------------------------------------------------


def test_the_same_label_is_reported_once_per_window():
    cooldown = capture.Cooldown(seconds=30)
    assert cooldown.allows("drive", "person", now=100) is True
    assert cooldown.allows("drive", "person", now=110) is False
    assert cooldown.allows("drive", "person", now=131) is True


def test_cooldown_is_per_camera_and_per_label():
    """A car arriving while a person is already in view is news, and the back
    garden is not the driveway."""
    cooldown = capture.Cooldown(seconds=30)
    assert cooldown.allows("drive", "person", now=100) is True
    assert cooldown.allows("drive", "car", now=100) is True
    assert cooldown.allows("garden", "person", now=100) is True
    assert cooldown.allows("drive", "person", now=100) is False


def test_filter_keeps_only_what_is_off_cooldown():
    cooldown = capture.Cooldown(seconds=30)
    detections = [
        {"label": "person", "confidence": 0.9, "box": [0, 0, 1, 1]},
        {"label": "car", "confidence": 0.8, "box": [0, 0, 1, 1]},
    ]
    assert len(cooldown.filter("drive", detections, now=100)) == 2
    again = cooldown.filter("drive", detections, now=105)
    assert again == []


def test_a_person_standing_still_produces_one_event_not_sixty(street, street_later):
    """The database and the recorder both care about this more than the CPU
    does."""
    det = CountingDetector()
    seen = []
    frames = [street, street_later] * 30
    worker = capture.CameraWorker(
        "drive", "fake://", det, lambda *a: seen.append(a),
        max_fps=0, cooldown=capture.Cooldown(seconds=3600),
        capture_factory=lambda url: FakeCapture(frames),
    )
    _run_worker(worker)

    assert det.calls > 1, "the detector should have run repeatedly"
    assert len(seen) == 1, f"cooldown let {len(seen)} events through"


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
