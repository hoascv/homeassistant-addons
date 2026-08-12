"""Faces: the matching maths, the size floor, and the real models.

Every expected value in the maths tests is written by hand — vectors chosen so
the cosine between them is something a reader can check without running the code.
The model tests use the bundled files and the existing street frame; no
photograph of anybody's face is committed to this repository, and none needs to
be. See fixtures/README.md for why.
"""
import os

import cv2
import numpy as np
import pytest

import faces

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
STREET = os.path.join(FIXTURES, "street.jpg")


# --- cosine -------------------------------------------------------------------


def test_cosine_of_a_vector_with_itself_is_one():
    v = np.array([3.0, 4.0] + [0.0] * 126, dtype=np.float32)
    assert faces.cosine(v, v) == pytest.approx(1.0)


def test_cosine_ignores_magnitude():
    """SFace embeddings are not unit length — a real one measured 6.03 — so a
    similarity that moved with magnitude would rank by how brightly lit a face
    was rather than by who it belongs to."""
    a = np.array([1.0, 1.0], dtype=np.float32)
    assert faces.cosine(a, a * 100) == pytest.approx(1.0)


def test_perpendicular_vectors_score_zero_and_opposite_ones_score_minus_one():
    a = np.array([1.0, 0.0])
    assert faces.cosine(a, np.array([0.0, 1.0])) == pytest.approx(0.0)
    assert faces.cosine(a, np.array([-1.0, 0.0])) == pytest.approx(-1.0)


def test_a_zero_vector_scores_zero_rather_than_dividing_by_zero():
    assert faces.cosine(np.zeros(4), np.array([1.0, 2.0, 3.0, 4.0])) == 0.0


def test_mismatched_lengths_score_zero():
    """Two different recognisers produce different dimensions. Comparing them
    must not raise in a camera thread, and must not return a number."""
    assert faces.cosine(np.ones(128), np.ones(64)) == 0.0


# --- packing ------------------------------------------------------------------


def test_an_embedding_survives_the_round_trip_to_a_blob():
    original = np.linspace(-1, 1, faces.EMBEDDING_DIMS).astype(np.float32)
    restored = faces.unpack_embedding(faces.pack_embedding(original))
    assert restored is not None
    assert np.allclose(original, restored)


def test_a_truncated_blob_is_no_embedding_rather_than_a_short_one():
    """A short vector would still compare against everything, at some arbitrary
    angle. Better to have no print than a print that scores nonsense."""
    assert faces.unpack_embedding(b"\x00\x01\x02\x03") is None
    assert faces.unpack_embedding(b"") is None
    assert faces.unpack_embedding(None) is None


# --- matching -----------------------------------------------------------------


def _vec(*values):
    """A 128-D vector from its first few components; the rest are zero."""
    v = np.zeros(faces.EMBEDDING_DIMS, dtype=np.float32)
    v[: len(values)] = values
    return v


def test_the_closest_person_wins_when_it_clears_both_bars():
    probe = _vec(1.0, 0.0)
    match = faces.best_match(
        probe, [(1, _vec(0.99, 0.14)), (2, _vec(0.0, 1.0))], threshold=0.45, margin=0.05
    )
    assert match["person_id"] == 1
    assert match["score"] > 0.98


def test_a_score_below_the_threshold_names_nobody():
    """A stranger scores *something* against everyone. Naming the nearest is how
    a doorbell tells you your neighbour is your daughter."""
    probe = _vec(1.0, 0.0)
    match = faces.best_match(probe, [(1, _vec(1.0, 1.0))], threshold=0.9, margin=0.0)
    assert match["person_id"] is None
    assert match["score"] == pytest.approx(0.7071, abs=1e-3), "the score is still reported"


def test_two_people_within_the_margin_name_nobody():
    """0.47 against 0.46 is a coin flip, and a coin flip that prints a name is
    worse than silence."""
    probe = _vec(1.0, 0.0)
    close = faces.best_match(
        probe, [(1, _vec(1.0, 0.35)), (2, _vec(1.0, 0.36))], threshold=0.2, margin=0.05
    )
    assert close["person_id"] is None
    assert close["runner_up"] is not None


def test_a_person_is_scored_by_their_best_print_not_their_average():
    """Prints exist to cover variation — hat, angle, night. Averaging them is
    exactly the wrong thing to do with the variation they were enrolled for: the
    good print here must carry the match on its own."""
    probe = _vec(1.0, 0.0)
    prints = [(1, _vec(1.0, 0.02)), (1, _vec(0.0, 1.0)), (1, _vec(-1.0, 0.0))]
    match = faces.best_match(probe, prints, threshold=0.9, margin=0.0)
    assert match["person_id"] == 1
    assert match["score"] > 0.99


