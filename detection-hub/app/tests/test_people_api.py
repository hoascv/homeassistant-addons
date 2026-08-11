"""Enrolling people, and the routes that let somebody manage them.

These are the first write routes this add-on has ever had, which is why the
access gate gets its own test here rather than being assumed from the read
endpoints it has always covered.
"""
import io
import os

import cv2
import numpy as np
import pytest

import store

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
STREET = os.path.join(FIXTURES, "street.jpg")


ENROL_FLOOR = 40


@pytest.fixture
def close_face(db_path, set_options):
    """A frame carrying a face big enough to enrol, with the floor set to match.

    Built by upscaling the one face-like region of the street frame — which
    yields 48 px — rather than committing a photograph of a real person. An
    enrolled face is a biometric template and this repository is public, so no
    such image exists here; see fixtures/README.md. The floor is a configurable
    policy, so a test can lower it and still exercise the real path.
    """
    set_options(face_min_pixels=ENROL_FLOOR)
    image = cv2.imread(STREET)
    return cv2.resize(image[120:240, 450:580], None, fx=8, fy=8,
                      interpolation=cv2.INTER_CUBIC)


def _jpeg(image):
    return cv2.imencode(".jpg", image)[1].tobytes()


# --- managing people ----------------------------------------------------------


def test_a_person_can_be_added_listed_renamed_and_removed(client, db_path):
    created = client.post("/api/people", json={"name": "Alice"})
    assert created.status_code == 201
    person_id = created.get_json()["id"]

    assert [p["name"] for p in client.get("/api/people").get_json()["people"]] == ["Alice"]

    renamed = client.put(f"/api/people/{person_id}", json={"name": "Alice B"})
    assert renamed.status_code == 200

    removed = client.delete(f"/api/people/{person_id}")
    assert removed.status_code == 200
    assert removed.get_json()["status"] == "deleted"
    assert client.get("/api/people").get_json()["people"] == []


def test_a_nameless_person_is_refused(client, db_path):
    assert client.post("/api/people", json={}).status_code == 400
    assert client.post("/api/people", json={"name": "   "}).status_code == 400


def test_the_same_name_twice_is_a_conflict_not_a_second_person(client, db_path):
    client.post("/api/people", json={"name": "Alice"})
    again = client.post("/api/people", json={"name": "Alice"})
    assert again.status_code == 409
    assert "already enrolled" in again.get_json()["error"]


def test_renaming_or_deleting_somebody_who_is_not_there_is_a_404(client, db_path):
    assert client.put("/api/people/999", json={"name": "Ghost"}).status_code == 404
    assert client.delete("/api/people/999").status_code == 404


# --- enrolling a face ---------------------------------------------------------


def test_a_face_can_be_enrolled_from_an_upload(client, db_path, close_face):
    person_id = client.post("/api/people", json={"name": "Alice"}).get_json()["id"]
    enrolled = client.post(f"/api/people/{person_id}/prints", data=_jpeg(close_face))

    assert enrolled.status_code == 201, enrolled.get_json()
    prints = client.get(f"/api/people/{person_id}/prints").get_json()["prints"]
    assert len(prints) == 1
    assert prints[0]["source"] == "upload"
    assert prints[0]["face_w"] >= ENROL_FLOOR
    # The vector is not something to hand to a browser.
    assert "embedding" not in prints[0]


def test_the_person_list_reports_what_was_enrolled(client, db_path, close_face):
    """`min_face_w` is the number that predicts whether matching will work: a
    person enrolled only from close-ups will not be found at a distance."""
    person_id = client.post("/api/people", json={"name": "Alice"}).get_json()["id"]
    client.post(f"/api/people/{person_id}/prints", data=_jpeg(close_face))

    person = client.get("/api/people").get_json()["people"][0]
    assert person["prints"] == 1
    assert person["min_face_w"] >= ENROL_FLOOR


