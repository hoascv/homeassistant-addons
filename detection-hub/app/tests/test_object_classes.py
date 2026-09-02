"""Teaching it a class, and what it does with one it was never taught.

The design is forced by a measurement recorded in test_objects.py: the detector
cannot box an object outside its 80 classes, so motion proposes the regions and
this names them. What follows is the storage, the review queue and the wiring —
and, throughout, the rule that an unrecognised thing gets no name rather than a
wrong one.
"""
import json

import pytest

import store


@pytest.fixture
def db(db_path):
    """The same database the `client` fixture serves from, so a crop written
    here is one the API can see. A separate connection of its own would be a
    separate file, and every route test would look at an empty queue."""
    conn = store.connect(db_path, actor="user")
    conn.db_path = db_path
    yield conn
    conn.close()


def _class(db, name="cargo bike"):
    class_id = store.create_object_class(db, name)
    db.commit()
    return class_id


def _sample(db, class_id=None, camera="drive", jpeg=b"\xff\xd8jpeg-bytes"):
    return store.save_object_sample(
        db, jpeg, 120, 90, camera=camera, box=[10, 20, 120, 90],
        embedding=b"\x00" * 16, dims=4, model="squeezenet1.1", class_id=class_id)


# --- classes ------------------------------------------------------------------


def test_a_class_can_be_created_and_listed(db):
    _class(db)
    [found] = store.object_classes(db)
    assert found["name"] == "cargo bike"
    assert found["samples"] == 0


def test_creating_the_same_name_twice_returns_the_same_class(db):
    """Otherwise labelling a crop with a name already in use silently forks the
    class, and half the training goes to a twin nobody can see."""
    first = _class(db)
    assert store.create_object_class(db, "cargo bike") == first
    assert len(store.object_classes(db)) == 1


def test_a_name_is_tidied_not_taken_literally(db):
    store.create_object_class(db, "  cargo   bike  ")
    db.commit()
    assert store.object_classes(db)[0]["name"] == "cargo bike"


def test_a_class_needs_a_name(db):
    with pytest.raises(ValueError, match="name"):
        store.create_object_class(db, "   ")


def test_the_counts_show_whether_a_class_is_ready(db):
    """A class with four samples all from one afternoon will fail at dusk, and
    the number is the only way anybody learns that before it happens."""
    class_id = _class(db)
    _sample(db, class_id, camera="drive")
    _sample(db, class_id, camera="drive")
    _sample(db, class_id, camera="garden")
    db.commit()

    [found] = store.object_classes(db)
    assert found["samples"] == 3
    assert found["cameras"] == 2


def test_archiving_keeps_the_class_out_of_the_list_but_not_the_database(db):
    """Detections already carry the label; deleting the row would leave them
    reading as an id."""
    class_id = _class(db)
    store.archive_object_class(db, class_id)
    db.commit()
    assert store.object_classes(db) == []
    assert len(store.object_classes(db, include_archived=True)) == 1


def test_deleting_a_class_takes_its_training_with_it(db, tmp_path):
    class_id = _class(db)
    sample_id = _sample(db, class_id)
    db.commit()
    path = store.object_sample_path(sample_id, store.object_sample_dir_for(db))
    assert __import__("os").path.exists(path)

    store.delete_object_class(db, class_id)
    db.commit()
    assert store.object_classes(db) == []
    assert not __import__("os").path.exists(path), "the crop was left on disk"


# --- the review queue ---------------------------------------------------------


def test_an_unlabelled_crop_waits_in_the_queue(db):
    _sample(db)
    db.commit()
    assert store.object_review_pending(db) == 1
    assert len(store.object_review_queue(db)) == 1


def test_labelling_takes_it_out_of_the_queue(db):
    class_id = _class(db)
    sample_id = _sample(db)
    db.commit()

    store.label_object_sample(db, sample_id, class_id=class_id)
    db.commit()
    assert store.object_review_pending(db) == 0
    assert store.object_classes(db)[0]["samples"] == 1


def test_ignoring_is_a_decision_not_an_absence(db):
    """A wheelie bin somebody looked at and chose not to teach. If ignoring
    only cleared the class the queue would offer it again forever."""
    sample_id = _sample(db)
    db.commit()
    store.label_object_sample(db, sample_id, ignore=True)
    db.commit()

    assert store.object_review_pending(db) == 0
    assert store.object_review_queue(db) == []