def test_an_empty_gallery_is_no_match_and_no_score():
    match = faces.best_match(_vec(1.0), [], threshold=0.45, margin=0.05)
    assert match == {"person_id": None, "score": None, "runner_up": None}


def test_prints_that_cannot_be_unpacked_are_skipped_not_scored():
    """A print from another recogniser, or a truncated row: ignored entirely
    rather than compared at whatever angle its bytes happen to give."""
    match = faces.best_match(
        _vec(1.0, 0.0), [(1, b"\x00\x01"), (2, faces.pack_embedding(_vec(1.0, 0.01)))],
        threshold=0.45, margin=0.05,
    )
    assert match["person_id"] == 2


# --- cropping -----------------------------------------------------------------


def test_a_crop_carries_its_offset_so_a_face_box_can_go_back_to_the_frame():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    crop, x0, y0 = faces.crop_with_margin(frame, [100, 200, 50, 100], margin=0.2)
    assert (x0, y0) == (80, 180)
    assert crop.shape[:2] == (100 + 40, 50 + 40)


def test_a_crop_at_the_edge_is_clipped_rather_than_wrapped():
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    crop, x0, y0 = faces.crop_with_margin(frame, [0, 0, 20, 20], margin=0.5)
    assert (x0, y0) == (0, 0)
    assert crop.shape[0] <= 100 and crop.shape[1] <= 100


def test_a_box_entirely_outside_the_frame_is_no_crop():
    frame = np.zeros((50, 50, 3), dtype=np.uint8)
    crop, _, _ = faces.crop_with_margin(frame, [200, 200, 10, 10], margin=0.0)
    assert crop is None


# --- the real models ----------------------------------------------------------


def test_both_face_models_are_bundled():
    """The whole feature is the files being there; a missing one would surface
    only as a face pass that silently never fires on somebody's host."""
    for path in (faces.DETECTOR_MODEL, faces.RECOGNISER_MODEL):
        assert os.path.exists(path), f"not bundled at {path}"


def test_the_identifier_loads_and_reports_what_it_is():
    status = faces.FaceIdentifier().status()
    assert status["available"] is True
    assert status["error"] is None
    assert status["recogniser"] == "sface_2021dec"
    assert status["dims"] == faces.EMBEDDING_DIMS


def test_a_missing_model_is_reported_not_raised():
    """Face identification is optional. A broken optional feature must leave an
    add-on that still detects objects, and must say why it is not working."""
    identifier = faces.FaceIdentifier(recogniser_model="/nope/sface.onnx")
    assert identifier.available() is False
    assert "not found" in identifier.error
    assert identifier.status()["available"] is False


def test_the_same_face_embeds_to_the_same_vector():
    """Pins that embeddings are real and comparable, without needing a
    photograph of anybody: identical input, identical direction."""
    identifier = faces.FaceIdentifier()
    image = cv2.imread(STREET)
    found = identifier.detect_faces(image)
    assert found, "the street frame has always had at least one face-like region"

    _, first = identifier.embed(image, found[0])
    _, second = identifier.embed(image, found[0])
    assert first.shape == (faces.EMBEDDING_DIMS,)
    assert faces.cosine(first, second) == pytest.approx(1.0, abs=1e-5)


def test_an_embedding_is_not_unit_length():
    """Recorded because `cosine` depends on it: SFace does not normalise its
    output, so anything comparing raw dot products would rank by brightness."""
    identifier = faces.FaceIdentifier()
    image = cv2.imread(STREET)
    _, embedding = identifier.embed(image, identifier.detect_faces(image)[0])
    assert not np.isclose(np.linalg.norm(embedding), 1.0)


def test_a_distant_street_frame_offers_no_face_worth_identifying():
    """The honest core of this feature. These people are 74-87 px tall and their
    faces are nothing — the same situation as a driveway camera. The answer must
    be a refusal with the measurement in it, not an embedding computed from a
    handful of pixels."""
    import detector

    identifier = faces.FaceIdentifier()
    image = cv2.imread(STREET)
    people = [d for d in detector.Detector().detect(image, confidence=0.6)[0]
              if d["label"] == "person"]
    assert people, "the fixture has people in it"

    for person in people:
        found = identifier.probe_person(image, person["box"])
        assert found["usable"] is False
        assert found["reason"], "a refusal has to say why"
        assert identifier.find_in_person(image, person["box"]) is None


