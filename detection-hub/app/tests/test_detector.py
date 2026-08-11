"""The detection maths, which is where this goes quietly wrong.

Every expected value here is derived by hand in the test, not recorded from a
run. A test that asserts whatever the code produced would pass just as happily
with the grid decode inverted or the letterbox padding black — both of which
produce confident detections in the wrong place, or none at all.
"""
import os

import cv2
import numpy as np
import pytest

import detector

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
STREET = os.path.join(FIXTURES, "street.jpg")
STREET_LATER = os.path.join(FIXTURES, "street_later.jpg")


# --- letterbox ----------------------------------------------------------------


def test_letterbox_preserves_aspect_and_reports_its_ratio():
    """A squashed frame detects badly and the boxes map back wrong, so the
    aspect is preserved and the scale factor is returned rather than assumed."""
    image = np.zeros((576, 768, 3), dtype=np.uint8)
    canvas, ratio = detector.letterbox(image, size=416)

    assert canvas.shape == (416, 416, 3)
    # 416/768 is the binding constraint (the wider side), so 0.5417.
    assert ratio == pytest.approx(416 / 768, abs=1e-6)
    # 576 * 416/768 = 312 rows of real image, the rest padding.
    assert canvas[311, 0].tolist() == [0, 0, 0], "row 311 should still be image"
    assert canvas[312, 0].tolist() == [114] * 3, "row 312 should be padding"


def test_letterbox_pads_with_114_not_black():
    """YOLOX was trained with 114 padding. Black is real image content to the
    model and drags detections near the border."""
    image = np.full((100, 400, 3), 255, dtype=np.uint8)
    canvas, _ = detector.letterbox(image, size=416)
    assert canvas[-1, -1].tolist() == [114, 114, 114]


def test_letterbox_puts_the_image_at_the_top_left():
    """Top-left, not centred — the whole reason mapping a box back is a single
    divide with no offset term."""
    image = np.full((100, 400, 3), 255, dtype=np.uint8)
    canvas, _ = detector.letterbox(image, size=416)
    assert canvas[0, 0].tolist() == [255, 255, 255]


# --- decode -------------------------------------------------------------------


def test_decode_places_a_box_where_the_grid_says():
    """The arithmetic that matters, worked by hand.

    Anchor 0 is the first cell of the stride-8 level, so grid (0,0). With raw
    xy = 0.5 the centre is (0.5 + 0) * 8 = 4. With raw wh = 0 the size is
    exp(0) * 8 = 8. Any sign flip or missing stride multiply moves this.
    """
    raw = np.zeros((1, 3549, 85), dtype=np.float32)
    raw[0, 0, 0:2] = 0.5
    raw[0, 0, 2:4] = 0.0

    out = detector.decode(raw, size=416)

    assert out[0, 0] == pytest.approx(4.0)
    assert out[0, 1] == pytest.approx(4.0)
    assert out[0, 2] == pytest.approx(8.0)
    assert out[0, 3] == pytest.approx(8.0)


def test_decode_uses_the_right_stride_per_level():
    """52*52 = 2704 anchors of stride 8, then 26*26 = 676 of stride 16, then
    13*13 = 169 of stride 32. Anchor 2704 is the first stride-16 cell."""
    raw = np.zeros((1, 3549, 85), dtype=np.float32)
    raw[0, 2704, 2:4] = 0.0  # exp(0) * 16
    out = detector.decode(raw, size=416)
    assert out[2704, 2] == pytest.approx(16.0)

    raw2 = np.zeros((1, 3549, 85), dtype=np.float32)
    out2 = detector.decode(raw2, size=416)
    assert out2[2704 + 676, 2] == pytest.approx(32.0), "first stride-32 anchor"


def test_decode_is_log_space_for_size():
    """wh is exponentiated. exp(1) * 8 = 21.75, not 8 and not 1."""
    raw = np.zeros((1, 3549, 85), dtype=np.float32)
    raw[0, 0, 2] = 1.0
    out = detector.decode(raw, size=416)
    assert out[0, 2] == pytest.approx(np.exp(1.0) * 8, rel=1e-5)


