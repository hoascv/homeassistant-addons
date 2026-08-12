"""Zones: which part of the frame an object has to be in before it counts.

The polygon in most of these is the unit square's left half — x from 0 to 0.5 —
so every expected answer can be checked by looking at the coordinate rather than
by running the code.
"""
import zones

LEFT_HALF = [(0.0, 0.0), (0.5, 0.0), (0.5, 1.0), (0.0, 1.0)]

# Roughly a driveway: a wedge along the bottom-left of the frame, with the street
# above and to the right of it.
DRIVEWAY = [(0.05, 1.0), (0.35, 0.55), (0.75, 0.6), (0.95, 1.0)]


# --- the geometry -------------------------------------------------------------


def test_a_point_inside_and_outside_a_square():
    assert zones.contains(LEFT_HALF, 0.25, 0.5) is True
    assert zones.contains(LEFT_HALF, 0.75, 0.5) is False


def test_a_point_above_and_below_a_square():
    assert zones.contains([(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)], 0.5, 0.1) is False
    assert zones.contains([(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)], 0.5, 0.9) is False


def test_a_concave_shape_does_not_swallow_its_notch():
    """Ray casting earns its keep here: a driveway with a flower bed cut out of
    it is concave, and a bounding-box test would call the notch inside."""
    c_shape = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.6, 1.0),
               (0.6, 0.4), (0.4, 0.4), (0.4, 1.0), (0.0, 1.0)]
    assert zones.contains(c_shape, 0.5, 0.2) is True, "the top bar of the C"
    assert zones.contains(c_shape, 0.5, 0.8) is False, "the notch is outside"


# --- which point of a box decides ---------------------------------------------


def test_a_box_is_judged_where_it_meets_the_ground():
    """The whole reason bottom-centre rather than centre: a van across the road
    has a box whose middle floats over the driveway while its wheels are plainly
    in the street."""
    frame_w, frame_h = 1000, 1000
    # A tall box whose centre is at y=0.5 but whose base is at y=0.9.
    box = [400, 100, 200, 800]
    assert zones.ground_point(box, frame_w, frame_h) == (0.5, 0.9)


def test_a_zero_sized_frame_is_not_a_division_error():
    assert zones.ground_point([0, 0, 10, 10], 0, 0) is None


# --- what a zone allows -------------------------------------------------------


def _det(label="car", box=(400, 400, 200, 400)):
    return {"label": label, "confidence": 0.9, "box": list(box)}


def test_no_zone_allows_everything():
    assert zones.allows(None, "car", [0, 0, 10, 10], 100, 100) is True


def test_a_car_in_the_driveway_is_kept_and_one_in_the_street_is_not():
    zone = {"points": DRIVEWAY, "labels": ("car",)}
    # Wheels at (0.5, 0.95): inside the wedge.
    assert zones.allows(zone, "car", [400, 500, 200, 450], 1000, 1000) is True
    # Wheels at (0.5, 0.3): up in the street.
    assert zones.allows(zone, "car", [400, 100, 200, 200], 1000, 1000) is False


def test_a_label_the_zone_does_not_name_is_unaffected():
    """The point of naming labels: cars are restricted to the driveway, people
    still count anywhere — including walking up the street, which is exactly what
    a camera is for."""
    zone = {"points": DRIVEWAY, "labels": ("car", "truck")}
    street_box = [400, 100, 200, 200]
    assert zones.allows(zone, "car", street_box, 1000, 1000) is False
    assert zones.allows(zone, "person", street_box, 1000, 1000) is True


def test_a_zone_with_no_labels_applies_to_everything():
    zone = {"points": DRIVEWAY, "labels": ()}
    street_box = [400, 100, 200, 200]
    assert zones.allows(zone, "person", street_box, 1000, 1000) is False


def test_filtering_keeps_order_and_drops_only_what_is_outside():
    zone = {"points": DRIVEWAY, "labels": ("car",)}
    detections = [
        _det("car", (400, 100, 200, 200)),    # street
        _det("person", (400, 100, 100, 200)),  # street, but not a car
        _det("car", (400, 500, 200, 450)),    # driveway
    ]
    kept = zones.filter_detections(zone, detections, 1000, 1000)
    assert [d["label"] for d in kept] == ["person", "car"]
    assert kept[1]["box"] == [400, 500, 200, 450]