def test_the_probe_answers_in_words_a_person_can_act_on(client, db_path, street_jpeg):
    """Whether this works is a property of the camera, and an empty result is
    not something anyone can debug. The verdict has to name the shortfall."""
    body = client.post("/api/faces/probe", data=street_jpeg).get_json()

    assert body["usable"] == 0
    assert body["people"], "the fixture has people in it"
    assert body["min_pixels"] == 60
    # Two honest refusals, and which one fires is itself information: a face too
    # small to use is a camera that might work closer up, no face at all is one
    # that is not showing faces to begin with. This frame is the second.
    assert "no faces at all" in body["verdict"]
    assert all(person["face_found"] is False for person in body["people"])
    for person in body["people"]:
        assert person["person_height"] > 0
        assert person["usable"] is False


def test_the_probe_stores_nothing(client, db_path, street_jpeg):
    """It is an instrument, not an input source — the same rule /api/detect
    follows without `?camera=`."""
    import store

    client.post("/api/faces/probe", data=street_jpeg)
    conn = store.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM detections").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 0
    finally:
        conn.close()


def test_the_probe_refuses_something_that_is_not_an_image(client, db_path):
    assert client.post("/api/faces/probe", data=b"not a jpeg").status_code == 400


def test_the_probe_is_behind_the_access_gate(direct_client, db_path, street_jpeg):
    """It runs two models per person. An unauthenticated caller on the published
    port must not be able to spend that."""
    assert direct_client.post("/api/faces/probe", data=street_jpeg).status_code == 401


# --- the options ---------------------------------------------------------------


def test_identification_is_off_until_it_is_asked_for(set_options):
    """Processing biometric data must be a deliberate act, not something an
    upgrade switches on in somebody's house."""
    import app as hub

    set_options()
    assert hub.get_face_options()["enabled"] is False


def test_the_face_settings_come_from_the_options_file(set_options):
    import app as hub

    set_options(identify_people=True, face_min_pixels=40,
                face_match_threshold=0.55, face_margin=0.1, face_attempts=12)
    options = hub.get_face_options()
    assert options == {"enabled": True, "min_pixels": 40, "threshold": 0.55,
                       "margin": 0.1, "attempts": 12}


def test_an_out_of_range_setting_is_clamped_not_obeyed(set_options):
    """These come from a text field. A threshold of 5 would match nobody ever,
    and silently doing that is worse than quietly using the nearest legal value."""
    import app as hub

    set_options(face_match_threshold=5, face_min_pixels=0, face_attempts=9999)
    options = hub.get_face_options()
    assert options["threshold"] == 0.90
    assert options["min_pixels"] == 24
    assert options["attempts"] == 60


def test_the_probe_honours_the_configured_floor(client, db_path, street_jpeg, set_options):
    """The floor is what someone lowers to find out what their camera could do,
    so it has to reach the endpoint rather than staying a constant."""
    set_options(face_min_pixels=200)
    assert client.post("/api/faces/probe", data=street_jpeg).get_json()["min_pixels"] == 200


def test_the_page_offers_the_camera_check(client, db_path):
    """It is the first thing anyone should run and the only honest way to find
    out whether this works on their camera — so it is a button, not a curl."""
    html = client.get("/").get_data(as_text=True)
    assert "Can it identify people?" in html
    assert "api/faces/probe" in html
    assert 'fetch("/api/faces/probe"' not in html, "absolute URL would break ingress"


def test_the_size_floor_is_what_refuses_a_small_face():
    """Lowering the floor far enough must change the verdict — otherwise the
    refusals above prove nothing about the floor, only that the frame is hard."""
    identifier = faces.FaceIdentifier()
    image = cv2.imread(STREET)
    faces_found = identifier.detect_faces(image)
    width = int(faces_found[0][2])

    whole_frame = [0, 0, image.shape[1], image.shape[0]]
    assert identifier.probe_person(image, whole_frame, min_pixels=width + 10)["usable"] is False
    assert identifier.probe_person(image, whole_frame, min_pixels=1)["usable"] is True


# --- reading a score off the page ---------------------------------------------


def test_the_page_carries_the_threshold_a_score_is_judged_against(client, db_path,
                                                                  set_options):
    """An unrecognised face is only actionable next to the bar it missed. Without
    the threshold on the page, tuning it means going to the API for the number."""
    set_options(identify_people=True, face_match_threshold=0.52)
    html = client.get("/").get_data(as_text=True)

    assert "const FACE_THRESHOLD = 0.52" in html
    assert "needs ${FACE_THRESHOLD}" in html
    assert "unrecognised" in html


def test_the_status_card_says_whether_identification_is_on(client, db_path, set_options):
    """Otherwise it is invisible until somebody is recognised, which is a long
    time to wonder whether the restart took."""
    set_options()
    assert "faces off" in client.get("/").get_data(as_text=True)

    set_options(identify_people=True, face_match_threshold=0.5, face_min_pixels=55)
    html = client.get("/").get_data(as_text=True)
    assert "faces ready" in html
    assert "match 0.5" in html and "from 55 px" in html
