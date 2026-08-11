"""Faces: finding one inside a person box, and turning it into a number.

Object detection answers "a person is here". This answers "which person", and it
is a different problem with a different failure mode: getting it wrong means
putting somebody's name on a stranger, so everything here is built to return
*nothing* rather than a guess.

Two models, both from the OpenCV Zoo and both run through the same `cv2` that
already carries YOLOX — no new dependency, no onnxruntime:

- YuNet (MIT) finds faces and their five landmarks. Small enough to run on a
  person crop for the price of rounding error.
- SFace (Apache-2.0) turns an aligned face into a 128-D vector. Two vectors from
  the same person point in nearly the same direction; that angle is the whole
  identification. Note the vectors are *not* unit length — measured 6.03 on a
  real face — so `cosine` normalises rather than assuming.

The alignment step is not optional. `alignCrop` uses the landmarks to warp a face
to the canonical 112x112 SFace was trained on; feeding it a plain rectangular
crop is the classic way to build a face pipeline that runs, returns plausible
numbers, and matches nobody.

The size floor is the honest part. A face has to be big enough to embed — see
`MIN_FACE_PIXELS` — and on a camera watching a driveway it usually is not. This
module says so rather than returning a vector computed from eight pixels.
"""
from __future__ import annotations

import os
import threading
import time

try:
    import cv2
    import numpy as np

    OPENCV_AVAILABLE = True
    OPENCV_ERROR = None
except ImportError as exc:  # pragma: no cover - exercised by the import guard test
    OPENCV_AVAILABLE = False
    OPENCV_ERROR = str(exc)
    cv2 = None
    np = None

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")

# YuNet 2023mar rather than the newer 2026may: 2026may requires OpenCV 5's ONNX
# Runtime engine, and requirements.txt spans `>=4.10,<6.0`. A model that loads on
# only half of the supported range is not a bundled model, it is a trap.
DETECTOR_MODEL = os.path.join(MODEL_DIR, "face_detection_yunet_2023mar.onnx")
RECOGNISER_MODEL = os.path.join(MODEL_DIR, "face_recognition_sface_2021dec.onnx")

# Stamped on every stored print. A vector from one recogniser means nothing to
# another, and comparing across them would not error — it would quietly score
# strangers as matches. Prints whose model does not match the running one are
# ignored rather than trusted.
RECOGNISER_NAME = "sface_2021dec"
EMBEDDING_DIMS = 128

# How wide a face must be before it is worth embedding. SFace works from a
# 112x112 aligned crop, so anything much smaller is upscaled guesswork. This is
# the default; the operator can move it, and the probe endpoint exists so they
# can see what their own camera actually produces before they do.
MIN_FACE_PIXELS = 60

# How much of the person box to include around the face. A person box is
# shoulders-down-to-feet more often than it is a portrait, and YuNet wants a
# little context around the head.
CROP_MARGIN = 0.2

# YuNet's own confidence in "this is a face", separate from any identity score.
DETECT_SCORE_THRESHOLD = 0.6
DETECT_NMS_THRESHOLD = 0.3
DETECT_TOP_K = 500


def crop_with_margin(image, box, margin=CROP_MARGIN):
    """The person box plus a margin, clipped to the frame.

    Returns (crop, offset_x, offset_y) so a face box found inside the crop can be
    put back into frame coordinates — which is what the caller stores, and what
    the page draws.
    """
    height, width = image.shape[:2]
    x, y, w, h = (int(v) for v in box)
    pad = int(margin * max(w, h))
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(width, x + w + pad), min(height, y + h + pad)
    if x1 <= x0 or y1 <= y0:
        return None, 0, 0
    # Contiguous because the ONNX path wants a real buffer, not a view with a
    # stride: a slice of a frame is not contiguous and cv2 will copy it anyway.
    return np.ascontiguousarray(image[y0:y1, x0:x1]), x0, y0


