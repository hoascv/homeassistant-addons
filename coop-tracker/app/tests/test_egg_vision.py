import base64

import pytest

import app as coopapp


def _tiny_data_uri():
    # A minimal valid 1x1 JPEG — enough to exercise the decode/size-limit
    # paths without needing opencv installed.
    tiny_jpeg = base64.b64decode(
        "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAMCAgICAgMCAgIDAwMDBAYEBAQEBAgGBgUGCQgKCgkICQkKDA8MCgsOCwkJDRENDg8QEBEQCgwSExIQEw8QEBD/2wBDAQMDAwQDBAgEBAgQCwkLEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBD/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAj/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCdABmX/9k="
    )
    return "data:image/jpeg;base64," + base64.b64encode(tiny_jpeg).decode()


# --- Gating logic (unconditional — no opencv required) ---


def test_egg_vision_disabled_by_default(client):
    res = client.post("/api/vision/eggs", json={"photo": _tiny_data_uri()})
    body = res.get_json()
    assert body["status"] == "disabled"
    assert body["eggs"] == []


def test_egg_vision_reports_libs_available_flag(client, set_options):
    set_options(egg_vision_enabled=True)
    res = client.post("/api/vision/eggs", json={"photo": _tiny_data_uri()})
    body = res.get_json()
    if coopapp.OPENCV_AVAILABLE:
        assert body["status"] != "libs_unavailable"
    else:
        assert body["status"] == "libs_unavailable"
        assert body["error"] == coopapp.OPENCV_ERROR


def test_egg_vision_libs_unavailable_forced(client, set_options, monkeypatch):
    monkeypatch.setattr(coopapp, "OPENCV_AVAILABLE", False)
    set_options(egg_vision_enabled=True)
    res = client.post("/api/vision/eggs", json={"photo": _tiny_data_uri()})
    assert res.get_json()["status"] == "libs_unavailable"


def test_egg_vision_rejects_invalid_photo_data(client, set_options):
    set_options(egg_vision_enabled=True)
    res = client.post("/api/vision/eggs", json={"photo": "not-a-data-uri"})
    assert res.status_code == 400


def test_egg_vision_rejects_oversized_photo(client, set_options, monkeypatch):
    monkeypatch.setattr(coopapp, "MAX_EGG_VISION_PHOTO_BYTES", 10)
    set_options(egg_vision_enabled=True)
    res = client.post("/api/vision/eggs", json={"photo": _tiny_data_uri()})
    assert res.status_code == 400
    assert "too large" in res.get_json()["error"]


def test_egg_size_code_boundaries():
    s_m, m_l, l_xl = coopapp.EGG_SIZE_MM_BOUNDS
    assert coopapp._egg_size_code(s_m - 0.1) == "S"
    assert coopapp._egg_size_code(s_m + 0.1) == "M"
    assert coopapp._egg_size_code(m_l + 0.1) == "L"
    assert coopapp._egg_size_code(l_xl + 0.1) == "XL"


def test_no_boxes_registered_blocks_analysis(client, set_options):
    set_options(egg_vision_enabled=True)
    res = client.post("/api/vision/eggs", json={"photo": _tiny_data_uri()})
    assert res.get_json()["status"] == "no_boxes_registered"


# --- Nesting box CRUD (unconditional — no opencv required) ---


def test_add_and_list_nesting_boxes(client):
    res = client.post("/api/nesting-boxes", json={"name": "Coop A", "width_mm": 320})
    assert res.status_code == 201
    body = res.get_json()
    assert body["name"] == "Coop A"
    assert body["width_mm"] == 320

    boxes = client.get("/api/nesting-boxes").get_json()
    assert len(boxes) == 1
    assert boxes[0]["name"] == "Coop A"


def test_add_nesting_box_requires_name(client):
    res = client.post("/api/nesting-boxes", json={"width_mm": 320})
    assert res.status_code == 400


def test_add_nesting_box_rejects_non_positive_width(client):
    res = client.post("/api/nesting-boxes", json={"name": "Coop A", "width_mm": 0})
    assert res.status_code == 400


def test_add_nesting_box_rejects_duplicate_name(client):
    client.post("/api/nesting-boxes", json={"name": "Coop A", "width_mm": 320})
    res = client.post("/api/nesting-boxes", json={"name": "coop a", "width_mm": 300})
    assert res.status_code == 400