def test_an_ignored_crop_never_becomes_training(db):
    class_id = _class(db)
    sample_id = _sample(db, class_id)
    db.commit()
    store.label_object_sample(db, sample_id, ignore=True)
    db.commit()
    assert store.object_prints(db) == []


def test_the_queue_is_oldest_first(db):
    """So a queue spanning dusk is worked through in the order the light
    changed, rather than showing the same afternoon over and over."""
    for stamp in ("2026-09-02T20:00:00", "2026-09-02T06:00:00", "2026-09-02T13:00:00"):
        store.save_object_sample(db, b"\xff\xd8x", 10, 10, camera="drive", at=stamp)
    db.commit()
    queue = store.object_review_queue(db)
    assert [row["taken_at"] for row in queue] == [
        "2026-09-02T06:00:00", "2026-09-02T13:00:00", "2026-09-02T20:00:00"]


def test_labelling_something_that_is_not_there(db):
    with pytest.raises(ValueError, match="no such sample"):
        store.label_object_sample(db, 999, class_id=1)


# --- what the matcher is given ------------------------------------------------


def test_only_labelled_crops_become_prints(db):
    class_id = _class(db)
    _sample(db, class_id)
    _sample(db, None)          # still in the queue
    db.commit()
    assert len(store.object_prints(db)) == 1


def test_prints_are_filtered_by_the_backbone_that_made_them(db):
    """A vector from one model means nothing to another, and comparing across
    them would not error — it would score strangers as matches."""
    class_id = _class(db)
    store.save_object_sample(db, b"\xff\xd8x", 10, 10, class_id=class_id,
                             embedding=b"\x00" * 16, dims=4, model="squeezenet1.1")
    store.save_object_sample(db, b"\xff\xd8x", 10, 10, class_id=class_id,
                             embedding=b"\x00" * 16, dims=4, model="something-else")
    db.commit()
    assert len(store.object_prints(db, model="squeezenet1.1")) == 1
    assert len(store.object_prints(db)) == 2


def test_a_crop_with_no_vector_is_not_a_print(db):
    """Saved before the embedder was available. It is still an image worth
    keeping, and re-embedding it later is exactly why the image is kept."""
    class_id = _class(db)
    store.save_object_sample(db, b"\xff\xd8x", 10, 10, class_id=class_id)
    db.commit()
    assert store.object_prints(db) == []
    assert store.object_classes(db)[0]["samples"] == 1


# --- the routes ---------------------------------------------------------------


def test_the_class_lifecycle_through_the_api(client):
    created = client.post("/api/objects", json={"name": "cargo bike"})
    assert created.status_code == 201
    class_id = created.get_json()["id"]

    listed = client.get("/api/objects").get_json()
    assert [c["name"] for c in listed["classes"]] == ["cargo bike"]
    assert listed["threshold"] and listed["margin"]

    assert client.delete(f"/api/objects/{class_id}").get_json()["classes"] == []


def test_a_class_without_a_name_is_a_400(client):
    assert client.post("/api/objects", json={"name": "  "}).status_code == 400


def test_labelling_by_name_creates_the_class(client, db):
    """Otherwise somebody working a queue has to leave it, create a class, and
    find their place again."""
    sample_id = _sample(db)
    db.commit()

    body = client.post(f"/api/objects/review/{sample_id}",
                       json={"name": "cargo bike"}).get_json()
    assert body["pending"] == 0
    assert [c["name"] for c in body["classes"]] == ["cargo bike"]
    assert body["classes"][0]["samples"] == 1


def test_ignoring_through_the_api(client, db):
    sample_id = _sample(db)
    db.commit()
    assert client.post(f"/api/objects/review/{sample_id}",
                       json={"ignore": True}).get_json()["pending"] == 0


def test_labelling_a_missing_sample_is_a_404(client):
    assert client.post("/api/objects/review/999", json={"ignore": True}).status_code == 404


def test_the_crop_is_served_from_its_own_directory(client, db):
    """Not the snapshot one: snapshots are pruned after a week and training
    material is not."""
    sample_id = _sample(db, jpeg=b"\xff\xd8" + b"x" * 200)
    db.commit()
    response = client.get(f"/api/objects/samples/{sample_id}")
    assert response.status_code == 200
    assert response.mimetype == "image/jpeg"