def cosine(a, b):
    """Cosine similarity of two embeddings, in -1..1.

    Written out rather than calling `FaceRecognizerSF.match`, for two reasons.
    The constant naming the cosine metric is `FaceRecognizerSF_FR_COSINE` on
    OpenCV 5 and `..._DIS_COSINE` on 4.x, and requirements.txt allows both — so
    the call that looks simplest is the one that breaks on half the range. And a
    plain function over two arrays can be tested with vectors written by hand,
    which is how the rest of the maths in this add-on is pinned.
    """
    a, b = np.asarray(a, dtype=np.float64).ravel(), np.asarray(b, dtype=np.float64).ravel()
    if a.size != b.size or not a.size:
        return 0.0
    norms = np.linalg.norm(a) * np.linalg.norm(b)
    if norms == 0:
        return 0.0
    return float(np.dot(a, b) / norms)


def pack_embedding(embedding):
    """A vector as bytes for the BLOB column: float32, little-endian, fixed."""
    return np.asarray(embedding, dtype="<f4").ravel().tobytes()


def unpack_embedding(blob):
    """Bytes back to a vector, or None if the row is not what it claims.

    A truncated or foreign blob returns None rather than a short vector: a
    silently wrong-length embedding would compare against everything at some
    arbitrary angle instead of failing.
    """
    if not blob:
        return None
    try:
        vector = np.frombuffer(blob, dtype="<f4")
    except (ValueError, TypeError):
        # A length that is not a multiple of four raises rather than truncating.
        # This runs in a camera thread against whatever is in the database, so
        # it answers "not an embedding" instead of ending the thread.
        return None
    return vector if vector.size == EMBEDDING_DIMS else None


def best_match(embedding, prints, threshold, margin):
    """Which person this face is, or None. Never a guess.

    `prints` is an iterable of (person_id, embedding). Two rules decide, and a
    match needs both:

    - the best score reaches `threshold`;
    - it beats the best score of *any other person* by `margin`. Two people at
      0.47 and 0.46 is a coin flip, and a coin flip that prints somebody's name
      is worse than saying nothing.

    Scoring is the maximum over a person's prints, not the mean. Prints exist to
    cover variation — hat, angle, night, glasses — and averaging them away is
    exactly the wrong thing to do with the variation they were enrolled for.

    Returns {"person_id", "score", "runner_up"} for a match, or
    {"person_id": None, "score": <best seen>, "runner_up": ...} when nothing
    clears the bars. The best score is reported either way: it is the number that
    tells an operator whether the threshold is wrong or the face is a stranger.
    """
    by_person = {}
    for person_id, other in prints:
        vector = other if isinstance(other, np.ndarray) else unpack_embedding(other)
        if vector is None:
            continue
        score = cosine(embedding, vector)
        if score > by_person.get(person_id, -1.0):
            by_person[person_id] = score

    if not by_person:
        return {"person_id": None, "score": None, "runner_up": None}

    ranked = sorted(by_person.items(), key=lambda item: -item[1])
    best_id, best_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else None

    if best_score < threshold:
        return {"person_id": None, "score": best_score, "runner_up": runner_up}
    if runner_up is not None and best_score - runner_up < margin:
        return {"person_id": None, "score": best_score, "runner_up": runner_up}
    return {"person_id": best_id, "score": best_score, "runner_up": runner_up}


