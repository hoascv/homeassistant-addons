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


# --- Real CV pipeline (skipped if opencv isn't installed) ---

pytestmark_cv = pytest.mark.skipif(not coopapp.OPENCV_AVAILABLE, reason="opencv not installed")


def _synthetic_photo(width=1200, height=900, coin=None, eggs=()):
    """Builds a synthetic test photo with cv2 primitives: a light
    background, an optional gray coin circle, and dark egg-shaped
    ellipses. Returns a data URI, the same shape the real upload flow
    produces. `eggs` is a list of (cx, cy, semi_x, semi_y, angle)."""
    import cv2
    import numpy as np

    img = np.full((height, width, 3), 230, np.uint8)
    if coin:
        cx, cy, r = coin
        cv2.circle(img, (cx, cy), r, (140, 140, 140), -1)
    for cx, cy, semi_x, semi_y, angle in eggs:
        cv2.ellipse(img, (cx, cy), (semi_x, semi_y), angle, 0, 360, (90, 70, 60), -1)
    ok, buf = cv2.imencode(".jpg", img)
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


@pytestmark_cv
class TestEggVisionRealDetection:
    def test_counts_well_separated_eggs_and_finds_coin(self, client, set_options):
        set_options(egg_vision_enabled=True, egg_vision_coin_diameter_mm=24.5)
        photo = _synthetic_photo(
            coin=(1080, 100, 40),
            eggs=[(200, 200, 60, 80, 10), (450, 300, 55, 75, -15), (700, 250, 65, 85, 5)],
        )
        res = client.post("/api/vision/eggs", json={"photo": photo})
        body = res.get_json()
        assert body["status"] == "ok"
        assert len(body["eggs"]) == 3
        assert body["coin"] is not None
        assert all(e["size"] in coopapp.EGG_SIZE_CODES for e in body["eggs"])

    def test_coin_not_found_when_no_coin_drawn(self, client, set_options):
        set_options(egg_vision_enabled=True)
        photo = _synthetic_photo(coin=None, eggs=[(200, 200, 60, 80, 10)])
        body = client.post("/api/vision/eggs", json={"photo": photo}).get_json()
        assert body["status"] == "coin_not_found"
        assert body["coin"] is None
        assert len(body["eggs"]) == 1
        assert "size" not in body["eggs"][0]

    def test_no_eggs_found_on_coin_only_photo(self, client, set_options):
        set_options(egg_vision_enabled=True)
        photo = _synthetic_photo(coin=(1080, 100, 40), eggs=[])
        body = client.post("/api/vision/eggs", json={"photo": photo}).get_json()
        assert body["status"] == "no_eggs_found"
        assert body["eggs"] == []

    def test_size_classification_matches_known_geometry(self, client, set_options):
        # coin r=40px, diameter_mm=24.5 -> px_per_mm = 80/24.5 = 3.265.
        # An egg width of 145px -> ~44.4mm, inside the M|L..L|XL band ("L").
        set_options(egg_vision_enabled=True, egg_vision_coin_diameter_mm=24.5)
        photo = _synthetic_photo(coin=(1080, 100, 40), eggs=[(400, 300, 72, 95, 0)])
        body = client.post("/api/vision/eggs", json={"photo": photo}).get_json()
        assert body["status"] == "ok"
        egg = body["eggs"][0]
        px_per_mm = (2 * body["coin"]["r"]) / body["coin_diameter_mm"]
        width_mm = egg["width_px"] / px_per_mm
        assert egg["size"] == coopapp._egg_size_code(width_mm)

    def test_excludes_near_touching_merged_egg_blob(self, client, set_options):
        # Two eggs placed close enough to merge into one contour (centers
        # 119px apart, each ~120px wide) produce an elongated blob that
        # must be excluded rather than counted as one oddly-sized egg —
        # only the separate third egg should come through.
        set_options(egg_vision_enabled=True)
        photo = _synthetic_photo(
            coin=(1080, 100, 40),
            eggs=[(240, 300, 60, 80, 0), (359, 300, 60, 80, 0), (700, 300, 60, 80, 0)],
        )
        body = client.post("/api/vision/eggs", json={"photo": photo}).get_json()
        assert body["status"] == "ok"
        assert len(body["eggs"]) == 1
        assert body["eggs"][0]["cx"] == pytest.approx(700, abs=5)

    def test_undecodable_photo_reports_error(self, client, set_options):
        # Valid base64, valid data-URI shape, but not actually image bytes
        # — passes _decode_photo_data_uri, fails at cv2.imdecode.
        set_options(egg_vision_enabled=True)
        garbage = base64.b64encode(b"not a real image, just garbage bytes").decode()
        body = client.post(
            "/api/vision/eggs", json={"photo": f"data:image/jpeg;base64,{garbage}"}
        ).get_json()
        assert body["status"] == "error"
        assert "decode" in body["error"]

    def test_excludes_tiny_noise_blob(self, client, set_options):
        # A speck that survives morphological cleanup as its own contour
        # (radius 18px, area ~970px²) but still falls under
        # EGG_MIN_AREA_FRACTION*photo_area (1620px²) — exercises the area
        # filter itself, not just the morphological-opening cleanup step.
        import cv2
        import numpy as np

        img = np.full((900, 1200, 3), 230, np.uint8)
        cv2.circle(img, (1080, 100), 40, (140, 140, 140), -1)
        cv2.ellipse(img, (400, 300), (60, 80), 0, 0, 360, (90, 70, 60), -1)
        cv2.circle(img, (200, 200), 18, (90, 70, 60), -1)  # noise speck
        ok, buf = cv2.imencode(".jpg", img)
        photo = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()

        set_options(egg_vision_enabled=True)
        body = client.post("/api/vision/eggs", json={"photo": photo}).get_json()
        assert body["status"] == "ok"
        assert len(body["eggs"]) == 1

    def test_analysis_exception_degrades_to_error_status(self, client, set_options, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("simulated cv2 failure")

        monkeypatch.setattr(coopapp, "_analyze_egg_photo", _boom)
        set_options(egg_vision_enabled=True)
        res = client.post("/api/vision/eggs", json={"photo": _tiny_data_uri()})
        assert res.status_code == 200
        body = res.get_json()
        assert body["status"] == "error"
        assert body["error"] == "simulated cv2 failure"
