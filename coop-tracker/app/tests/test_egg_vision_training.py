import base64

import pytest

import app as coopapp
from test_egg_vision import _synthetic_box_photo, _tiny_data_uri

pytestmark_cv = pytest.mark.skipif(
    not (coopapp.OPENCV_AVAILABLE and coopapp.SKLEARN_AVAILABLE), reason="opencv/sklearn not installed"
)


@pytest.fixture
def nesting_box(client):
    return client.post("/api/nesting-boxes", json={"name": "Coop A", "width_mm": 320}).get_json()


def _sample_payload(photo, box, eggs, source=None):
    """Builds a POST /api/vision/eggs/sample body. `eggs` is a list of
    dicts with cx/cy/width_px/height_px/angle/size/added, matching the
    corrected_result shape app.js sends."""
    box_walls = {"left": 80, "top": 40, "right": 1080, "bottom": 860}
    payload = {
        "photo": photo,
        "original": {"box_id": box["id"], "box_width_mm": box["width_mm"], "box_walls": box_walls, "eggs": eggs},
        "corrected": {"box_id": box["id"], "box_width_mm": box["width_mm"], "box_walls": box_walls, "eggs": eggs},
    }
    if source:
        payload["source"] = source
    return payload


# --- Sample storage gating ---


def test_sample_disabled_when_training_off_and_not_wizard(client, set_options, nesting_box):
    set_options(egg_vision_enabled=True)
    res = client.post(
        "/api/vision/eggs/sample", json=_sample_payload(_tiny_data_uri(), nesting_box, [])
    )
    assert res.get_json()["status"] == "disabled"


def test_sample_stored_when_training_enabled(client, set_options, nesting_box):
    set_options(egg_vision_enabled=True, egg_vision_training_enabled=True)
    photo = _synthetic_box_photo(box=(80, 40, 1080, 860), eggs=[(400, 300, 60, 80, 0)])
    eggs = [{"cx": 400, "cy": 300, "width_px": 120, "height_px": 160, "angle": 0, "size": "M", "added": False}]
    res = client.post("/api/vision/eggs/sample", json=_sample_payload(photo, nesting_box, eggs))
    body = res.get_json()
    assert res.status_code == 201
    assert body["status"] == "stored"
    assert body["sample_count"] == 1


def test_sample_stored_via_wizard_even_when_training_disabled(client, set_options, nesting_box):
    set_options(egg_vision_enabled=True, egg_vision_training_enabled=False)
    photo = _synthetic_box_photo(box=(80, 40, 1080, 860), eggs=[])
    res = client.post(
        "/api/vision/eggs/sample", json=_sample_payload(photo, nesting_box, [], source="wizard")
    )
    assert res.get_json()["status"] == "stored"


def test_sample_rejects_invalid_corrected_payload(client, set_options, nesting_box):
    set_options(egg_vision_enabled=True, egg_vision_training_enabled=True)
    res = client.post(
        "/api/vision/eggs/sample",
        json={"photo": _tiny_data_uri(), "original": {}, "corrected": {"eggs": "not-a-list"}},
    )
    assert res.status_code == 400


def test_sample_retention_pruning(client, set_options, nesting_box, conn):
    set_options(egg_vision_enabled=True, egg_vision_training_enabled=True, egg_vision_training_retention_count=3)
    photo = _synthetic_box_photo(box=(80, 40, 1080, 860), eggs=[])
    for _ in range(5):
        client.post("/api/vision/eggs/sample", json=_sample_payload(photo, nesting_box, []))
    count = conn.execute("SELECT COUNT(*) FROM egg_vision_samples").fetchone()[0]
    assert count == 3


# --- Training ---


def test_train_reports_insufficient_samples(client, set_options, nesting_box):
    set_options(egg_vision_enabled=True)
    res = client.post("/api/vision/train")
    body = res.get_json()
    assert body["status"] == "insufficient_samples"
    assert body["sample_count"] == 0


def test_train_clear_deletes_samples_not_model(client, set_options, nesting_box, conn):
    set_options(egg_vision_enabled=True, egg_vision_training_enabled=True)
    photo = _synthetic_box_photo(box=(80, 40, 1080, 860), eggs=[(400, 300, 60, 80, 0)])
    eggs = [{"cx": 400, "cy": 300, "width_px": 120, "height_px": 160, "angle": 0, "size": "M", "added": False}]
    client.post("/api/vision/eggs/sample", json=_sample_payload(photo, nesting_box, eggs))
    conn.execute(
        "INSERT INTO egg_vision_models (trained_at, trained_on_sample_count, classifier_blob) "
        "VALUES ('2024-01-01', 30, NULL)"
    )
    conn.commit()

    res = client.post("/api/vision/train/clear")
    assert res.get_json()["status"] == "cleared"
    assert conn.execute("SELECT COUNT(*) FROM egg_vision_samples").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM egg_vision_models").fetchone()[0] == 1


