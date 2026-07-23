import base64
import hashlib
import os
import pickle

import pytest

import app as coopapp
from test_egg_vision import _synthetic_box_photo, _tiny_data_uri

pytestmark_cv = pytest.mark.skipif(
    not (coopapp.OPENCV_AVAILABLE and coopapp.SKLEARN_AVAILABLE), reason="opencv/sklearn not installed"
)

# Pinned hash of the bundled SqueezeNet — see ARCHITECTURE.md §20.1 and
# the repo-integrity test at the bottom of this file.
BUNDLED_EMBED_MODEL_SHA256 = "1eeff551a67ae8d565ca33b572fc4b66e3ef357b0eb2863bb9ff47a918cc4088"

TRAPEZOID_WALLS = {
    "top_y": 40,
    "bottom_y": 860,
    "left_top_x": 80,
    "left_bottom_x": 80,
    "right_top_x": 1080,
    "right_bottom_x": 1080,
}


@pytest.fixture
def nesting_box(client):
    return client.post("/api/nesting-boxes", json={"name": "Coop A", "width_mm": 320}).get_json()


@pytest.fixture
def fake_embedder(monkeypatch):
    """Deterministic stand-in for the SqueezeNet embedder: an 8x8
    downsampled grayscale of the image, L2-normalized. Behaves like a
    real embedding for test purposes (similar photos map to similar
    vectors; the light-vs-dark synthetic boxes separate enormously) and
    exercises the centering/centroid/threshold logic end to end without
    needing the ONNX file. Works because both training and prediction
    call the module-level _embed_box_photo by name."""
    import cv2
    import numpy as np

    def fake(img):
        v = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (8, 8)).astype("float32").ravel()
        n = np.linalg.norm(v)
        return v / n if n else v

    monkeypatch.setattr(coopapp, "_embed_box_photo", fake)
    return fake


def _sample_payload(photo, box, eggs, source=None, walls=None):
    """Builds a POST /api/vision/eggs/sample body. `eggs` is a list of
    dicts with cx/cy/width_px/height_px/angle/size/added, matching the
    corrected_result shape app.js sends."""
    box_walls = walls if walls is not None else dict(TRAPEZOID_WALLS)
    payload = {
        "photo": photo,
        "original": {"box_id": box["id"], "box_width_mm": box["width_mm"], "box_walls": box_walls, "eggs": eggs},
        "corrected": {"box_id": box["id"], "box_width_mm": box["width_mm"], "box_walls": box_walls, "eggs": eggs},
    }
    if source:
        payload["source"] = source
    return payload


def _egg_correction(cx=400, cy=300):
    return {"cx": cx, "cy": cy, "width_px": 120, "height_px": 160, "angle": 0, "size": "M", "added": False}


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
    res = client.post("/api/vision/eggs/sample", json=_sample_payload(photo, nesting_box, [_egg_correction()]))
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


# --- Training gating ---


def test_train_reports_insufficient_samples_only_at_zero(client, set_options, nesting_box):
    set_options(egg_vision_enabled=True)
    res = client.post("/api/vision/train")
    body = res.get_json()
    assert body["status"] == "insufficient_samples"
    assert body["sample_count"] == 0


def test_train_clear_deletes_samples_not_model(client, set_options, nesting_box, conn):
    set_options(egg_vision_enabled=True, egg_vision_training_enabled=True)
    photo = _synthetic_box_photo(box=(80, 40, 1080, 860), eggs=[(400, 300, 60, 80, 0)])
    client.post("/api/vision/eggs/sample", json=_sample_payload(photo, nesting_box, [_egg_correction()]))
    conn.execute(
        "INSERT INTO egg_vision_models (trained_at, trained_on_sample_count, classifier_blob) "
        "VALUES ('2024-01-01', 30, NULL)"
    )
    conn.commit()

    res = client.post("/api/vision/train/clear")
    assert res.get_json()["status"] == "cleared"
    assert conn.execute("SELECT COUNT(*) FROM egg_vision_samples").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM egg_vision_models").fetchone()[0] == 1


# --- Wall geometry helpers (no opencv needed) ---


def test_wall_px_per_mm_uses_local_row_scale():
    # Converging trapezoid: 20px span at the top, 100px at the bottom.
    walls = {
        "top_y": 0,
        "bottom_y": 100,
        "left_top_x": 40,
        "left_bottom_x": 0,
        "right_top_x": 60,
        "right_bottom_x": 100,
    }
    assert coopapp._wall_px_per_mm_at(walls, 0, 100) == pytest.approx(0.2)
    assert coopapp._wall_px_per_mm_at(walls, 100, 100) == pytest.approx(1.0)
    assert coopapp._wall_px_per_mm_at(walls, 50, 100) == pytest.approx(0.6)
    # y clamped to the wall segment
    assert coopapp._wall_px_per_mm_at(walls, -50, 100) == pytest.approx(0.2)
    assert coopapp._wall_px_per_mm_at(walls, 999, 100) == pytest.approx(1.0)


