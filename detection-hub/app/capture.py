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
import zones

# Force TCP for RTSP. The default lets ffmpeg negotiate UDP, which over wifi
# drops packets and yields torn frames that read as motion — the gate then wakes
# the detector for artefacts, all night.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

# How long to wait after a read fails before reopening, and the ceiling on that
# backoff. A camera rebooting should not be retried in a tight loop.
RECONNECT_DELAY_SECONDS = 2
RECONNECT_MAX_SECONDS = 60

# Distinguishes "no tracker was passed, use the default" from "a tracker was
# explicitly disabled (None)".
_UNSET = object()


def open_stream(url):
    """Open a video source. Separated so tests can substitute a fake."""
    import cv2

    return cv2.VideoCapture(url)


def _explain(exc):
    """Turn a capture failure into something a person can act on.

    OpenCV's ffmpeg backend prints the telling line — `401`, `406`, `Connection
    refused` — to its own stderr, out of reach of this exception, which only ever
    says "could not open stream". So the raw error is kept and a hint appended
    for the causes worth naming: a wrong password reads as a protocol fault (this
    hardware answers 406, not 401), which is exactly the trail that misleads.
    """
    text = str(exc)[:200]
    if "could not open stream" in text:
        return (
            f"{text} — check the URL, the credentials (a rejected password can "
            "show as an ffmpeg 401/406 line above), and that the camera is "
            "reachable on the network"
        )
    return text


# How much two boxes must overlap to be the same physical object between frames.
# 0.3 rather than 0.5: at a couple of frames a second a walking person moves
# enough that consecutive boxes only partly overlap, and treating that as two
# people would defeat the point. A parked car overlaps itself almost completely.
SAME_OBJECT_IOU = 0.3


