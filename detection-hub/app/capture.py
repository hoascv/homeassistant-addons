"""Watching cameras without spending the whole CPU on it.

The shape of this file is dictated by one number. A forward pass costs ~15ms, so
a single 15fps stream fed straight to the detector is a quarter of a core spent
almost entirely re-deciding that nothing has changed. Two streams at a higher
frame rate is a whole core. So three throttles sit between a camera and the
model, in increasing order of cleverness:

1. **Sampling** — read every frame the stream offers (they must be drained or
   the decoder backs up) but only *consider* `max_fps` of them.
2. **The motion gate** — compare a considered frame against the last one at
   320px grey. Unchanged frames never reach the detector. This is what makes the
   whole thing affordable, and it is why it has its own tests.
3. **The cooldown** — a person standing in view is one event, not sixty. This
   protects the database and Home Assistant's recorder rather than the CPU.

Threads rather than asyncio: `cv2.VideoCapture.read()` is a blocking C call that
releases the GIL, which is exactly the shape a thread suits and asyncio does not.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime

import detector

# Force TCP for RTSP. The default lets ffmpeg negotiate UDP, which over wifi
# drops packets and yields torn frames that read as motion — the gate then wakes
# the detector for artefacts, all night.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

# How long to wait after a read fails before reopening, and the ceiling on that
# backoff. A camera rebooting should not be retried in a tight loop.
RECONNECT_DELAY_SECONDS = 2
RECONNECT_MAX_SECONDS = 60


def open_stream(url):
    """Open a video source. Separated so tests can substitute a fake."""
    import cv2

    return cv2.VideoCapture(url)


class Cooldown:
    """One event per camera per label per window.

    Without this a person who stands still produces a detection every sampled
    frame: hundreds of near-identical rows, hundreds of snapshots, and an
    automation that fires until they move away.
    """

    def __init__(self, seconds=30):
        self.seconds = seconds
        self._last = {}
        self._lock = threading.Lock()

    def allows(self, camera, label, now=None):
        now = now if now is not None else time.monotonic()
        key = (camera, label)
        with self._lock:
            last = self._last.get(key)
            if last is not None and now - last < self.seconds:
                return False
            self._last[key] = now
            return True

    def filter(self, camera, detections, now=None):
        """Keep only the detections whose label is off cooldown.

        Applied per label rather than per frame: a car arriving while a person
        is already in view is news, even though the person is not.
        """
        return [d for d in detections if self.allows(camera, d["label"], now)]


class CameraWorker(threading.Thread):
    """One camera, one thread: read, gate, detect, hand off.

    Owns no database connection. It calls back with detections and lets the
    caller decide what to persist, which keeps sqlite's thread affinity someone
    else's problem and makes this testable with no storage at all.
    """

    def __init__(
        self,
        camera_id,
        url,
        det,
        on_detections,
        *,
        max_fps=2,
        motion_threshold=0.005,
        confidence=0.6,
        labels=None,
        cooldown=None,
        capture_factory=open_stream,
        log=print,
    ):
        super().__init__(name=f"camera-{camera_id}", daemon=True)
        self.camera_id = camera_id
        self.url = url
        self.detector = det
        self.on_detections = on_detections
        # 0 means "consider every frame": no sampling throttle. Useful on a
        # low-rate source (a doorbell snapshot feed) where every frame is
        # already meaningful, and it is what the gate tests need to isolate the
        # gate from the sampler.
        self.max_fps = max(0.0, float(max_fps))
        self.motion_threshold = float(motion_threshold)
        self.confidence = confidence
        self.labels = labels
        self.cooldown = cooldown or Cooldown()
        self.capture_factory = capture_factory
        self.log = log

        # NOT `_stop`: threading.Thread._stop() is an internal method that
        # join() calls, and shadowing it with an Event makes every join raise
        # "'Event' object is not callable" — a camera you cannot stop.
        self._stopped = threading.Event()
        self._previous = None
        self.state = "starting"
        self.detail = ""
        self.frames_seen = 0
        self.frames_considered = 0
        self.frames_detected = 0
        self.last_frame_at = None
        self.last_detection_at = None

    def stop(self):
        self._stopped.set()

    # -- the loop --------------------------------------------------------------

    def run(self):
        backoff = RECONNECT_DELAY_SECONDS
        while not self._stopped.is_set():
            capture = None
            try:
                capture = self.capture_factory(self.url)
                if not capture or not capture.isOpened():
                    raise OSError("could not open stream")
                self.state = "ok"
                self.detail = "streaming"
                backoff = RECONNECT_DELAY_SECONDS
                self._read_until_failure(capture)
            except Exception as exc:  # noqa: BLE001 - a camera failing is routine
                self.state = "error"
                self.detail = str(exc)[:200]
                self.log(f"camera {self.camera_id}: {self.detail}")
            finally:
                if capture is not None:
                    try:
                        capture.release()
                    except Exception:  # noqa: BLE001
                        pass
            if self._stopped.is_set():
                break
            # A camera that is rebooting, or a URL that is wrong, must not be
            # retried in a tight loop — that is its own kind of CPU burn.
            self._stopped.wait(backoff)
            backoff = min(RECONNECT_MAX_SECONDS, backoff * 2)

    def _read_until_failure(self, capture):
        interval = (1.0 / self.max_fps) if self.max_fps else 0.0
        next_consider = 0.0
        while not self._stopped.is_set():
            ok, frame = capture.read()
            if not ok or frame is None:
                raise OSError("stream ended")
            self.frames_seen += 1
            self.last_frame_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

            # Every frame is read — a decoder that is not drained backs up and
            # eventually hands you stale frames — but only some are considered.
            now = time.monotonic()
            if now < next_consider:
                continue
            next_consider = now + interval
            self.frames_considered += 1
            self._consider(frame)

    def _consider(self, frame):
        score = detector.motion_score(self._previous, frame)
        self._previous = frame
        if score < self.motion_threshold:
            return

        detections, error = self.detector.detect(
            frame, confidence=self.confidence, labels=self.labels
        )
        if error:
            self.state = "error"
            self.detail = error
            return

        fresh = self.cooldown.filter(self.camera_id, detections)
        if not fresh:
            return

        self.frames_detected += 1
        self.last_detection_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        try:
            self.on_detections(self.camera_id, fresh, frame)
        except Exception as exc:  # noqa: BLE001 - a storage failure is not a
            # reason to stop watching the camera; the next frame may well work.
            self.log(f"camera {self.camera_id}: could not record: {exc}")

    # -- reporting -------------------------------------------------------------

    def status(self):
        return {
            "id": self.camera_id,
            "state": self.state,
            "detail": self.detail,
            "alive": self.is_alive(),
            "frames_seen": self.frames_seen,
            "frames_considered": self.frames_considered,
            "frames_detected": self.frames_detected,
            "last_frame_at": self.last_frame_at,
            "last_detection_at": self.last_detection_at,
        }


class CaptureManager:
    """Owns the camera threads and the configuration they came from.

    Reconfiguration stops everything and starts again rather than diffing. The
    set of cameras changes when someone edits the add-on's options, which is
    rare and already involves a restart in this repo's conventions — a diffing
    implementation would be more code for a case that barely arises.
    """

    def __init__(self, det, on_detections, log=print):
        self.detector = det
        self.on_detections = on_detections
        self.log = log
        self.workers = {}
        self._lock = threading.Lock()

    def configure(self, cameras, **options):
        """(re)start workers for `cameras`, a list of {"id", "url"} dicts."""
        with self._lock:
            self._stop_all()
            cooldown = Cooldown(options.pop("cooldown_seconds", 30))
            for camera in cameras:
                if not camera.get("url"):
                    continue
                worker = CameraWorker(
                    camera["id"],
                    camera["url"],
                    self.detector,
                    self.on_detections,
                    cooldown=cooldown,
                    log=self.log,
                    **options,
                )
                self.workers[camera["id"]] = worker
                worker.start()
                self.log(f"camera {camera['id']}: watching")

    def _stop_all(self):
        for worker in self.workers.values():
            worker.stop()
        for worker in self.workers.values():
            worker.join(timeout=5)
        self.workers = {}

    def stop(self):
        with self._lock:
            self._stop_all()

    def status(self):
        return [worker.status() for worker in self.workers.values()]

    def metrics(self):
        """What the watchdog's status file reports.

        `seconds_since_last_frame` is the one that matters: a thread that died
        while the web UI kept answering is precisely the failure the status-file
        convention was written after, and a frame count alone cannot show it.
        """
        statuses = self.status()
        if not statuses:
            return {"cameras": 0}

        newest = [s["last_frame_at"] for s in statuses if s["last_frame_at"]]
        since = None
        if newest:
            latest = max(newest)
            since = int(
                (datetime.now() - datetime.strptime(latest, "%Y-%m-%dT%H:%M:%S"))
                .total_seconds()
            )
        return {
            "cameras": len(statuses),
            "cameras_ok": sum(1 for s in statuses if s["state"] == "ok" and s["alive"]),
            "frames_seen": sum(s["frames_seen"] for s in statuses),
            "frames_detected": sum(s["frames_detected"] for s in statuses),
            "seconds_since_last_frame": since,
            "detector_latency_ms": self.detector.last_latency_ms,
        }


def parse_cameras(raw):
    """Parse the `cameras` option: one `name = url` per line.

    A list-of-objects schema would be tidier in config.yaml, but this is edited
    in a textarea in the Supervisor UI and a typo in nested YAML is a much worse
    experience than a typo in `name = url`. Lines without an `=` are skipped
    rather than failing the whole configuration — one bad line should not stop
    the other cameras.
    """
    cameras, seen = [], set()
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, url = line.partition("=")
        name, url = name.strip(), url.strip()
        if not name or not url or name in seen:
            continue
        seen.add(name)
        cameras.append({"id": name, "url": url})
    return cameras