def test_wall_px_per_mm_degenerate_geometry_returns_none():
    flat = {"top_y": 50, "bottom_y": 50, "left_top_x": 0, "left_bottom_x": 0, "right_top_x": 10, "right_bottom_x": 10}
    assert coopapp._wall_px_per_mm_at(flat, 50, 100) is None
    crossed = {"top_y": 0, "bottom_y": 100, "left_top_x": 90, "left_bottom_x": 90, "right_top_x": 10, "right_bottom_x": 10}
    assert coopapp._wall_px_per_mm_at(crossed, 50, 100) is None
    ok = {"top_y": 0, "bottom_y": 100, "left_top_x": 0, "left_bottom_x": 0, "right_top_x": 100, "right_bottom_x": 100}
    assert coopapp._wall_px_per_mm_at(ok, 50, None) is None


def test_normalize_box_walls_accepts_pre_1_32_shape():
    old = {"left": 10, "top": 20, "right": 110, "bottom": 220}
    walls = coopapp._normalize_box_walls(old)
    assert walls["left_top_x"] == 10 and walls["left_bottom_x"] == 10
    assert walls["right_top_x"] == 110 and walls["right_bottom_x"] == 110
    assert walls["top_y"] == 20 and walls["bottom_y"] == 220

    new = dict(TRAPEZOID_WALLS)
    assert coopapp._normalize_box_walls(new) is new
    assert coopapp._normalize_box_walls(None) is None
    assert coopapp._normalize_box_walls({"nonsense": 1}) is None


def test_recompute_scales_each_egg_at_its_own_row(client, nesting_box):
    # Same pixel width at two different rows of a converging box must
    # yield different mm — the whole point of slanted walls.
    walls = {
        "top_y": 0,
        "bottom_y": 800,
        "left_top_x": 300,
        "left_bottom_x": 100,
        "right_top_x": 700,
        "right_bottom_x": 900,
    }
    eggs = [
        {"cx": 500, "cy": 0, "width_px": 100, "height_px": 130, "angle": 0, "aspect_ratio": 1.3, "extent": 0.8},
        {"cx": 500, "cy": 800, "width_px": 100, "height_px": 130, "angle": 0, "aspect_ratio": 1.3, "extent": 0.8},
    ]
    body = client.post(
        "/api/vision/eggs/recompute",
        json={"box_id": nesting_box["id"], "box_walls": walls, "eggs": eggs},
    ).get_json()
    top_mm, bottom_mm = body["eggs"][0]["width_mm"], body["eggs"][1]["width_mm"]
    # top span 400px, bottom span 800px, width 320mm: same 100px reads
    # twice as many mm at the (narrower-looking, farther) top row.
    assert top_mm == pytest.approx(100 / (400 / 320))
    assert bottom_mm == pytest.approx(100 / (800 / 320))
    assert top_mm == pytest.approx(2 * bottom_mm)


def test_recompute_accepts_pre_1_32_wall_shape(client, nesting_box):
    body = client.post(
        "/api/vision/eggs/recompute",
        json={
            "box_id": nesting_box["id"],
            "box_walls": {"left": 80, "top": 40, "right": 1080, "bottom": 860},
            "eggs": [{"cx": 400, "cy": 300, "width_px": 120, "height_px": 160, "angle": 0, "aspect_ratio": 1.3, "extent": 0.8}],
        },
    ).get_json()
    assert body["eggs"][0]["width_mm"] == pytest.approx(120 / (1000 / 320))


# --- Box-ID prediction head (fake embedder, no ONNX file needed) ---


def _make_head(centroids, dim=4, mean=None):
    return {
        "format": coopapp.EGG_VISION_BOX_MODEL_FORMAT,
        "kind": "nearest_centroid_cosine",
        "dim": dim,
        "mean": mean if mean is not None else [0.0] * dim,
        "centroids": centroids,
    }


