"""Recognising objects you have taught it, in regions something else proposed.

The detector handles the 80 COCO classes and nothing else. This is what lets a
household add its own — a particular cargo bike, a wheelie bin, a delivery van —
without a GPU and without a labelled dataset in the thousands.

**It names regions; it does not find them.** That division is the whole design,
and it is forced by measurement rather than preference: YOLOX proposes boxes
only for things it already recognises, and on an object outside COCO it returns
either nothing, a sliver, or a box the size of the frame. So the proposal comes
from `detector.motion_regions` — on a camera bolted to a wall, "what changed" is
a proposal method that owes nothing to what the object is — and this module
answers only "what is that".

The matching rule is `faces.best_match`, imported rather than reimplemented. It
is the same question with different vectors, and two copies of a rule about when
to abstain would eventually disagree about it.
"""
from __future__ import annotations

import os
import threading

import faces

try:
    import cv2
    import numpy as np

    OPENCV_AVAILABLE = True
except ImportError:  # pragma: no cover - mirrors detector.py's guard
    OPENCV_AVAILABLE = False
    cv2 = None
    np = None

MODEL_PATH = os.environ.get(
    "DETECTION_OBJECT_EMBED_MODEL",
    os.path.join(os.path.dirname(__file__), "models", "squeezenet1.1-7.onnx"),
)
INPUT_SIZE = 224
# ImageNet, RGB order — what this export was trained against. Getting these
# wrong does not error; it quietly degrades every embedding.
MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)

# Reported alongside every answer so they can be moved on evidence rather than
# taste. A wrong name on a security alert is worse than no name: "your cargo
# bike is here" while a van sits in its place is the failure this module exists
# to avoid.
#
# These suit *centred* vectors — see `centre`.
#
# 0.35, and the number is provisional by design. Measured on a real CCTV frame
# of the object this was built for, three crops of it scored +0.20 to +0.64
# against each other and -0.68 to +0.11 against the paving, wall and grass
# around it. The usable gap is that narrow — and two views of one object can be
# less alike (+0.20, front against rear) than a bike and a wall (+0.11). An
# earlier default of 0.5 sat outside that band entirely and would have refused
# a genuine view of the thing it was trained on.
#
# It is survivable because the camera does not move: every crop comes from one
# angle, where the same measurement gave +0.64. But that is an argument for
# calibrating against a fixed camera's own output, not for trusting a constant
# — which is why every queued crop records the score it actually got.
DEFAULT_THRESHOLD = 0.35
DEFAULT_MARGIN = 0.12


class ObjectEmbedder:
    """SqueezeNet, loaded once and shared.

    Small on purpose: 4.7MB and a few milliseconds a crop, so a frame with four
    motion regions stays inside the budget the detector already works to. A
    larger backbone would separate classes better and would also mean this
    could not run on every motion event, which is worth more.
    """

    def __init__(self, model_path=None):
        self.model_path = model_path or MODEL_PATH
        self._net = None
        self._lock = threading.Lock()
        self.error = None

    def load(self):
        if self._net is not None or self.error:
            return self._net
        if not OPENCV_AVAILABLE:
            self.error = "opencv is not available"
            return None
        if not os.path.exists(self.model_path):
            self.error = f"embedder model not found at {self.model_path}"
            return None
        try:
            self._net = cv2.dnn.readNetFromONNX(self.model_path)
        except cv2.error as exc:
            self.error = str(exc)
        return self._net

    def embed(self, crop):
        """One crop as an L2-normalised vector, or None.

        Aspect distortion from the square resize is accepted because it is
        identical at enrolment and at recognition — a box bike squashed the same
        way both times still matches itself.
        """
        net = self.load()
        if net is None or crop is None or crop.size == 0:
            return None
        small = cv2.resize(crop, (INPUT_SIZE, INPUT_SIZE), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        rgb = (rgb - np.array(MEAN, np.float32)) / np.array(STD, np.float32)
        blob = rgb.transpose(2, 0, 1)[None]
        with self._lock:
            net.setInput(blob)
            vector = net.forward().flatten().astype(np.float32)
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm > 0 else None

    def status(self):
        self.load()
        return {"available": self._net is not None, "error": self.error,
                "model_path": self.model_path, "dim": 1000}


def mean_vector(prints):
    """The average of every enrolled print — the direction to subtract.

    Kept over all classes together rather than per class: what is being removed
    is what this camera's crops have in common, which is a property of the
    scene and the backbone, not of any one object.
    """
    vectors = [_as_vector(v) for _, v in prints]
    vectors = [v for v in vectors if v is not None]
    if not vectors:
        return None
    return np.mean(np.stack(vectors), axis=0)


def centre(embedding, mean):
    """Remove the common direction and renormalise.

    This is not a refinement, it is the difference between working and not.
    SqueezeNet's 1000-d output is dominated by a direction every crop shares, so
    raw cosine puts everything at 0.94-1.00: measured on a real frame, two crops
    of the same object scored 0.995 and a crop of the object against a crop of
    the cobbles beside it scored 0.957. A 0.038 gap is no gap. Centred, the same
    pairs score 0.774 and -0.758.

    Coop Tracker's box classifier does the same thing for the same reason.
    """
    if embedding is None:
        return None
    if mean is None:
        return embedding
    centred = embedding - mean
    norm = float(np.linalg.norm(centred))
    return centred / norm if norm > 0 else None


def _as_vector(value):
    if value is None or isinstance(value, np.ndarray):
        return value
    return faces.unpack_embedding(value)


def identify(embedding, prints, mean=None,
             threshold=DEFAULT_THRESHOLD, margin=DEFAULT_MARGIN):
    """Which trained class this crop is, or None. Never a guess.

    `prints` is an iterable of (object_id, embedding), and `mean` the vector to
    centre against — pass what `mean_vector` returned for the same set, or leave
    it out and it is computed here.

    Both bars have to clear: close enough to something known, and decisively
    closer to it than to anything else. Failing either is reported as no match
    *with the score*, because that number is what says whether the threshold is
    wrong or the thing is genuinely new.
    """
    prints = list(prints)
    if mean is None:
        mean = mean_vector(prints)

    query = centre(embedding, mean)
    if query is None:
        return {"object_id": None, "score": None, "runner_up": None,
                "nearest_id": None}

    centred_prints = []
    for object_id, value in prints:
        vector = centre(_as_vector(value), mean)
        if vector is not None:
            centred_prints.append((object_id, vector))

    result = faces.best_match(query, centred_prints, threshold, margin)
    return {
        "object_id": result["person_id"],
        "score": result["score"],
        "runner_up": result["runner_up"],
        # Which class it was closest to, accepted or not — the number a keeper
        # needs to set the threshold from their own camera rather than from a
        # default somebody guessed.
        "nearest_id": result["nearest_id"],
    }


def crop_region(image, box, margin=0.12):
    """A motion region as an image, with a little context around it.

    The margin is what makes a bike a bike rather than a rectangle of dark
    plywood: the wall and the kerb around it are part of what the embedding
    keys on, and they are stable on a fixed camera.
    """
    if image is None:
        return None
    x, y, w, h = (int(v) for v in box)
    pad_x, pad_y = int(w * margin), int(h * margin)
    height, width = image.shape[:2]
    x1, y1 = max(0, x - pad_x), max(0, y - pad_y)
    x2, y2 = min(width, x + w + pad_x), min(height, y + h + pad_y)
    if x2 <= x1 or y2 <= y1:
        return None
    return image[y1:y2, x1:x2]