class FaceIdentifier:
    """The two models, loaded once, shared by every camera thread.

    Same contract as `Detector`: loading is lazy, a failure is recorded on the
    instance rather than raised, and `status()` says what happened. A missing or
    unloadable model must leave an add-on that still detects objects — face
    identification is an optional extra and a broken extra is not a dead add-on.
    """

    def __init__(self, detector_model=None, recogniser_model=None):
        self.detector_model = detector_model or DETECTOR_MODEL
        self.recogniser_model = recogniser_model or RECOGNISER_MODEL
        self._detector = None
        self._recogniser = None
        self.error = None
        self.last_latency_ms = None
        # One lock for both models. YuNet is stateful in a way YOLOX is not:
        # setInputSize configures the *next* detect, so the pair has to be
        # atomic or two camera threads will each run with the other's size.
        self._lock = threading.Lock()

    def load(self):
        """Both models, or None with `error` set. Cheap after the first call."""
        if self._detector is not None and self._recogniser is not None:
            return self._detector, self._recogniser
        if self.error:
            return None, None
        with self._lock:
            if self._detector is not None and self._recogniser is not None:
                return self._detector, self._recogniser
            if not OPENCV_AVAILABLE:
                self.error = f"opencv is not installed: {OPENCV_ERROR}"
                return None, None
            for path in (self.detector_model, self.recogniser_model):
                if not os.path.exists(path):
                    self.error = f"face model not found at {path}"
                    return None, None
            try:
                self._detector = cv2.FaceDetectorYN.create(
                    self.detector_model, "", (320, 320),
                    DETECT_SCORE_THRESHOLD, DETECT_NMS_THRESHOLD, DETECT_TOP_K,
                )
                self._recogniser = cv2.FaceRecognizerSF.create(self.recogniser_model, "")
            except cv2.error as exc:
                self._detector = self._recogniser = None
                self.error = f"could not load the face models: {exc}"
                return None, None
        return self._detector, self._recogniser

    def available(self):
        return OPENCV_AVAILABLE and self.load()[0] is not None

    def status(self):
        detector, _ = self.load()
        return {
            "available": detector is not None,
            "recogniser": RECOGNISER_NAME,
            "dims": EMBEDDING_DIMS,
            "detector_model": os.path.basename(self.detector_model),
            "recogniser_model": os.path.basename(self.recogniser_model),
            "error": self.error,
            "last_latency_ms": self.last_latency_ms,
        }

    def detect_faces(self, image):
        """Every face in this image, biggest first.

        Biggest first because when a person box contains two faces — someone
        walking behind — the one that fills the box is the one the box is about.
        """
        detector, _ = self.load()
        if detector is None or image is None or not image.size:
            return []
        height, width = image.shape[:2]
        if height < 10 or width < 10:
            return []
        with self._lock:
            started = time.perf_counter()
            detector.setInputSize((width, height))
            _, faces = detector.detect(image)
            self.last_latency_ms = round((time.perf_counter() - started) * 1000, 1)
        if faces is None:
            return []
        return sorted(faces, key=lambda row: -float(row[2]))

    def embed(self, image, face_row):
        """An aligned 112x112 crop and its embedding, or (None, None).

        The crop comes back with the vector because it is what the UI shows when
        asking "is this the right face?" — a name attached to a print nobody can
        see is a name nobody can audit.
        """
        _, recogniser = self.load()
        if recogniser is None:
            return None, None
        with self._lock:
            try:
                aligned = recogniser.alignCrop(image, face_row)
                embedding = recogniser.feature(aligned)
            except cv2.error:
                # A face box on the very edge can produce a warp the aligner
                # refuses. One unusable frame, not a broken camera.
                return None, None
        return aligned, np.asarray(embedding, dtype=np.float32).ravel()

    def find_in_person(self, frame, person_box, min_pixels=MIN_FACE_PIXELS):
        """The one usable face inside a person box, as a dict, or None.

        Returns None for "no face here" *and* for "a face too small to mean
        anything", which are the same answer to the caller — but `probe` below
        keeps them apart, because to a human deciding whether their camera can do
        this at all, they are completely different news.
        """
        found = self.probe_person(frame, person_box, min_pixels)
        if not found or not found["usable"]:
            return None
        return found

    def probe_person(self, frame, person_box, min_pixels=MIN_FACE_PIXELS):
        """What the largest face in this person box is, whether or not it is
        usable. The instrument behind /api/faces/probe."""
        crop, offset_x, offset_y = crop_with_margin(frame, person_box)
        if crop is None:
            return None
        faces = self.detect_faces(crop)
        if not faces:
            return {"usable": False, "reason": "no face found", "face_box": None,
                    "face_width": 0, "detect_score": None, "embedding": None,
                    "aligned": None}

        row = faces[0]
        width = int(row[2])
        box = [int(row[0]) + offset_x, int(row[1]) + offset_y, width, int(row[3])]
        result = {
            "usable": False, "reason": None, "face_box": box, "face_width": width,
            "detect_score": round(float(row[-1]), 3), "embedding": None, "aligned": None,
        }
        if width < min_pixels:
            result["reason"] = (
                f"the largest face is {width} px wide; at least {min_pixels} is "
                "needed to identify reliably"
            )
            return result

        aligned, embedding = self.embed(crop, row)
        if embedding is None:
            result["reason"] = "the face could not be aligned"
            return result
        result.update(usable=True, embedding=embedding, aligned=aligned)
        return result