def test_decode_refuses_a_model_that_disagrees_about_anchor_count():
    """Swapping in a model exported at another input size produces boxes that
    are silently wrong. The row count is the cheapest possible check."""
    raw = np.zeros((1, 100, 85), dtype=np.float32)
    with pytest.raises(ValueError, match="anchors"):
        detector.decode(raw, size=416)


# --- the motion gate ----------------------------------------------------------


def test_identical_frames_score_zero():
    """The entire CPU argument rests on this: an unchanging scene must never
    reach the detector."""
    frame = cv2.imread(STREET)
    assert detector.motion_score(frame, frame) == 0.0


def test_real_movement_scores_well_above_zero():
    """Four seconds of a street, same camera. Measured at ~0.054, so a gate
    anywhere in the sensible range separates it from the 0.0 above."""
    before, after = cv2.imread(STREET), cv2.imread(STREET_LATER)
    score = detector.motion_score(before, after)
    assert score > 0.01, f"real movement scored only {score}"


def test_no_baseline_lets_the_first_frame_through():
    """Otherwise a camera that starts on a still scene never detects anything
    until something moves — including the thing already standing there."""
    assert detector.motion_score(None, cv2.imread(STREET)) == 1.0


def test_noise_does_not_read_as_motion():
    """Sensor noise and JPEG artefacts are why the gate blurs before comparing.
    Without it a still night-time camera detects constantly."""
    frame = cv2.imread(STREET)
    rng = np.random.default_rng(0)
    noisy = np.clip(
        frame.astype(np.int16) + rng.integers(-6, 7, frame.shape), 0, 255
    ).astype(np.uint8)
    assert detector.motion_score(frame, noisy) < 0.005


# --- end to end, against the real model ---------------------------------------


def test_it_finds_the_people_and_vehicles_in_a_real_frame():
    """The preprocessing test. Normalise when the model does not expect it, or
    pad with black, and scores collapse — this fails while every unit test
    above still passes."""
    detections, error = detector.Detector().detect(cv2.imread(STREET), confidence=0.6)

    assert error is None
    found = [d["label"] for d in detections]
    assert found.count("person") >= 3, f"expected several pedestrians, got {found}"
    assert {"car", "truck"} & set(found), f"expected a vehicle, got {found}"


def test_boxes_land_inside_the_original_frame():
    """A box outside the image means the letterbox ratio was not undone."""
    image = cv2.imread(STREET)
    height, width = image.shape[:2]
    detections, _ = detector.Detector().detect(image, confidence=0.6)

    for det in detections:
        x, y, w, h = det["box"]
        assert 0 <= x < width and 0 <= y < height, det
        assert w > 0 and h > 0 and x + w <= width + 2 and y + h <= height + 2, det


def test_results_are_ordered_most_confident_first():
    detections, _ = detector.Detector().detect(cv2.imread(STREET), confidence=0.6)
    scores = [d["confidence"] for d in detections]
    assert scores == sorted(scores, reverse=True)


def test_a_label_filter_excludes_everything_else():
    """A driveway camera that only wants people should not be told about the
    parked car every time the light changes."""
    detections, _ = detector.Detector().detect(
        cv2.imread(STREET), confidence=0.6, labels=["person"]
    )
    assert detections, "the filter removed everything"
    assert {d["label"] for d in detections} == {"person"}


def test_a_blank_frame_detects_nothing():
    """Guards against a decode bug that manufactures boxes from an empty grid."""
    blank = np.full((480, 640, 3), 128, dtype=np.uint8)
    detections, error = detector.Detector().detect(blank, confidence=0.6)
    assert error is None
    assert detections == []


def test_raising_the_threshold_only_removes_detections():
    """Confidence is a filter, not a knob that changes what was seen."""
    image = cv2.imread(STREET)
    det = detector.Detector()
    loose, _ = det.detect(image, confidence=0.5)
    strict, _ = det.detect(image, confidence=0.85)
    assert len(strict) <= len(loose)
    assert all(d["confidence"] >= 0.85 for d in strict)