def test_delete_nesting_box_unlinks_rather_than_cascades(client, conn):
    box_id = client.post("/api/nesting-boxes", json={"name": "Coop A", "width_mm": 320}).get_json()["id"]
    conn.execute(
        "INSERT INTO egg_vision_samples (created_at, photo, image_width, image_height, box_id, "
        "original_detection, corrected_result) VALUES ('2024-01-01', ?, 100, 100, ?, '{}', '{}')",
        (b"fake", box_id),
    )
    conn.commit()

    res = client.delete(f"/api/nesting-boxes/{box_id}")
    assert res.status_code == 204
    assert client.get("/api/nesting-boxes").get_json() == []

    row = conn.execute("SELECT box_id FROM egg_vision_samples").fetchone()
    assert row["box_id"] is None


# --- Real CV pipeline (skipped if opencv/sklearn isn't installed) ---

pytestmark_cv = pytest.mark.skipif(
    not (coopapp.OPENCV_AVAILABLE and coopapp.SKLEARN_AVAILABLE), reason="opencv/sklearn not installed"
)


def _synthetic_box_photo(width=1200, height=900, box=None, eggs=()):
    """Builds a synthetic test photo with cv2 primitives: a light
    background, an optional medium-gray box rectangle, and dark
    egg-shaped ellipses. Returns a data URI, the same shape the real
    upload flow produces. `box` is (left, top, right, bottom); `eggs` is
    a list of (cx, cy, semi_x, semi_y, angle)."""
    import cv2
    import numpy as np

    img = np.full((height, width, 3), 230, np.uint8)
    if box:
        left, top, right, bottom = box
        cv2.rectangle(img, (left, top), (right, bottom), (170, 170, 170), -1)
    for cx, cy, semi_x, semi_y, angle in eggs:
        cv2.ellipse(img, (cx, cy), (semi_x, semi_y), angle, 0, 360, (90, 70, 60), -1)
    ok, buf = cv2.imencode(".jpg", img)
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


@pytest.fixture
def nesting_box(client):
    return client.post("/api/nesting-boxes", json={"name": "Coop A", "width_mm": 320}).get_json()


@pytestmark_cv
class TestSplitEggRegions:
    """Direct unit tests for the touching-egg splitter (v1.32.2) — see
    _split_egg_regions / ARCHITECTURE.md §20.3."""

    @staticmethod
    def _mask(eggs, w=1200, h=900):
        import cv2
        import numpy as np

        mask = np.zeros((h, w), np.uint8)
        for cx, cy, sx, sy, ang in eggs:
            cv2.ellipse(mask, (cx, cy), (sx, sy), ang, 0, 360, 255, -1)
        return mask

    def test_splits_two_touching(self):
        mask = self._mask([(400, 300, 60, 80, 0), (500, 300, 60, 80, 0)])
        assert len(coopapp._split_egg_regions(mask)) == 2

    def test_splits_three_touching(self):
        mask = self._mask([(300, 300, 55, 75, 0), (400, 300, 55, 75, 0), (500, 300, 55, 75, 0)])
        assert len(coopapp._split_egg_regions(mask)) == 3

    def test_single_egg_not_split(self):
        assert len(coopapp._split_egg_regions(self._mask([(400, 300, 60, 80, 0)]))) == 1

    def test_single_elongated_egg_not_over_split(self):
        # A lone elongated egg must stay one region, not split along its
        # long axis into two — the over-segmentation failure mode.
        assert len(coopapp._split_egg_regions(self._mask([(400, 300, 48, 92, 45)]))) == 1

    def test_separated_eggs_pass_through(self):
        mask = self._mask([(250, 300, 60, 80, 0), (800, 400, 60, 80, 0)])
        assert len(coopapp._split_egg_regions(mask)) == 2