def test_a_face_can_be_enrolled_from_a_snapshot_the_add_on_took(client, db_path, close_face):
    """The source the page uses, and the better one: the real camera, lens and
    compression that matching will have to work against."""
    conn = store.connect(db_path, actor="user")
    try:
        snapshot_id = store.save_snapshot(conn, _jpeg(close_face),
                                          close_face.shape[1], close_face.shape[0])
        conn.commit()
    finally:
        conn.close()

    person_id = client.post("/api/people", json={"name": "Alice"}).get_json()["id"]
    enrolled = client.post(f"/api/people/{person_id}/prints",
                           json={"snapshot_id": snapshot_id})

    assert enrolled.status_code == 201, enrolled.get_json()
    prints = client.get(f"/api/people/{person_id}/prints").get_json()["prints"]
    assert prints[0]["source"] == "snapshot"
    assert prints[0]["source_snapshot_id"] == snapshot_id


def test_the_crop_a_print_was_made_from_can_be_looked_at(client, db_path, close_face):
    """A name is only auditable if the face that earns it can be seen."""
    person_id = client.post("/api/people", json={"name": "Alice"}).get_json()["id"]
    print_id = client.post(f"/api/people/{person_id}/prints",
                           data=_jpeg(close_face)).get_json()["id"]

    crop = client.get(f"/api/faces/{print_id}")
    assert crop.status_code == 200
    assert crop.mimetype == "image/jpeg"
    assert crop.get_data()[:2] == b"\xff\xd8"


def test_a_frame_with_no_face_is_refused_with_a_reason(client, db_path, street_jpeg):
    """The distant street frame: people, no faces. Somebody wondering why
    nothing happens must be told which problem they have."""
    person_id = client.post("/api/people", json={"name": "Alice"}).get_json()["id"]
    refused = client.post(f"/api/people/{person_id}/prints", data=street_jpeg)

    assert refused.status_code == 400
    error = refused.get_json()["error"]
    assert "no face" in error or "px" in error


def test_a_face_below_the_floor_is_refused_with_the_measurement(client, db_path,
                                                                close_face, set_options):
    """The most useful error message in the feature: it names the number, so the
    reader can decide whether to move the camera or move the floor."""
    set_options(face_min_pixels=400)
    person_id = client.post("/api/people", json={"name": "Alice"}).get_json()["id"]
    refused = client.post(f"/api/people/{person_id}/prints", data=_jpeg(close_face))

    assert refused.status_code == 400
    assert "400" in refused.get_json()["error"], refused.get_json()


def test_enrolling_for_somebody_who_does_not_exist_is_a_404(client, db_path, close_face):
    assert client.post("/api/people/999/prints", data=_jpeg(close_face)).status_code == 404


def test_a_print_can_be_removed_on_its_own(client, db_path, close_face):
    """One bad enrolment should not cost the person their other prints."""
    person_id = client.post("/api/people", json={"name": "Alice"}).get_json()["id"]
    first = client.post(f"/api/people/{person_id}/prints", data=_jpeg(close_face))
    client.post(f"/api/people/{person_id}/prints", data=_jpeg(close_face))

    removed = client.delete(f"/api/people/{person_id}/prints/{first.get_json()['id']}")
    assert removed.status_code == 200
    assert len(client.get(f"/api/people/{person_id}/prints").get_json()["prints"]) == 1


def test_deleting_a_person_takes_their_biometrics_with_them(client, db_path, close_face):
    person_id = client.post("/api/people", json={"name": "Alice"}).get_json()["id"]
    print_id = client.post(f"/api/people/{person_id}/prints",
                           data=_jpeg(close_face)).get_json()["id"]

    removed = client.delete(f"/api/people/{person_id}")
    assert removed.get_json()["prints_deleted"] == 1
    assert client.get(f"/api/faces/{print_id}").status_code == 404


# --- the gate -----------------------------------------------------------------


def test_an_unauthenticated_caller_cannot_create_a_person(direct_client, db_path):
    """These are the first write routes here. The published port has no
    credential unless api_token is set, and enrolling a face is not something to
    leave open."""
    assert direct_client.post("/api/people", json={"name": "Mallory"}).status_code == 401
    assert direct_client.get("/api/people").status_code == 401
    assert direct_client.delete("/api/people/1").status_code == 401