@pytestmark_cv
class TestPredictBoxId:
    def test_confident_match_returns_similarity_and_margin(self, monkeypatch):
        monkeypatch.setattr(coopapp, "_embed_box_photo", lambda img: __import__("numpy").array([1.0, 0, 0, 0], "float32"))
        head = _make_head({1: [1.0, 0, 0, 0], 2: [0, 1.0, 0, 0]})
        box_id, sim, margin = coopapp._predict_box_id(None, head, [1, 2])
        assert box_id == 1
        assert sim == pytest.approx(1.0)
        assert margin == pytest.approx(1.0)

    def test_ambiguous_match_has_low_margin(self, monkeypatch):
        import numpy as np

        halfway = np.array([1.0, 1.0, 0, 0], "float32")
        monkeypatch.setattr(coopapp, "_embed_box_photo", lambda img: halfway / np.linalg.norm(halfway))
        head = _make_head({1: [1.0, 0, 0, 0], 2: [0, 1.0, 0, 0]})
        box_id, sim, margin = coopapp._predict_box_id(None, head, [1, 2])
        assert margin == pytest.approx(0.0, abs=1e-6)

    def test_rejects_non_dict_head(self):
        assert coopapp._predict_box_id(None, "not-a-dict", None) == (None, 0.0, 0.0)
        assert coopapp._predict_box_id(None, None, None) == (None, 0.0, 0.0)

    def test_rejects_wrong_format_version(self, monkeypatch):
        head = _make_head({1: [1.0, 0, 0, 0], 2: [0, 1.0, 0, 0]})
        head["format"] = 1
        assert coopapp._predict_box_id(None, head, [1, 2]) == (None, 0.0, 0.0)

    def test_rejects_dim_mismatch(self, monkeypatch):
        import numpy as np

        monkeypatch.setattr(coopapp, "_embed_box_photo", lambda img: np.zeros(7, "float32"))
        head = _make_head({1: [1.0, 0, 0, 0], 2: [0, 1.0, 0, 0]})  # dim 4
        assert coopapp._predict_box_id(None, head, [1, 2]) == (None, 0.0, 0.0)

    def test_rejects_when_embedder_unavailable(self, monkeypatch):
        monkeypatch.setattr(coopapp, "_embed_box_photo", lambda img: None)
        head = _make_head({1: [1.0, 0, 0, 0], 2: [0, 1.0, 0, 0]})
        assert coopapp._predict_box_id(None, head, [1, 2]) == (None, 0.0, 0.0)


# --- Full training pipeline ---


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
            client.post("/api/vision/eggs/sample", json=_sample_payload(photo, box, [_egg_correction(cx=cx)]))

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

    def test_old_wall_shape_samples_still_train(self, client, set_options, nesting_box):
        # Samples stored by 1.31.x carry the flat {left,top,right,bottom}
        # wall shape — the shim must keep them usable for training.
        set_options(egg_vision_enabled=True, egg_vision_training_enabled=True)
        old_walls = {"left": 80, "top": 40, "right": 1080, "bottom": 860}
        photo = _synthetic_box_photo(box=(80, 40, 1080, 860), eggs=[(400, 300, 60, 80, 0)])
        for _ in range(3):
            client.post(
                "/api/vision/eggs/sample",
                json=_sample_payload(photo, nesting_box, [_egg_correction()], walls=old_walls),
            )
        body = client.post("/api/vision/train").get_json()
        assert body["status"] == "trained"
        # the classifier itself won't reach its 15/15 minimums here — the
        # point is the walls shim produced usable examples, not zero
        assert body["classifier_positive_count"] > 0

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

    def test_box_head_trains_below_25_total_and_resolves_photo(self, client, set_options, fake_embedder):
        # Doubles as the regression test for the decoupled train gate:
        # pre-1.32, nothing trained below 25 TOTAL samples, so 2 boxes x 4
        # wizard photos (8 total) left the box head permanently untrained.
        set_options(egg_vision_enabled=True, egg_vision_training_enabled=True)
        box_a = client.post("/api/nesting-boxes", json={"name": "Box A", "width_mm": 320}).get_json()
        box_b = client.post("/api/nesting-boxes", json={"name": "Box B", "width_mm": 320}).get_json()

        per_box = coopapp.EGG_VISION_BOX_ID_MIN_SAMPLES_PER_BOX + 1
        for _ in range(per_box):
            photo_a = _synthetic_box_photo(box=(80, 40, 1080, 860), eggs=[])
            client.post("/api/vision/eggs/sample", json=_sample_payload(photo_a, box_a, []))

        import cv2
        import numpy as np

        for _ in range(per_box):
            img = np.full((900, 1200, 3), 40, np.uint8)  # dark scene — far from box_a's in embedding space
            cv2.rectangle(img, (80, 40), (1080, 860), (20, 20, 20), -1)
            ok, buf = cv2.imencode(".jpg", img)
            photo_b = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()
            client.post("/api/vision/eggs/sample", json=_sample_payload(photo_b, box_b, []))

        train_body = client.post("/api/vision/train").get_json()
        assert train_body["status"] == "trained"
        assert train_body["box_classifier_trained"] is True

        # A photo matching box_a's appearance, submitted with no box_id,
        # now auto-resolves instead of requiring confirm_box.
        photo = _synthetic_box_photo(box=(80, 40, 1080, 860), eggs=[])
        body = client.post("/api/vision/eggs", json={"photo": photo}).get_json()
        assert body["status"] != "confirm_box"
        assert body["box"]["id"] == box_a["id"]

    def test_stale_pre_1_32_pickled_head_degrades_to_confirm_box(self, client, set_options, conn):
        # 1.31.x stored a pickled sklearn LogisticRegression trained on
        # 48-dim histograms — it must degrade to confirm_box, never 500,
        # and retraining must recover.
        set_options(egg_vision_enabled=True, egg_vision_training_enabled=True)
        client.post("/api/nesting-boxes", json={"name": "Box A", "width_mm": 320})
        client.post("/api/nesting-boxes", json={"name": "Box B", "width_mm": 320})

        from sklearn.linear_model import LogisticRegression

        stale = LogisticRegression(max_iter=100).fit([[0.0] * 48, [1.0] * 48], [1, 2])
        conn.execute(
            "INSERT INTO egg_vision_models (trained_at, trained_on_sample_count, box_classifier_blob, "
            "box_classifier_labels) VALUES ('2024-01-01', 6, ?, '[1, 2]')",
            (pickle.dumps(stale),),
        )
        conn.commit()

        photo = _synthetic_box_photo(box=(80, 40, 1080, 860), eggs=[])
        res = client.post("/api/vision/eggs", json={"photo": photo})
        assert res.status_code == 200
        assert res.get_json()["status"] == "confirm_box"

    def test_embedder_unavailable_skips_box_head_but_trains_rest(self, client, set_options, monkeypatch):
        set_options(egg_vision_enabled=True, egg_vision_training_enabled=True)
        monkeypatch.setattr(coopapp, "_embed_box_photo", lambda img: None)
        box_a = client.post("/api/nesting-boxes", json={"name": "Box A", "width_mm": 320}).get_json()
        box_b = client.post("/api/nesting-boxes", json={"name": "Box B", "width_mm": 320}).get_json()
        photo = _synthetic_box_photo(box=(80, 40, 1080, 860), eggs=[(400, 300, 60, 80, 0)])
        for box in (box_a, box_b):
            for _ in range(coopapp.EGG_VISION_BOX_ID_MIN_SAMPLES_PER_BOX + 1):
                client.post("/api/vision/eggs/sample", json=_sample_payload(photo, box, [_egg_correction()]))

        body = client.post("/api/vision/train").get_json()
        assert body["status"] == "trained"
        assert body["box_classifier_trained"] is False
        assert body["classifier_positive_count"] > 0  # egg pipeline unaffected

        analyze = client.post("/api/vision/eggs", json={"photo": photo}).get_json()
        assert analyze["status"] == "confirm_box"


