"""Where on a camera an object has to be before it counts.

A camera pointed at a driveway usually sees the street as well, and a car parked
across the road is not an event. The fix is a shape: an area of the frame, drawn
once, that certain labels have to be inside before they are recorded at all.

Two decisions the rest of this file rests on.

**Coordinates are relative**, 0..1 of the frame's width and height, never pixels.
A camera switched from its substream to its main stream, or replaced with a
different model, changes resolution and would otherwise silently move the zone —
a driveway that quietly became the roof. Relative coordinates survive that; the
only thing that invalidates them is moving the camera, which invalidates the
drawing anyway.

**A box is tested by its bottom edge, not its middle.** An object stands on the
ground at the bottom of its box, and that is the point that is actually somewhere:
a van across the road has a box whose centre floats above the driveway boundary
while its wheels are plainly in the street. Bottom-centre also degrades sensibly
for a person — feet, not their head over a fence.
"""
from __future__ import annotations

import json

# The labels a zone applies to, when it does not say. Empty means every label,
# which is the shape most people mean by "only look at the driveway".
ALL_LABELS = ()


def parse(raw):
    """A stored zone as {"points": [[x, y], ...], "labels": [...]}, or None.

    Anything unparseable is no zone rather than an error: this is read in a
    camera thread on every frame, and a mangled row must not stop a camera from
    watching. It fails open — everything is recorded — because the alternative
    is a camera that silently records nothing.
    """
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    points = data.get("points")
    if not isinstance(points, list) or len(points) < 3:
        return None
    cleaned = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return None
        try:
            x, y = float(point[0]), float(point[1])
        except (TypeError, ValueError):
            return None
        cleaned.append((min(1.0, max(0.0, x)), min(1.0, max(0.0, y))))
    labels = data.get("labels")
    labels = tuple(str(l).strip().lower() for l in labels if str(l).strip()) if isinstance(labels, list) else ALL_LABELS
    return {"points": cleaned, "labels": labels}


def dump(points, labels=()):
    """The stored form. Coordinates are rounded to four places — a pixel on an
    8K frame — which keeps the row readable and diffable."""
    return json.dumps({
        "points": [[round(float(x), 4), round(float(y), 4)] for x, y in points],
        "labels": [str(l).strip().lower() for l in labels if str(l).strip()],
    })


def contains(polygon, x, y):
    """Is the relative point (x, y) inside this polygon?

    Ray casting: count how many edges a ray to the right crosses. Odd is inside.
    Written out rather than reaching for cv2.pointPolygonTest because it is four
    lines, it works on a plain list of tuples, and it can be tested with a square
    whose answers a reader can check by looking at them.
    """
    inside = False
    count = len(polygon)
    for i in range(count):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % count]
        # The edge must straddle the ray's height. The strict/non-strict pair
        # is what stops a vertex exactly on the ray being counted twice.
        if (y1 > y) != (y2 > y):
            crossing = x1 + (y - y1) / (y2 - y1) * (x2 - x1)
            if x < crossing:
                inside = not inside
    return inside


def ground_point(box, width, height):
    """Where a detection touches the ground, in relative coordinates.

    Bottom-centre of the box — see the module docstring for why that rather than
    the centre.
    """
    x, y, w, h = box
    if not width or not height:
        return None
    return ((x + w / 2) / width, (y + h) / height)


def allows(zone, label, box, width, height):
    """Should this detection be recorded?

    True when there is no zone, when the zone does not cover this label, or when
    the object is standing inside it. The three are deliberately the same answer:
    a zone is a restriction on a few labels, and everything it does not mention
    carries on as before.
    """
    if not zone or not zone["points"]:
        return True
    if zone["labels"] and label not in zone["labels"]:
        return True
    point = ground_point(box, width, height)
    if point is None:
        return True
    return contains(zone["points"], point[0], point[1])


def filter_detections(zone, detections, width, height):
    """The detections a zone lets through, in order."""
    if not zone:
        return detections
    return [
        det for det in detections
        if allows(zone, det.get("label"), det.get("box") or [0, 0, 0, 0], width, height)
    ]