# --- storage form -------------------------------------------------------------


def test_a_zone_survives_the_round_trip():
    stored = zones.dump(DRIVEWAY, ["car", "TRUCK "])
    parsed = zones.parse(stored)
    assert parsed["points"] == [(round(x, 4), round(y, 4)) for x, y in DRIVEWAY]
    assert parsed["labels"] == ("car", "truck"), "labels are normalised on the way in"


def test_a_mangled_zone_is_no_zone_rather_than_a_crash():
    """Read in a camera thread on every frame. It fails open — everything is
    recorded — because a camera that silently records nothing is worse than one
    that records too much."""
    for bad in ("", None, "not json", "[]", '{"points": "nope"}',
                '{"points": [[0, 0], [1, 1]]}',          # two points is not a shape
                '{"points": [[0, 0], [1, 1], ["a", 0]]}'):
        assert zones.parse(bad) is None


def test_coordinates_outside_the_frame_are_clamped_not_rejected():
    """A drag that ends a few pixels off the edge of the canvas is a normal way
    to draw a shape that reaches the edge, not a mistake to refuse."""
    parsed = zones.parse('{"points": [[-0.2, 0.5], [1.4, 0.5], [0.5, 1.2]]}')
    assert parsed["points"] == [(0.0, 0.5), (1.0, 0.5), (0.5, 1.0)]


# --- through the camera path and the API --------------------------------------


def test_a_street_car_never_reaches_the_recorder(monkeypatch):
    """The point of filtering here rather than on the page: no track, no row, no
    snapshot, no event — not a filtered view of things already paid for."""
    import numpy as np

    import capture

    recorded = []

    class Detector:
        last_latency_ms = 5.0

        def detect(self, frame, confidence=0.6, labels=None):
            return ([
                {"label": "car", "confidence": 0.9, "box": [400, 100, 200, 200]},   # street
                {"label": "car", "confidence": 0.9, "box": [400, 500, 200, 450]},   # driveway
                {"label": "person", "confidence": 0.9, "box": [400, 100, 100, 200]},  # street
            ], None)

    worker = capture.CameraWorker(
        "drive", "rtsp://x", Detector(),
        lambda camera, dets, frame: recorded.append(dets) or [1] * len(dets),
        motion_threshold=0,
        zone=zones.dump(DRIVEWAY, ["car"]),
    )
    worker._consider(np.zeros((1000, 1000, 3), dtype=np.uint8))

    assert len(recorded) == 1
    kept = [(d["label"], d["box"][1]) for d in recorded[0]]
    assert ("car", 100) not in kept, "the street car was recorded"
    assert ("car", 500) in kept, "the driveway car was dropped"
    assert ("person", 100) in kept, "a person in the street is still news"
    assert worker.frames_filtered == 1
    assert worker.status()["zone_active"] is True


def test_a_camera_with_no_zone_records_everything(monkeypatch):
    import numpy as np

    import capture

    recorded = []

    class Detector:
        last_latency_ms = 5.0

        def detect(self, frame, confidence=0.6, labels=None):
            return ([{"label": "car", "confidence": 0.9, "box": [400, 100, 200, 200]}], None)

    worker = capture.CameraWorker(
        "drive", "rtsp://x", Detector(),
        lambda camera, dets, frame: recorded.append(dets) or [1],
        motion_threshold=0,
    )
    worker._consider(np.zeros((1000, 1000, 3), dtype=np.uint8))
    assert len(recorded[0]) == 1
    assert worker.frames_filtered == 0


