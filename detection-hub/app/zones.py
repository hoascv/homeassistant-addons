"""Areas of a camera's view, and the rules that decide what counts.

A camera pointed at a driveway sees the street as well, and a car parked across
the road is not an event. So a camera can be given named areas — Driveway, Porch,
Gate — each a shape, a set of object types, and one of two kinds:

- **include**: this label only counts inside this shape.
- **ignore**: this label never counts inside this shape, whatever else says.

Ignore wins. That is what lets a big include area have a hole cut in it — "the
whole drive except the bit of pavement in the corner" — without drawing a
concave shape by hand around the exclusion.

Three decisions the rest of this file rests on.

**Coordinates are relative**, 0..1 of the frame's width and height, never pixels.
A camera switched from its substream to its main stream changes resolution and
would otherwise silently move every shape — a driveway that quietly became the
roof. Only moving the camera invalidates them, which invalidates the drawing
anyway.

**A box is judged by its bottom edge, not its middle.** An object stands on the
ground at the bottom of its box, and that is the point that is actually
somewhere: a van across the road has a box whose centre floats above the driveway
boundary while its wheels are plainly in the street.

**A label nothing mentions is unrestricted.** Areas are a statement about the
labels they name, not about the frame as a whole, so a camera with a car zone
still reports people from anywhere in view. Restricting everything means saying
so — the shape's label list is what carries that.
"""
from __future__ import annotations

import json

INCLUDE = "include"
IGNORE = "ignore"
KINDS = (INCLUDE, IGNORE)


def parse_points(raw):
    """A points list as [(x, y), ...], or None if it is not a usable shape.

    Fewer than three points is not an area. Coordinates outside the frame are
    clamped rather than refused: a drag that ends a few pixels off the edge of
    the canvas is a normal way to draw a shape that reaches the edge.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return None
    if not isinstance(raw, list) or len(raw) < 3:
        return None
    points = []
    for point in raw:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return None
        try:
            x, y = float(point[0]), float(point[1])
        except (TypeError, ValueError):
            return None
        points.append((min(1.0, max(0.0, x)), min(1.0, max(0.0, y))))
    return points


def parse_labels(raw):
    """The object types a shape applies to, lowercased. Empty means every type."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return ()
    if not isinstance(raw, list):
        return ()
    return tuple(str(l).strip().lower() for l in raw if str(l).strip())


def parse(row):
    """One stored zone row as a dict the matcher can use, or None.

    Unparseable is *no zone* rather than an error. These are read in a camera
    thread on every detection, and a mangled row must not stop a camera watching:
    it fails open, because a camera that silently records nothing is a worse
    failure than one that records too much.
    """
    if not row:
        return None
    points = parse_points(row["points"] if "points" in row.keys() else None)
    if points is None:
        return None
    kind = (row["kind"] if "kind" in row.keys() else INCLUDE) or INCLUDE
    return {
        "id": row["id"] if "id" in row.keys() else None,
        "camera": row["camera"] if "camera" in row.keys() else None,
        "name": (row["name"] if "name" in row.keys() else "") or "",
        "kind": kind if kind in KINDS else INCLUDE,
        "points": points,
        "labels": parse_labels(row["labels"] if "labels" in row.keys() else None),
    }


def parse_all(rows):
    """Every usable zone from a set of rows, grouped by camera."""
    by_camera = {}
    for row in rows or []:
        zone = parse(row)
        if zone:
            by_camera.setdefault(zone["camera"], []).append(zone)
    return by_camera


def dump_points(points):
    """The stored form: four decimal places, which is a pixel on an 8K frame and
    keeps the row readable."""
    return json.dumps([[round(float(x), 4), round(float(y), 4)] for x, y in points])


def dump_labels(labels):
    return json.dumps([str(l).strip().lower() for l in labels or [] if str(l).strip()])


def contains(polygon, x, y):
    """Is the relative point (x, y) inside this polygon?

    Ray casting: count how many edges a ray to the right crosses. Odd is inside.
    Written out rather than reaching for cv2.pointPolygonTest because it is a few
    lines, works on plain tuples, and can be tested with a square whose answers a
    reader can check by looking at them.
    """
    inside = False
    count = len(polygon)
    for i in range(count):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % count]
        # The edge must straddle the ray's height. The strict/non-strict pair is
        # what stops a vertex exactly on the ray being counted twice.
        if (y1 > y) != (y2 > y):
            crossing = x1 + (y - y1) / (y2 - y1) * (x2 - x1)
            if x < crossing:
                inside = not inside
    return inside


def ground_point(box, width, height):
    """Where a detection meets the ground, in relative coordinates — the bottom
    centre of its box. See the module docstring for why not the centre."""
    x, y, w, h = box
    if not width or not height:
        return None
    return ((x + w / 2) / width, (y + h) / height)


def _applies(zone, label):
    return not zone["labels"] or label in zone["labels"]


def evaluate(zone_list, label, box, width, height):
    """Should this detection be kept, and which area was it in?

    Returns (keep, zone_name). The order is the whole rule set:

    1. An **ignore** area covering this label, containing the object, drops it —
       whatever any include area says. Ignore wins so a hole can be cut in a
       larger shape.
    2. Otherwise, if any **include** area covers this label, the object has to be
       inside one of them, and that one names it.
    3. A label no area mentions is unrestricted and unnamed.
    """
    point = ground_point(box, width, height)
    if point is None or not zone_list:
        return True, None
    x, y = point

    relevant = [z for z in zone_list if _applies(z, label)]
    for zone in relevant:
        if zone["kind"] == IGNORE and contains(zone["points"], x, y):
            return False, None

    includes = [z for z in relevant if z["kind"] == INCLUDE]
    if not includes:
        return True, None
    for zone in includes:
        if contains(zone["points"], x, y):
            return True, zone["name"] or None
    return False, None


def filter_detections(zone_list, detections, width, height):
    """The detections these areas let through, each stamped with the area it was
    in. The stamp is what reaches the database and the Home Assistant event, so
    an automation can act on "a person in the Porch" rather than on a camera."""
    if not zone_list:
        return detections
    kept = []
    for det in detections:
        keep, name = evaluate(
            zone_list, det.get("label"), det.get("box") or [0, 0, 0, 0], width, height
        )
        if keep:
            det["zone"] = name
            kept.append(det)
    return kept