@pytestmark_cv
class TestEggVisionRealDetection:
    def test_counts_well_separated_eggs_and_finds_box(self, client, set_options, nesting_box):
        set_options(egg_vision_enabled=True)
        photo = _synthetic_box_photo(
            box=(80, 40, 1120, 860),
            eggs=[(200, 200, 60, 80, 10), (450, 300, 55, 75, -15), (700, 250, 65, 85, 5)],
        )
        res = client.post("/api/vision/eggs", json={"photo": photo})
        body = res.get_json()
        assert body["status"] == "ok"
        assert len(body["eggs"]) == 3
        assert body["box_walls"] is not None
        assert all(e["size"] in coopapp.EGG_SIZE_CODES for e in body["eggs"])

    def test_walls_not_found_when_no_box_drawn(self, client, set_options, nesting_box):
        set_options(egg_vision_enabled=True)
        photo = _synthetic_box_photo(box=None, eggs=[(200, 200, 60, 80, 10)])
        body = client.post("/api/vision/eggs", json={"photo": photo}).get_json()
        assert body["status"] == "walls_not_found"
        assert len(body["eggs"]) == 1
        assert "size" not in body["eggs"][0]

    def test_no_eggs_found_on_box_only_photo(self, client, set_options, nesting_box):
        set_options(egg_vision_enabled=True)
        photo = _synthetic_box_photo(box=(80, 40, 1120, 860), eggs=[])
        body = client.post("/api/vision/eggs", json={"photo": photo}).get_json()
        assert body["status"] == "no_eggs_found"
        assert body["eggs"] == []

    def test_size_classification_matches_known_geometry(self, client, set_options, nesting_box):
        # Box drawn 1000px wide (80 to 1080), width_mm=320 -> px_per_mm=3.125.
        # An egg width of 140px -> 44.8mm, inside the M|L..L|XL band ("L").
        # An axis-aligned synthetic box detects as a zero-slant trapezoid,
        # so the local scale at the egg's row equals the flat span scale.
        set_options(egg_vision_enabled=True)
        photo = _synthetic_box_photo(box=(80, 40, 1080, 860), eggs=[(400, 300, 70, 95, 0)])
        body = client.post("/api/vision/eggs", json={"photo": photo}).get_json()
        assert body["status"] == "ok"
        egg = body["eggs"][0]
        px_per_mm = coopapp._wall_px_per_mm_at(body["box_walls"], egg["cy"], nesting_box["width_mm"])
        width_mm = egg["width_px"] / px_per_mm
        assert egg["size"] == coopapp._egg_size_code(width_mm)

    def test_splits_touching_eggs(self, client, set_options, nesting_box):
        # Two eggs placed close enough to merge into one contour (centers
        # 119px apart, each ~120px wide) must now be SPLIT back into two
        # (v1.32.2, distance-transform peaks + nearest-centre partition —
        # see ARCHITECTURE.md §20.3), not excluded or counted as one
        # oversized egg. With the separate third egg, that's three total.
        set_options(egg_vision_enabled=True)
        photo = _synthetic_box_photo(
            box=(80, 40, 1120, 860),
            eggs=[(240, 300, 60, 80, 0), (359, 300, 60, 80, 0), (700, 300, 60, 80, 0)],
        )
        body = client.post("/api/vision/eggs", json={"photo": photo}).get_json()
        assert body["status"] == "ok"
        assert len(body["eggs"]) == 3
        xs = sorted(round(e["cx"]) for e in body["eggs"])
        assert xs[0] == pytest.approx(240, abs=25)
        assert xs[1] == pytest.approx(359, abs=25)
        assert xs[2] == pytest.approx(700, abs=10)

    def test_splits_vertically_stacked_eggs(self, client, set_options, nesting_box):
        # The user's real photo: two brown eggs touching one above the
        # other, which pre-1.32.2 read as a single "XL" blob.
        set_options(egg_vision_enabled=True)
        photo = _synthetic_box_photo(
            box=(80, 40, 1120, 860),
            eggs=[(500, 320, 60, 80, 0), (500, 450, 60, 80, 0)],
        )
        body = client.post("/api/vision/eggs", json={"photo": photo}).get_json()
        assert body["status"] == "ok"
        assert len(body["eggs"]) == 2

    def test_undecodable_photo_reports_error(self, client, set_options, nesting_box):
        # Valid base64, valid data-URI shape, but not actually image bytes
        # — passes _decode_photo_data_uri, fails at cv2.imdecode.
        set_options(egg_vision_enabled=True)
        garbage = base64.b64encode(b"not a real image, just garbage bytes").decode()
        body = client.post(
            "/api/vision/eggs", json={"photo": f"data:image/jpeg;base64,{garbage}"}
        ).get_json()
        assert body["status"] == "error"
        assert "decode" in body["error"]

    def test_excludes_tiny_noise_blob(self, client, set_options, nesting_box):
        # A speck that survives morphological cleanup as its own contour
        # (radius 18px, area ~970px²) but still falls under
        # EGG_MIN_AREA_FRACTION*photo_area (1620px²) — exercises the area
        # filter itself, not just the morphological-opening cleanup step.
        import cv2
        import numpy as np

        img = np.full((900, 1200, 3), 230, np.uint8)
        cv2.rectangle(img, (80, 40), (1120, 860), (170, 170, 170), -1)
        cv2.ellipse(img, (400, 300), (60, 80), 0, 0, 360, (90, 70, 60), -1)
        cv2.circle(img, (200, 200), 18, (90, 70, 60), -1)  # noise speck
        ok, buf = cv2.imencode(".jpg", img)
        photo = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()

        set_options(egg_vision_enabled=True)
        body = client.post("/api/vision/eggs", json={"photo": photo}).get_json()
        assert body["status"] == "ok"
        assert len(body["eggs"]) == 1

    def test_analysis_exception_degrades_to_error_status(self, client, set_options, monkeypatch, nesting_box):
        def _boom(*a, **k):
            raise RuntimeError("simulated cv2 failure")

        monkeypatch.setattr(coopapp, "_analyze_egg_photo", _boom)
        set_options(egg_vision_enabled=True)
        res = client.post("/api/vision/eggs", json={"photo": _tiny_data_uri()})
        assert res.status_code == 200
        body = res.get_json()
        assert body["status"] == "error"
        assert body["error"] == "simulated cv2 failure"

    def test_finds_brown_egg_on_straw_bedding(self, client, set_options, nesting_box):
        # The real-world failure case that motivated the color-distance
        # pass (1.32.0): a brown egg on pale straw has almost no
        # BRIGHTNESS contrast (the old grayscale Otsu returned "no eggs
        # found" on the user's actual photo) but clear COLOR contrast —
        # the egg is saturated orange-brown, the straw pale yellow.
        # Straw is simulated as a noisy streak texture so this also
        # proves texture noise doesn't fragment into false positives.
        import cv2
        import numpy as np

        rng = np.random.default_rng(42)
        img = np.full((900, 1200, 3), 230, np.uint8)
        straw_base = (150, 200, 210)  # BGR pale yellow — luma ~193
        cv2.rectangle(img, (80, 40), (1120, 860), straw_base, -1)
        for _ in range(3000):
            x1, y1 = int(rng.integers(90, 1110)), int(rng.integers(50, 850))
            ln, ang = int(rng.integers(10, 60)), float(rng.uniform(0, np.pi))
            x2, y2 = int(x1 + ln * np.cos(ang)), int(y1 + ln * np.sin(ang))
            shade = int(rng.integers(-40, 40))
            color = tuple(int(np.clip(c + shade, 0, 255)) for c in straw_base)
            cv2.line(img, (x1, y1), (x2, y2), color, 1)
        # Egg: BGR (110, 150, 215) — luma ~163, only ~30 below the straw
        # (the old pass needed far more), but strongly redder in Lab.
        cv2.ellipse(img, (500, 400), (60, 80), 0, 0, 360, (110, 150, 215), -1)
        ok, buf = cv2.imencode(".jpg", img)
        photo = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()

        set_options(egg_vision_enabled=True)
        body = client.post("/api/vision/eggs", json={"photo": photo}).get_json()
        assert body["status"] == "ok"
        assert len(body["eggs"]) == 1
        assert body["eggs"][0]["cx"] == pytest.approx(500, abs=10)
        assert body["eggs"][0]["cy"] == pytest.approx(400, abs=10)

    def test_explicit_box_id_is_used_over_auto_resolution(self, client, set_options, nesting_box):
        second_box = client.post("/api/nesting-boxes", json={"name": "Coop B", "width_mm": 400}).get_json()
        set_options(egg_vision_enabled=True)
        photo = _synthetic_box_photo(box=(80, 40, 1080, 860), eggs=[])
        body = client.post("/api/vision/eggs", json={"photo": photo, "box_id": second_box["id"]}).get_json()
        assert body["status"] == "no_eggs_found"
        assert body["box"]["id"] == second_box["id"]

    def test_confirm_box_when_multiple_boxes_and_no_id_or_model(self, client, set_options, nesting_box):
        client.post("/api/nesting-boxes", json={"name": "Coop B", "width_mm": 400})
        set_options(egg_vision_enabled=True)
        photo = _synthetic_box_photo(box=(80, 40, 1080, 860), eggs=[])
        body = client.post("/api/vision/eggs", json={"photo": photo}).get_json()
        assert body["status"] == "confirm_box"
        assert len(body["box_candidates"]) == 2