@pytestmark_cv
class TestEggVisionTrainingRealPipeline:
    def _store_consistent_samples(self, client, box, count):
        """Stores `count` samples that consistently keep one clean egg and
        reject a deliberately-elongated (merged-looking) blob — enough
        signal for the classifier to learn a boundary tighter than the
        fixed EGG_MAX_ASPECT cutoff, and enough sized examples for the
        size model."""
        for i in range(count):
            cx = 400 + (i % 5) * 10  # slight jitter so contours aren't bit-identical every time
            photo = _synthetic_box_photo(
                box=(80, 40, 1080, 860),
                eggs=[(cx, 300, 60, 80, 0), (750, 300, 150, 40, 0)],  # clean egg + elongated blob
            )
            eggs = [
                {"cx": cx, "cy": 300, "width_px": 120, "height_px": 160, "angle": 0, "size": "M", "added": False}
            ]
            client.post("/api/vision/eggs/sample", json=_sample_payload(photo, box, eggs))

    def test_train_produces_classifier_and_size_model(self, client, set_options, nesting_box):
        set_options(egg_vision_enabled=True, egg_vision_training_enabled=True)
        self._store_consistent_samples(client, nesting_box, coopapp.EGG_VISION_MIN_TRAINING_SAMPLES)

        res = client.post("/api/vision/train")
        body = res.get_json()
        assert body["status"] == "trained"
        assert body["classifier_trained"] is True
        assert body["classifier_positive_count"] >= coopapp.EGG_VISION_MIN_CLASSIFIER_POS
        assert body["classifier_negative_count"] >= coopapp.EGG_VISION_MIN_CLASSIFIER_NEG

        status = client.get("/api/vision/train/status").get_json()
        assert status["model"]["has_classifier"] is True

    def test_zero_behavior_change_without_classifier_arg(self):
        # Calling _analyze_egg_photo with no classifier (every install
        # that hasn't opted in and trained) must still work exactly like
        # the fixed-threshold path — this is the regression check that
        # the ML addition didn't disturb the default behavior.
        photo = _synthetic_box_photo(
            box=(80, 40, 1080, 860), eggs=[(400, 300, 60, 80, 10), (700, 250, 65, 85, 5)]
        )
        photo_bytes = base64.b64decode(photo.split(",", 1)[1])
        result = coopapp._analyze_egg_photo(photo_bytes, {"id": 1, "width_mm": 320})
        assert result["status"] == "ok"
        assert len(result["eggs"]) == 2

    def test_box_classifier_trains_and_resolves_ambiguous_photo(self, client, set_options):
        set_options(egg_vision_enabled=True, egg_vision_training_enabled=True)
        box_a = client.post("/api/nesting-boxes", json={"name": "Box A", "width_mm": 320}).get_json()
        box_b = client.post("/api/nesting-boxes", json={"name": "Box B", "width_mm": 320}).get_json()

        # Two visually distinct "boxes" via background/box color, enough
        # samples each to clear EGG_VISION_BOX_ID_MIN_SAMPLES_PER_BOX, and
        # enough total to clear EGG_VISION_MIN_TRAINING_SAMPLES so
        # /api/vision/train actually attempts training at all.
        per_box = max(coopapp.EGG_VISION_BOX_ID_MIN_SAMPLES_PER_BOX + 2, coopapp.EGG_VISION_MIN_TRAINING_SAMPLES // 2 + 1)
        for _ in range(per_box):
            photo_a = _synthetic_box_photo(box=(80, 40, 1080, 860), eggs=[])
            client.post("/api/vision/eggs/sample", json=_sample_payload(photo_a, box_a, []))

        for _ in range(per_box):
            import cv2
            import numpy as np

            img = np.full((900, 1200, 3), 40, np.uint8)  # dark background — visually distinct from box_a's photos
            cv2.rectangle(img, (80, 40), (1080, 860), (20, 20, 20), -1)
            ok, buf = cv2.imencode(".jpg", img)
            photo_b = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()
            client.post("/api/vision/eggs/sample", json=_sample_payload(photo_b, box_b, []))

        train_body = client.post("/api/vision/train").get_json()
        assert train_body["box_classifier_trained"] is True

        # A photo matching box_a's appearance, submitted with no box_id,
        # should now auto-resolve instead of requiring confirm_box.
        photo = _synthetic_box_photo(box=(80, 40, 1080, 860), eggs=[])
        body = client.post("/api/vision/eggs", json={"photo": photo}).get_json()
        assert body["status"] != "confirm_box"
        assert body["box"]["id"] == box_a["id"]