def test_a_zone_can_be_drawn_cleared_and_read_back(client, db_path):
    saved = client.put("/api/cameras/drive/zone",
                       json={"points": DRIVEWAY, "labels": ["car", "truck"]})
    assert saved.status_code == 200, saved.get_json()

    camera = next(c for c in client.get("/api/cameras").get_json()["cameras"]
                  if c["id"] == "drive")
    assert len(camera["zone"]["points"]) == 4
    assert camera["zone"]["labels"] == ["car", "truck"]

    cleared = client.delete("/api/cameras/drive/zone")
    assert cleared.status_code == 200
    camera = next(c for c in client.get("/api/cameras").get_json()["cameras"]
                  if c["id"] == "drive")
    assert camera["zone"] is None


def test_a_shape_that_is_not_a_shape_is_refused(client, db_path):
    assert client.put("/api/cameras/drive/zone", json={"points": []}).status_code == 400
    assert client.put("/api/cameras/drive/zone",
                      json={"points": [[0, 0], [1, 1]]}).status_code == 400
    assert client.put("/api/cameras/drive/zone",
                      json={"points": [["a", 0], [1, 1], [0, 1]]}).status_code == 400


def test_a_label_the_detector_does_not_know_is_refused(client, db_path):
    """It would restrict nothing and look exactly like the zone being ignored."""
    refused = client.put("/api/cameras/drive/zone",
                         json={"points": DRIVEWAY, "labels": ["lorry"]})
    assert refused.status_code == 400
    assert "lorry" in refused.get_json()["error"]


def test_drawing_a_zone_is_behind_the_access_gate(direct_client, db_path):
    assert direct_client.put("/api/cameras/drive/zone",
                             json={"points": DRIVEWAY}).status_code == 401
    assert direct_client.delete("/api/cameras/drive/zone").status_code == 401


def test_the_page_offers_the_editor(client, db_path):
    html = client.get("/").get_data(as_text=True)
    assert "Where to look" in html
    assert "api/cameras/" in html
    assert 'fetch("/api/cameras' not in html, "absolute URL would break ingress"


def test_a_saved_zone_is_still_reported_while_the_camera_is_running(client, db_path,
                                                                    monkeypatch):
    """The bug this pins: the thread's own status carried a `zone` flag, and the
    live state is merged *over* the stored row — so a running camera replaced its
    saved shape with a boolean, the parse failed, and the page said "nothing set"
    about a zone that was in force the whole time."""
    import app as hub

    client.put("/api/cameras/drive/zone", json={"points": DRIVEWAY, "labels": ["car"]})

    # A real worker's status, not a hand-written one: the collision was between
    # two keys that nobody was comparing side by side, so the test has to use
    # whatever the worker actually reports rather than a copy of it.
    import capture

    worker = capture.CameraWorker("drive", "rtsp://x", None, lambda *a: None,
                                  zone=zones.dump(DRIVEWAY, ["car"]))

    class RunningCapture:
        def status(self):
            return [worker.status()]

        def metrics(self):
            return {}

    monkeypatch.setattr(hub, "get_capture", lambda: RunningCapture())
    camera = next(c for c in client.get("/api/cameras").get_json()["cameras"]
                  if c["id"] == "drive")

    assert camera["zone"] is not None, "a live camera hid its own zone"
    assert len(camera["zone"]["points"]) == 4
    assert camera["zone"]["labels"] == ["car"]


def test_the_page_can_show_what_a_zone_has_dropped(client, db_path, monkeypatch):
    """"An area is set", "an area is set in the wrong place" and "a quiet
    driveway" look identical without this number."""
    import app as hub
    import capture

    client.put("/api/cameras/drive/zone", json={"points": DRIVEWAY, "labels": ["car"]})

    worker = capture.CameraWorker("drive", "rtsp://x", None, lambda *a: None,
                                  zone=zones.dump(DRIVEWAY, ["car"]))
    worker.frames_filtered = 7

    class RunningCapture:
        def status(self):
            return [worker.status()]

        def metrics(self):
            return {}

    monkeypatch.setattr(hub, "get_capture", lambda: RunningCapture())
    camera = next(c for c in client.get("/api/cameras").get_json()["cameras"]
                  if c["id"] == "drive")

    assert camera["frames_filtered"] == 7
    assert camera["zone_active"] is True
    assert "dropped outside it" in client.get("/").get_data(as_text=True)