def _iou(a, b):
    """Intersection-over-union of two [x, y, w, h] boxes, 0..1."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


class PresenceTracker:
    """One event per object, not one per frame it happens to be seen.

    The naive rule — suppress a label for N seconds after logging it — re-fires
    the moment N elapses, so a parked car floods the log: it does not move, but
    the wind, the light or the camera's own exposure keeps opening the motion
    gate, the detector keeps finding the car sitting there, and every re-find
    past the window reads as new. Time cannot tell "same car, still parked" from
    "a different car arrived"; position can.

    So a detection is matched to a remembered one of the same label by box
    overlap. A match is the same object still present — its position is refreshed
    and it is suppressed, however long it stays. A detection that matches nothing
    is a genuine arrival and is emitted. A remembered object that stops appearing
    in analysed frames is expired after `absent_seconds`, so when it finally
    leaves and something new turns up, that is logged.

    Expiry is driven by *absence from frames the detector looked at*, not by wall
    clock, which is what makes it robust on a still scene: however rarely the
    gate opens, a car that is present is in every frame the detector examines, so
    it is never wrongly declared gone and never re-logged.
    """

    def __init__(self, absent_seconds=30, iou_threshold=SAME_OBJECT_IOU):
        self.absent_seconds = absent_seconds
        self.iou_threshold = iou_threshold
        self._tracks = {}  # camera -> [{label, box, missed_since, track_id, ...}]
        self._lock = threading.Lock()
        # Tracks used to be anonymous, which was fine while the only question was
        # "is this the same object". Identification asks a second one — "have we
        # named this one yet" — and that needs something to hang an answer on.
        self._next_track_id = 1

    def reconcile(self, camera, detections, now=None):
        """Return only the detections that are new arrivals this frame.

        `detections` is the whole frame's set, so absence is observable: a track
        with no match here has not been seen, and after the grace period it is
        dropped.
        """
        now = now if now is not None else time.monotonic()
        with self._lock:
            tracks = self._tracks.get(camera, [])
            claimed = set()
            fresh = []

            for det in detections:
                best, best_iou = None, self.iou_threshold
                for i, track in enumerate(tracks):
                    if i in claimed or track["label"] != det["label"]:
                        continue
                    overlap = _iou(det["box"], track["box"])
                    if overlap >= best_iou:
                        best, best_iou = i, overlap
                if best is not None:
                    tracks[best]["box"] = det["box"]  # follow it if it drifts
                    tracks[best]["missed_since"] = None
                    claimed.add(best)
                else:
                    tracks.append({
                        "label": det["label"], "box": det["box"], "missed_since": None,
                        "track_id": self._next_track_id,
                        # Filled in by the worker once the arrival has been
                        # written: identification updates that row rather than
                        # writing a second one.
                        "detection_id": None,
                        "attempts": 0,
                        "resolved": False,
                    })
                    # Stamped on the detection so the worker can tie the row it
                    # is about to write back to the track. Inert everywhere else:
                    # neither record_detections nor fire_event looks at it.
                    det["track_id"] = self._next_track_id
                    self._next_track_id += 1
                    claimed.add(len(tracks) - 1)
                    fresh.append(det)

            survivors = []
            for i, track in enumerate(tracks):
                if i in claimed:
                    survivors.append(track)
                    continue
                if track["missed_since"] is None:
                    track["missed_since"] = now
                # A single missed frame is flicker or a brief occlusion, not a
                # departure; only give up after the grace period.
                if now - track["missed_since"] < self.absent_seconds:
                    survivors.append(track)
            self._tracks[camera] = survivors
            return fresh

    def note_detection_id(self, camera, track_id, detection_id):
        """Tell a track which row its arrival was written to."""
        with self._lock:
            for track in self._tracks.get(camera, []):
                if track.get("track_id") == track_id:
                    track["detection_id"] = detection_id
                    return True
        return False

    def awaiting_identity(self, camera, label="person", max_attempts=5):
        """Live tracks still worth looking at for a face.

        This is what makes identification possible at all. `reconcile` emits a
        detection once, on arrival — and somebody walking up a path is usually
        facing anywhere but the camera at that moment. The track outlives the
        arrival, so the frames after it can keep looking and name the row that
        was already written.

        Returns shallow copies: the caller runs slow vision work against these
        and must not be holding a reference into the tracker's own state while
        another thread reconciles it.
        """
        with self._lock:
            return [
                dict(track)
                for track in self._tracks.get(camera, [])
                if track["label"] == label
                and not track.get("resolved")
                and track.get("detection_id") is not None
                and track.get("attempts", 0) < max_attempts
            ]

    def note_attempt(self, camera, track_id):
        """Count one face pass against a track, returning attempts so far."""
        with self._lock:
            for track in self._tracks.get(camera, []):
                if track.get("track_id") == track_id:
                    track["attempts"] = track.get("attempts", 0) + 1
                    return track["attempts"]
        return None

    def resolve(self, camera, track_id):
        """This track is done — matched, or out of attempts. Either way nothing
        else is spent on it while it stays in view."""
        with self._lock:
            for track in self._tracks.get(camera, []):
                if track.get("track_id") == track_id:
                    track["resolved"] = True
                    return True
        return False


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
        presence=_UNSET,
        capture_factory=open_stream,
        log=print,
        identifier=None,
        on_identity=None,
        on_regions=None,
        face_attempts=5,
        face_min_pixels=60,
        zone=None,
    ):
        # `zone` is this camera's stored area rows, not a single shape: a camera
        # can have several, and they are evaluated together — see zones.evaluate.
        super().__init__(name=f"camera-{camera_id}", daemon=True)
        self.camera_id = camera_id
        self.url = url
        self.detector = det
        self.on_detections = on_detections
        # Called with the boxes of whatever changed, for classes the detector
        # was never trained on. None means nobody has taught it any, and the
        # contour work is skipped entirely.
        self.on_regions = on_regions
        # 0 means "consider every frame": no sampling throttle. Useful on a
        # low-rate source (a doorbell snapshot feed) where every frame is
        # already meaningful, and it is what the gate tests need to isolate the
        # gate from the sampler.
        self.max_fps = max(0.0, float(max_fps))
        self.motion_threshold = float(motion_threshold)
        self.confidence = confidence
        self.labels = labels
        # A shared tracker collapses repeats to one event per object present;
        # None turns that off and logs every detection the gate lets through.
        # Omitted entirely (the standalone default) gets its own tracker.
        self.presence = PresenceTracker() if presence is _UNSET else presence
        self.capture_factory = capture_factory
        self.log = log
        # Both None unless identification is switched on, and the face pass is
        # skipped entirely when either is missing — so the cost of the feature
        # for someone not using it is one `if`.
        self.identifier = identifier
        self.on_identity = on_identity
        self.face_attempts = max(1, int(face_attempts))
        self.face_min_pixels = int(face_min_pixels)
        # Parsed once here rather than per frame: this is read on every detection
        # in a hot loop, and areas only change when somebody edits them — which
        # reconfigures the workers anyway.
        self.zones = [z for z in (zones.parse(row) for row in zone or []) if z]

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
        # Detections dropped for being outside the zone. Worth counting: a zone
        # drawn slightly wrong shows up as this climbing while frames_detected
        # stays at zero, which is otherwise indistinguishable from a quiet drive.
        self.frames_filtered = 0
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
                # A positive line, on purpose. The only stream log used to be the
                # failure one, so a working camera produced silence — which reads
                # the same as a camera that never started. This says, in the log,
                # that frames are actually arriving, and at what size: the RTSP
                # SDP on this hardware does not advertise dimensions, so the
                # capture properties are the only place the resolution shows up.
                width, height = self._dimensions(capture)
                where = f" ({width}x{height})" if width else ""
                self.log(f"camera {self.camera_id}: connected{where}")
                self._read_until_failure(capture)
            except Exception as exc:  # noqa: BLE001 - a camera failing is routine
                self.state = "error"
                self.detail = _explain(exc)
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

    @staticmethod
    def _dimensions(capture):
        try:
            import cv2

            return (int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
                    int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        except Exception:  # noqa: BLE001 - a fake capture in tests, or no cv2
            return (0, 0)

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
        previous = self._previous
        score = detector.motion_score(previous, frame)
        self._previous = frame
        if score < self.motion_threshold:
            return

        # Where the frame changed, for classes the detector does not know.
        # Computed from the same pair the score came from, which is why
        # `previous` is held above rather than read back after the assignment.
        # Only when somebody is listening: it is contour work on every motion
        # frame, and a household with no custom classes should not pay for it.
        if self.on_regions is not None:
            try:
                regions = detector.motion_regions(previous, frame)
                if regions:
                    self.on_regions(self.camera_id, frame, regions)
            except Exception as exc:  # noqa: BLE001 - a naming failure must not
                # end the camera thread, exactly as an identification failure
                # must not below.
                self.log(f"camera {self.camera_id}: could not name regions: {exc}")

        detections, error = self.detector.detect(
            frame, confidence=self.confidence, labels=self.labels
        )
        if error:
            self.state = "error"
            self.detail = error
            return

        # Before anything else sees them. A car in the street is not an event on
        # a driveway camera, and dropping it here means no track, no row, no
        # snapshot and no event — rather than filtering it out of the page later
        # and still paying for all four.
        if self.zones:
            height, width = frame.shape[:2]
            kept = zones.filter_detections(self.zones, detections, width, height)
            self.frames_filtered += len(detections) - len(kept)
            detections = kept

        # With a tracker, only genuine arrivals get through; without one, every
        # detection does. The tracker is fed the whole frame's set either way so
        # it can tell an object left.
        if self.presence is not None:
            fresh = self.presence.reconcile(self.camera_id, detections)
        else:
            fresh = detections

        written = None
        if fresh:
            self.frames_detected += 1
            self.last_detection_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            try:
                written = self.on_detections(self.camera_id, fresh, frame)
                self._remember_rows(fresh, written)
            except Exception as exc:  # noqa: BLE001 - a storage failure is not a
                # reason to stop watching the camera; the next frame may well work.
                self.log(f"camera {self.camera_id}: could not record: {exc}")

        # Deliberately outside the `if fresh` above: after the arrival frame a
        # person who is still standing there produces no fresh detections at all,
        # and those are exactly the frames in which they finally turn towards the
        # camera. `detections` rather than `fresh` is the test for "somebody is
        # in view right now".
        if detections and self.identifier is not None:
            try:
                if self.presence is not None:
                    self._identify_pending(frame)
                else:
                    self._identify_once(frame, fresh, written)
            except Exception as exc:  # noqa: BLE001 - same reasoning as above:
                # a face pass that failed must not end the camera thread.
                self.log(f"camera {self.camera_id}: could not identify: {exc}")

    def _remember_rows(self, fresh, written):
        """Tie each arrival to the row it was written to, for identification.

        `written` is the list of ids the recorder produced, in the same order.
        An older recorder returning None simply means no identification: the
        pass needs a row to update, and inventing an id would corrupt one.
        """
        if not written or self.presence is None:
            return
        for det, detection_id in zip(fresh, written):
            if det.get("track_id") is not None:
                self.presence.note_detection_id(
                    self.camera_id, det["track_id"], detection_id
                )

    def _identify_once(self, frame, fresh, written):
        """The no-tracker path: one attempt per detection, no retry.

        With `collapse_repeats` off there are no tracks, so there is nothing to
        retry against — every frame is an arrival. Identification still works,
        it just gets the one look, which is the cost the operator chose when they
        turned collapsing off. Saying nothing here would be worse: a feature that
        silently does nothing under a supported setting is a trap.
        """
        if self.on_identity is None or not written:
            return
        for det, detection_id in zip(fresh, written):
            if det.get("label") != "person":
                continue
            found = self.identifier.find_in_person(
                frame, det["box"], self.face_min_pixels
            )
            self.on_identity(self.camera_id, detection_id, found, True)

    def _identify_pending(self, frame):
        """Look for a face on every person still waiting for a name.

        Bounded twice over: only tracks under their attempt budget are
        considered, and a track is resolved — permanently, for as long as it
        stays in view — the moment it is matched or runs out. So a person who
        never shows their face costs `face_attempts` crops for the whole visit,
        not one per frame for as long as they stand there.
        """
        if self.identifier is None or self.on_identity is None or self.presence is None:
            return

        for track in self.presence.awaiting_identity(
            self.camera_id, "person", self.face_attempts
        ):
            attempts = self.presence.note_attempt(self.camera_id, track["track_id"])
            final = attempts is not None and attempts >= self.face_attempts
            found = self.identifier.find_in_person(
                frame, track["box"], self.face_min_pixels
            )
            # Nothing usable and budget left: say nothing, look again next frame.
            # This is what keeps one visit to one database write.
            if found is None and not final:
                continue
            if self.on_identity(self.camera_id, track["detection_id"], found, final):
                self.presence.resolve(self.camera_id, track["track_id"])

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
            "frames_filtered": self.frames_filtered,
            # NOT "zone": the stored column of that name holds the shape itself,
            # and a thread's view is merged over the database row — so a boolean
            # here replaced the saved shape, the parse failed, and the page
            # reported "nothing set" about a zone that was in force all along.
            "zone_active": bool(self.zones),
            "zones": len(self.zones),
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

    def __init__(self, det, on_detections, log=print, identifier=None,
                 on_identity=None, on_regions=None):
        self.detector = det
        self.on_detections = on_detections
        self.on_regions = on_regions
        self.log = log
        # The face models and the callback that decides who somebody is. Both
        # None means identification is off, which is the default.
        self.identifier = identifier
        self.on_identity = on_identity
        self.workers = {}
        self._lock = threading.Lock()

    def configure(self, cameras, **options):
        """(re)start workers for `cameras`, a list of {"id", "url"} dicts.

        `zones` maps camera id to a stored zone. Passed in rather than read here
        because this module owns no database connection — the same division that
        keeps sqlite's thread affinity somebody else's problem.
        """
        with self._lock:
            self._stop_all()
            absent_seconds = options.pop("cooldown_seconds", 30)
            collapse = options.pop("collapse_repeats", True)
            camera_zones = options.pop("zones", None) or {}
            # One tracker shared across the cameras, keyed by camera inside, so a
            # car on the drive and a car in the street are tracked apart. None
            # when the operator has turned collapsing off.
            presence = PresenceTracker(absent_seconds=absent_seconds) if collapse else None
            for camera in cameras:
                if not camera.get("url"):
                    continue
                worker = CameraWorker(
                    camera["id"],
                    camera["url"],
                    self.detector,
                    self.on_detections,
                    presence=presence,
                    log=self.log,
                    identifier=self.identifier,
                    on_identity=self.on_identity,
                    on_regions=self.on_regions,
                    zone=camera_zones.get(camera["id"]),
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