# --- failure reporting --------------------------------------------------------


def test_a_missing_model_is_reported_not_raised(tmp_path):
    """This runs on a background thread for cameras; an exception there is
    stderr noise and a silently dead capture loop."""
    det = detector.Detector(model_path=str(tmp_path / "absent.onnx"))
    detections, error = det.detect(np.zeros((100, 100, 3), dtype=np.uint8))
    assert detections == []
    assert "model not found" in error
    assert det.available() is False


def test_status_reports_enough_to_diagnose_a_bad_model(tmp_path):
    det = detector.Detector(model_path=str(tmp_path / "absent.onnx"))
    det.load()
    status = det.status()
    assert status["model_loaded"] is False
    assert "absent.onnx" in status["model_path"]
    assert status["error"]


def test_garbage_bytes_are_a_refusal_not_a_traceback():
    image, error = detector.decode_image(b"this is not a jpeg")
    assert image is None and "not a readable image" in error


def test_empty_body_is_reported():
    image, error = detector.decode_image(b"")
    assert image is None and "empty image" in error


def test_a_real_jpeg_round_trips():
    with open(STREET, "rb") as handle:
        image, error = detector.decode_image(handle.read())
    assert error is None and image.shape == (576, 768, 3)

    encoded = detector.encode_jpeg(image)
    assert encoded[:2] == b"\xff\xd8", "should be a JPEG"
    again, error = detector.decode_image(encoded)
    assert error is None and again.shape == image.shape


# --- choosing a model ---------------------------------------------------------


def test_both_bundled_models_are_present():
    """The whole feature is the file being there; a missing one would surface
    only as a broken detector on someone's host."""
    for name, path in detector.MODELS.items():
        assert os.path.exists(path), f"{name} is not bundled at {path}"


def test_model_for_resolves_the_bundled_names():
    assert detector.model_for("nano").endswith("yolox_nano.onnx")
    assert detector.model_for("tiny").endswith("yolox_tiny.onnx")


def test_model_for_is_forgiving_about_case_and_space():
    assert detector.model_for("  TINY ") == detector.MODELS["tiny"]


def test_an_unknown_model_name_falls_back_to_the_default():
    """This comes from a config option; a typo should leave a working detector,
    not an add-on that detects nothing."""
    assert detector.model_for("yolov99") == detector.DEFAULT_MODEL
    assert detector.model_for(None) == detector.DEFAULT_MODEL
    assert detector.model_for("") == detector.DEFAULT_MODEL


def test_the_running_model_is_named_in_status():
    assert detector.Detector().status()["model"] == "nano"
    assert detector.Detector(model_path=detector.MODELS["tiny"]).status()["model"] == "tiny"


def test_a_model_outside_the_bundle_reports_as_custom(tmp_path):
    det = detector.Detector(model_path=str(tmp_path / "mine.onnx"))
    assert det.status()["model"] == "custom"


def test_tiny_loads_and_detects_the_same_scene():
    """The bundled file has to actually work — a 20 MB blob that fails to load
    would otherwise be discovered by whoever switched to it."""
    det = detector.Detector(model_path=detector.MODELS["tiny"])
    detections, error = det.detect(cv2.imread(STREET), confidence=0.6)

    assert error is None
    found = [d["label"] for d in detections]
    assert found.count("person") >= 2, f"tiny found {found}"
    assert {"car", "truck"} & set(found), f"tiny found no vehicle: {found}"


def test_both_models_share_the_decode():
    """They are both YOLOX at the same input size, which is why switching needs
    no other change. If a future model breaks that, this fails rather than the
    boxes quietly landing in the wrong places."""
    for path in detector.MODELS.values():
        det = detector.Detector(model_path=path)
        assert det.status()["input_size"] == detector.INPUT_SIZE
        detections, error = det.detect(cv2.imread(STREET), confidence=0.6)
        assert error is None and detections