def test_a_missing_crop_is_a_404(client):
    assert client.get("/api/objects/samples/999").status_code == 404


# --- what happens when a camera sees something --------------------------------


@pytest.fixture
def frames():
    """A plain frame and the same frame with an object in it."""
    cv2 = pytest.importorskip("cv2")
    import numpy as np
    before = np.full((480, 640, 3), 90, np.uint8)
    after = before.copy()
    cv2.rectangle(after, (200, 150), (360, 330), (230, 230, 230), -1)
    return before, after


def test_an_unrecognised_region_is_kept_for_review(db_path, frames, set_options):
    """Nothing is trained yet, so nothing can be named — and the crop is worth
    keeping precisely because it is the sort of frame this camera produces."""
    import app as hub
    import detector
    set_options(object_learning=True)
    _, after = frames
    regions = detector.motion_regions(*frames)

    hub._on_camera_regions("drive", after, regions)

    conn = store.connect(db_path)
    assert store.object_review_pending(conn) == 1
    [queued] = store.object_review_queue(conn)
    assert queued["camera"] == "drive"
    assert json.loads(queued["box"])[2] > 0
    conn.close()


def test_the_queue_does_not_flood_from_one_passing_car(db_path, frames, set_options):
    """Motion on forty consecutive frames is one car, not forty objects. A
    queue nobody can finish is a queue nobody starts."""
    import app as hub
    import detector
    set_options(object_learning=True)
    _, after = frames
    regions = detector.motion_regions(*frames)

    for _ in range(5):
        hub._on_camera_regions("drive", after, regions)

    conn = store.connect(db_path)
    assert store.object_review_pending(conn) == 1
    conn.close()


def test_a_full_queue_stops_collecting(db_path, frames, set_options, monkeypatch):
    import app as hub
    import detector
    set_options(object_learning=True)
    monkeypatch.setattr(hub, "OBJECT_QUEUE_MAX", 0)
    _, after = frames

    hub._on_camera_regions("drive", after, detector.motion_regions(*frames))

    conn = store.connect(db_path)
    assert store.object_review_pending(conn) == 0
    conn.close()


def test_a_recognised_region_is_recorded_as_a_detection(db_path, frames, set_options,
                                                        monkeypatch):
    """The point of the whole feature: a class the household taught it appears
    in the detection history like any of the eighty it shipped with."""
    import app as hub
    import detector
    set_options(object_learning=True)

    conn = store.connect(db_path)
    class_id = store.create_object_class(conn, "cargo bike")
    store.save_object_sample(conn, b"\xff\xd8x", 10, 10, class_id=class_id,
                             embedding=b"\x00" * 16, dims=4,
                             model=hub.OBJECT_EMBED_MODEL)
    conn.commit()
    conn.close()

    monkeypatch.setattr(hub, "identify_object", lambda conn, crop: {
        "object_id": class_id, "score": 0.81, "runner_up": 0.1, "name": "cargo bike"})

    _, after = frames
    hub._on_camera_regions("drive", after, detector.motion_regions(*frames))

    conn = store.connect(db_path)
    labels = [row["label"] for row in conn.execute("SELECT label FROM detections")]
    assert labels == ["cargo bike"]
    # And a named region is not also queued for labelling: it is already known.
    assert store.object_review_pending(conn) == 0
    conn.close()


def test_a_storm_of_regions_is_not_all_cropped(db_path, frames, set_options):
    """A frame with more than a handful of changed regions is weather, and
    embedding all of it helps nobody."""
    import app as hub
    _, after = frames
    set_options(object_learning=True)
    many = [[x, 10, 40, 40] for x in range(0, 600, 20)]

    hub._on_camera_regions("drive", after, many)

    conn = store.connect(db_path)
    assert store.object_review_pending(conn) <= 1
    conn.close()


def test_the_machinery_is_off_until_somebody_asks_for_it(set_options):
    """A household that has taught it nothing should pay nothing — no contour
    work on every motion frame, no crops, no queue."""
    import app as hub
    set_options(object_learning=False)
    assert hub.get_object_learning_enabled() is False
    set_options(object_learning=True)
    assert hub.get_object_learning_enabled() is True
