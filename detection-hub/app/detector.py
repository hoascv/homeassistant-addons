"""Object detection on the CPU, and the motion gate that keeps it affordable.

The detector is the expensive part and the motion gate is what makes it viable.
A 416x416 forward pass costs ~15ms; a camera at 15fps would spend a whole core
re-deciding that nothing has changed. So `motion_score` runs on every sampled
frame at a fraction of the cost, and the detector only ever sees frames that
differ from what came before.

The model is YOLOX-Nano, baked into the image at `models/`. Chosen over
YOLOv5/v8 because those are Ultralytics AGPL-3.0, which is the wrong licence to
vendor into a public repository; YOLOX is Apache-2.0. Nano over Tiny because it
measured 14ms against Tiny's 51ms for the same objects on the same frame, at a
fifth of the file size.
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

# The bundled models, both YOLOX and both exported at INPUT_SIZE, so the
# letterbox, the grid decode and the NMS below are identical for either — only
# the weights differ. Measured on the same frame: nano 14 ms, tiny 51 ms, for
# the same objects found on that scene. Nano is the default because 3.6x the
# CPU should be a decision, not a surprise; tiny is there for the case nano is
# expected to lose, which is small and distant objects.
MODELS = {
    "nano": os.path.join(MODEL_DIR, "yolox_nano.onnx"),
    "tiny": os.path.join(MODEL_DIR, "yolox_tiny.onnx"),
}
DEFAULT_MODEL_NAME = "nano"
DEFAULT_MODEL = MODELS[DEFAULT_MODEL_NAME]


def model_for(name):
    """A bundled model's path by name, falling back to the default.

    An unrecognised name is the default rather than an error: this is fed from a
    config option, and a typo should leave a working detector rather than an
    add-on that will not detect anything.
    """
    return MODELS.get((name or "").strip().lower(), DEFAULT_MODEL)

# The input side YOLOX was exported at. Changing it silently breaks `decode`,
# which derives its grids from this — so it travels with the model, not as a
# free-floating option.
INPUT_SIZE = 416

# YOLOX pads with 114, not 0. A black border reads as real image content and
# shifts detections near the edge.
PAD_VALUE = 114

# The 80 COCO classes, in the order the model emits them. Order is load-bearing:
# the index into this list *is* the class id.
COCO_LABELS = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
)


# --- preprocessing ------------------------------------------------------------


def letterbox(image, size=INPUT_SIZE):
    """Scale into a square canvas without distorting, image at the TOP-LEFT.

    Returns (canvas, ratio). The ratio is what maps a box back to original
    pixels, and top-left placement is why that is a single divide rather than a
    divide and two offsets — YOLOX centres nothing, so neither do we.
    """
    height, width = image.shape[:2]
    ratio = min(size / height, size / width)
    new_h, new_w = int(round(height * ratio)), int(round(width * ratio))
    canvas = np.full((size, size, 3), PAD_VALUE, dtype=np.uint8)
    canvas[:new_h, :new_w] = cv2.resize(
        image, (new_w, new_h), interpolation=cv2.INTER_LINEAR
    )
    return canvas, ratio


def _grids_and_strides(size=INPUT_SIZE):
    """Per-anchor grid coordinates and strides for YOLOX's three FPN levels.

    For 416 this is 52x52 + 26x26 + 13x13 = 3549 anchors, matching the model's
    output row count exactly — which is the cheapest available check that the
    model and this decode agree.
    """
    grids, strides = [], []
    for stride in (8, 16, 32):
        cells = size // stride
        ys, xs = np.meshgrid(np.arange(cells), np.arange(cells), indexing="ij")
        grids.append(np.stack((xs, ys), 2).reshape(-1, 2))
        strides.append(np.full((cells * cells, 1), stride))
    return np.concatenate(grids, 0), np.concatenate(strides, 0)


def decode(raw, size=INPUT_SIZE):
    """Turn the model's raw head output into pixel boxes in the padded frame.

    YOLOX emits one (1, anchors, 85) tensor: 4 box values, 1 objectness, 80 class
    scores. The box is *not* pixels — xy is an offset from its grid cell and wh
    is log-space, both in stride units. Getting this wrong produces boxes that
    look plausible and are in the wrong place, which is why it is a separate,
    directly tested function rather than three lines inside `detect`.
    """
    grid, stride = _grids_and_strides(size)
    if raw.shape[1] != grid.shape[0]:
        raise ValueError(
            f"model emits {raw.shape[1]} anchors, decode expects {grid.shape[0]} "
            f"for input size {size} — model and INPUT_SIZE disagree"
        )
    out = raw[0].astype(np.float32).copy()
    out[:, 0:2] = (out[:, 0:2] + grid) * stride
    out[:, 2:4] = np.exp(out[:, 2:4]) * stride
    return out


# --- the motion gate ----------------------------------------------------------


def motion_regions(previous, current, width=320, blur=21, delta_threshold=25,
                   min_area=0.002, merge_gap=24):
    """Where the frame changed, as boxes in the original image's coordinates.

    The same difference `motion_score` already computes, kept instead of
    counted. It exists because a custom object class needs somewhere to look:
    YOLOX proposes boxes only for things it recognises, so an object outside
    the 80 COCO classes gets either no box or the whole frame — measured, on a
    cargo bike, as exactly those two failures. On a camera bolted to a wall,
    "what changed" is a proposal method that owes nothing to what the object is.

    Boxes closer than `merge_gap` are merged: a person walking produces a torso
    and two legs, and three crops of one person are three chances to be wrong
    about it.
    """
    if previous is None or current is None:
        return []
    scale = width / current.shape[1]
    size = (width, max(1, int(current.shape[0] * scale)))

    def prepare(frame):
        small = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
        grey = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(grey, (blur, blur), 0)

    diff = cv2.absdiff(prepare(previous), prepare(current))
    mask = cv2.threshold(diff, delta_threshold, 255, cv2.THRESH_BINARY)[1]
    # Dilated before contouring so a single object broken up by the blur comes
    # back as one region rather than a constellation.
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8), iterations=2)

    contours = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
    frame_area = mask.shape[0] * mask.shape[1]
    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w * h < min_area * frame_area:
            continue
        boxes.append([x, y, x + w, y + h])

    boxes = _merge_boxes(boxes, merge_gap)
    # Back to the full-resolution frame the crops will be taken from.
    return [
        [int(x1 / scale), int(y1 / scale), int((x2 - x1) / scale), int((y2 - y1) / scale)]
        for x1, y1, x2, y2 in boxes
    ]


def _merge_boxes(boxes, gap):
    """Repeatedly union boxes that touch or nearly touch."""
    merged = True
    while merged and len(boxes) > 1:
        merged = False
        out = []
        while boxes:
            x1, y1, x2, y2 = boxes.pop()
            rest = []
            for other in boxes:
                if (x1 - gap < other[2] and other[0] - gap < x2
                        and y1 - gap < other[3] and other[1] - gap < y2):
                    x1, y1 = min(x1, other[0]), min(y1, other[1])
                    x2, y2 = max(x2, other[2]), max(y2, other[3])
                    merged = True
                else:
                    rest.append(other)
            boxes = rest
            out.append([x1, y1, x2, y2])
        boxes = out
    return boxes


def motion_score(previous, current, width=320, blur=21, delta_threshold=25):
    """Fraction of the frame that changed, 0.0 to 1.0.

    Deliberately crude: downscale, grey, blur, absolute difference, count pixels
    over a threshold. The blur is what stops sensor noise and compression
    artefacts reading as movement. This runs on every sampled frame, so it is
    kept far cheaper than the thing it guards.
    """
    if previous is None or current is None:
        return 1.0  # no baseline yet — let the first frame through
    scale = width / current.shape[1]
    size = (width, max(1, int(current.shape[0] * scale)))

    def prepare(frame):
        small = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
        grey = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(grey, (blur, blur), 0)

    diff = cv2.absdiff(prepare(previous), prepare(current))
    changed = cv2.threshold(diff, delta_threshold, 255, cv2.THRESH_BINARY)[1]
    return float(np.count_nonzero(changed)) / changed.size


# --- the detector -------------------------------------------------------------


class Detector:
    """A loaded model, shared across waitress' worker threads.

    The net is built lazily on first use rather than at import, so a broken or
    missing model file surfaces as a reported error on one request instead of a
    container that will not boot. `forward()` is not documented thread-safe, so
    inference is serialised — the same choice Coop Tracker made for its
    embedder, and at ~15ms a frame the lock is not the bottleneck.
    """

    def __init__(self, model_path=None, input_size=INPUT_SIZE):
        self.model_path = model_path or DEFAULT_MODEL
        self.input_size = input_size
        self._net = None
        self._lock = threading.Lock()
        self.error = None
        self.last_latency_ms = None

    def available(self):
        return OPENCV_AVAILABLE and self.load() is not None

    def load(self):
        if self._net is not None or self.error is not None:
            return self._net
        with self._lock:
            if self._net is not None or self.error is not None:
                return self._net
            if not OPENCV_AVAILABLE:
                self.error = f"opencv is not installed: {OPENCV_ERROR}"
                return None
            if not os.path.exists(self.model_path):
                self.error = f"model not found at {self.model_path}"
                return None
            try:
                self._net = cv2.dnn.readNetFromONNX(self.model_path)
            except Exception as exc:  # noqa: BLE001 - any load failure is reportable
                self.error = f"could not load {self.model_path}: {exc}"
                return None
        return self._net

    @property
    def model_name(self):
        """The bundled model's short name, or "custom" for anything else — so
        "which model is actually running" is answerable without comparing
        paths."""
        for name, path in MODELS.items():
            if os.path.abspath(path) == os.path.abspath(self.model_path):
                return name
        return "custom"

    def status(self):
        return {
            "opencv_available": OPENCV_AVAILABLE,
            "model": self.model_name,
            "model_path": self.model_path,
            "model_loaded": self._net is not None,
            "input_size": self.input_size,
            "error": self.error,
            "last_latency_ms": self.last_latency_ms,
        }

    def detect(self, image, confidence=0.6, nms_threshold=0.45, labels=None):
        """Detections in the original image's pixel coordinates.

        Returns a list of {"label", "confidence", "box": [x, y, w, h]}, ordered
        most confident first. `labels`, when given, filters to those class names
        *before* NMS is reported — a driveway camera that only cares about people
        and cars should not pay attention costs for 78 other classes.
        """
        net = self.load()
        if net is None:
            return [], self.error

        padded, ratio = letterbox(image, self.input_size)
        # NCHW float32, channels as-is. The standard YOLOX export folds
        # normalisation into the graph: no /255, no mean subtraction. Adding
        # either makes every score collapse.
        blob = padded.transpose(2, 0, 1)[None].astype(np.float32)

        with self._lock:
            started = time.perf_counter()
            net.setInput(blob)
            raw = net.forward()
            self.last_latency_ms = round((time.perf_counter() - started) * 1000, 1)

        predictions = decode(raw, self.input_size)
        scores = predictions[:, 4:5] * predictions[:, 5:]
        class_ids = scores.argmax(1)
        best = scores.max(1)

        keep = best > confidence
        if labels:
            wanted = {COCO_LABELS.index(name) for name in labels if name in COCO_LABELS}
            keep &= np.isin(class_ids, list(wanted)) if wanted else False
        if not np.any(keep):
            return [], None

        boxes, best, class_ids = predictions[keep, :4], best[keep], class_ids[keep]
        # centre/size -> top-left/size, then back out of the letterbox scaling.
        top_left = boxes[:, :2] - boxes[:, 2:4] / 2
        rects = np.concatenate([top_left, boxes[:, 2:4]], 1) / ratio

        indices = cv2.dnn.NMSBoxes(
            rects.tolist(), best.tolist(), confidence, nms_threshold
        )
        detections = [
            {
                "label": COCO_LABELS[class_ids[i]],
                "confidence": round(float(best[i]), 3),
                "box": [int(v) for v in rects[i]],
            }
            for i in np.array(indices).flatten()
        ]
        detections.sort(key=lambda d: -d["confidence"])
        return detections, None


def decode_image(data):
    """JPEG/PNG bytes to a BGR array, or (None, reason).

    Never raises: this is fed by an HTTP body and by files dropped in a watched
    directory, and neither is trustworthy.
    """
    if not OPENCV_AVAILABLE:
        return None, f"opencv is not installed: {OPENCV_ERROR}"
    if not data:
        return None, "empty image"
    try:
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    except Exception as exc:  # noqa: BLE001 - malformed input is a 400, not a 500
        return None, f"could not decode image: {exc}"
    if image is None:
        return None, "not a readable image"
    return image, None


def encode_jpeg(image, quality=80):
    """BGR array to JPEG bytes, for storing a snapshot."""
    ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    return buf.tobytes() if ok else None