# --- Real bundled embedder (integration — the model file ships in the repo) ---


@pytest.mark.skipif(
    not coopapp.OPENCV_AVAILABLE or not os.path.exists(coopapp.EGG_VISION_BOX_EMBED_MODEL_PATH),
    reason="opencv or bundled ONNX model not available",
)
class TestRealEmbedder:
    def test_bundled_model_integrity(self):
        with open(coopapp.EGG_VISION_BOX_EMBED_MODEL_PATH, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        assert digest == BUNDLED_EMBED_MODEL_SHA256

    def test_embedder_loads_and_separates_textured_boxes(self):
        import cv2
        import numpy as np

        status = coopapp._box_embedder_status()
        assert status["available"], status["error"]
        assert status["dim"] >= 256

        def textured_box(seed, base, box_color):
            rng = np.random.default_rng(seed)
            img = np.full((900, 1200, 3), base, np.uint8)
            cv2.rectangle(img, (80, 40), (1080, 860), box_color, -1)
            for _ in range(400):  # wood-grain-ish streaks so the CNN has real texture to fingerprint
                x, y = int(rng.integers(90, 1070)), int(rng.integers(50, 850))
                shade = int(rng.integers(-30, 30))
                color = tuple(int(np.clip(c + shade, 0, 255)) for c in box_color)
                cv2.line(img, (x, y), (x + int(rng.integers(20, 80)), y + int(rng.integers(-5, 5))), color, 2)
            return img

        img_a1 = textured_box(1, 230, (120, 160, 190))  # warm light wood
        img_a2 = textured_box(2, 225, (120, 160, 190))  # same box, different photo
        img_b = textured_box(3, 60, (40, 60, 80))  # dark wood

        emb_a1 = coopapp._embed_box_photo(img_a1)
        emb_a2 = coopapp._embed_box_photo(img_a2)
        emb_b = coopapp._embed_box_photo(img_b)
        assert emb_a1 is not None
        same = float(np.dot(emb_a1, emb_a2))
        cross = float(np.dot(emb_a1, emb_b))
        assert same > cross
