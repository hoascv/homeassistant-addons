"""The store and the change feed.

The feed's shape is a contract with `pipeline-airflow/jobs/trackers_feed.py`,
which reads specific JSON paths and fails obscurely if they move. These tests
assert that contract rather than whatever the code happens to emit.
"""
import os
from datetime import datetime, timedelta

import pytest

import store


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "detections.db")
    store.init_db(path)
    conn = store.connect(path, actor="user")
    conn.db_path = path
    yield conn
    conn.close()


def _det(label="person", conf=0.9, box=(1, 2, 3, 4)):
    return {"label": label, "confidence": conf, "box": list(box)}


# --- schema -------------------------------------------------------------------


def test_init_db_is_idempotent(tmp_path):
    """It runs on every boot, including over an existing database."""
    path = str(tmp_path / "d.db")
    store.init_db(path)
    store.init_db(path)
    conn = store.connect(path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"detections", "snapshots", "cameras", "change_log"} <= tables
    conn.close()


def test_a_detection_survives_its_snapshot(db):
    """Images age out far faster than rows, so the reference is nullable and
    pruning clears it rather than leaving it dangling."""
    snap = store.save_snapshot(db, b"\xff\xd8fake", 640, 480)
    store.record_detections(db, "drive", [_det()], snapshot_id=snap)
    db.commit()

    db.execute("DELETE FROM snapshots")
    store.prune(db, snapshot_max=0)
    db.commit()

    row = db.execute("SELECT snapshot_id FROM detections").fetchone()
    assert row["snapshot_id"] is None


# --- change feed --------------------------------------------------------------


def test_inserting_a_detection_writes_a_change_row(db):
    store.record_detections(db, "drive", [_det()])
    db.commit()
    changes = store.changes(db)["changes"]
    assert len(changes) == 1
    assert changes[0]["table"] == "detections"
    assert changes[0]["op"] == "I"


def test_the_feed_carries_the_row_itself(db):
    store.record_detections(db, "drive", [_det(label="car", conf=0.7)])
    db.commit()
    row = store.changes(db)["changes"][0]["row"]
    assert row["camera"] == "drive"
    assert row["label"] == "car"
    assert row["confidence"] == 0.7


def test_a_delete_carries_a_null_row_and_op_D(db):
    """There is no honest payload for a row that is gone; consumers key on op."""
    store.record_detections(db, "drive", [_det()])
    db.commit()
    db.execute("DELETE FROM detections")
    db.commit()

    last = store.changes(db)["changes"][-1]
    assert last["op"] == "D"
    assert last["row"] is None


def test_since_returns_only_what_came_after(db):
    store.record_detections(db, "a", [_det()])
    db.commit()
    watermark = store.max_seq(db)
    store.record_detections(db, "b", [_det()])
    db.commit()

    changes = store.changes(db, since=watermark)["changes"]
    assert [c["row"]["camera"] for c in changes] == ["b"]


def test_the_actor_records_who_caused_it(db, tmp_path):
    """A detection written by a camera thread is not a user editing something,
    and the lakehouse keeps the distinction."""
    store.record_detections(db, "drive", [_det()])
    db.commit()
    assert store.changes(db)["changes"][0]["actor"] == "user"

    camera_conn = store.connect(db.db_path, actor="camera")
    store.record_detections(camera_conn, "drive", [_det()])
    camera_conn.commit()
    camera_conn.close()

    assert store.changes(db)["changes"][-1]["actor"] == "camera"


def test_full_reload_required_when_the_watermark_fell_off_the_end(db):
    """A consumer further behind than our oldest surviving row cannot catch up
    incrementally, and saying so is the difference between a visible bootstrap
    and a silent gap."""
    store.record_detections(db, "a", [_det()])
    db.commit()
    db.execute("DELETE FROM change_log WHERE seq < 5")
    db.execute("INSERT INTO change_log (seq, table_name, row_id, op, changed_at)"
               " VALUES (50, 'detections', '1', 'I', '2026-01-01T00:00:00')")
    db.commit()

    assert store.changes(db, since=2)["full_reload_required"] is True
    assert store.changes(db, since=49)["full_reload_required"] is False


def test_a_fresh_consumer_is_not_told_to_reload(db):
    """since=0 is a first run, not a gap."""
    store.record_detections(db, "a", [_det()])
    db.commit()
    assert store.changes(db, since=0)["full_reload_required"] is False


def test_limit_is_clamped(db):
    for i in range(5):
        store.record_detections(db, "a", [_det()])
    db.commit()
    assert len(store.changes(db, limit=2)["changes"]) == 2
    assert len(store.changes(db, limit=99999)["changes"]) == 5


# --- export and stats ---------------------------------------------------------


def test_export_names_the_key_column_per_table(db):
    """trackers_merge.py needs this to know what identifies a row; guessing the
    first JSON key collapses a table."""
    payload = store.export(db)
    assert payload["keys"] == {"detections": "id", "cameras": "id", "people": "id",
                               "object_classes": "id"}


def test_export_carries_every_tracked_table_and_the_watermark(db):
    store.record_detections(db, "drive", [_det()])
    store.upsert_camera(db, "drive", "rtsp")
    db.commit()

    payload = store.export(db)
    # Pinned deliberately: a table added to the feed without the lakehouse
    # schema being widened to match is data silently dropped downstream, so the
    # set changing should have to be a decision somebody wrote down.
    assert set(payload["tables"]) == {"detections", "cameras", "people",
                                      "object_classes"}
    assert len(payload["tables"]["detections"]) == 1
    assert payload["max_seq"] == store.max_seq(db)


def test_snapshots_never_appear_in_the_feed(db):
    """The whole reason images live in their own table: a change event stays
    small, and a megabyte of JPEG never reaches the pipeline as base64."""
    snap = store.save_snapshot(db, b"\xff\xd8" + b"x" * 5000, 640, 480)
    store.record_detections(db, "drive", [_det()], snapshot_id=snap)
    db.commit()

    payload = store.export(db)
    assert "snapshots" not in payload["tables"]

    serialised = str(store.changes(db))
    assert "xxxx" not in serialised, "image bytes leaked into the change feed"
    # The reference survives, so a consumer can still fetch it deliberately.
    assert payload["tables"]["detections"][0]["snapshot_id"] == snap


def test_stats_counts_without_serialising(db):
    store.record_detections(db, "drive", [_det(), _det(label="car")])
    store.save_snapshot(db, b"\xff\xd8", 1, 1)
    db.commit()

    result = store.stats(db, db_path=db.db_path)
    assert result["counts"]["detections"] == 2
    assert result["other_counts"]["snapshots"] == 1
    assert result["total"] == result["counts"]["detections"] + result["counts"]["cameras"]
    assert result["total_all"] == result["total"] + result["other_total"]
    assert result["db_bytes"] > 0


# --- retention ----------------------------------------------------------------


def test_old_detections_are_pruned_by_age(db):
    old = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%S")
    store.record_detections(db, "drive", [_det()], at=old)
    store.record_detections(db, "drive", [_det()])
    db.commit()

    store.prune(db, detection_days=30)
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM detections").fetchone()[0] == 1


def test_snapshots_are_capped_by_count_as_well_as_age(db):
    """A busy hour blows past any size expectation well inside the age window,
    and /data is inside Home Assistant's backups."""
    for _ in range(10):
        store.save_snapshot(db, b"\xff\xd8", 1, 1)
    db.commit()

    store.prune(db, snapshot_max=4)
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 4


def test_pruning_keeps_the_newest_snapshots(db):
    ids = [store.save_snapshot(db, b"\xff\xd8", 1, 1) for _ in range(6)]
    db.commit()
    store.prune(db, snapshot_max=2)
    db.commit()
    kept = [r[0] for r in db.execute("SELECT id FROM snapshots ORDER BY id")]
    assert kept == ids[-2:]


def test_change_log_pruning_keeps_the_last_entry_per_row(db):
    """Otherwise a consumer rebuilding from the feed lands on a partial history
    instead of current state."""
    store.record_detections(db, "drive", [_det()])
    db.commit()
    old = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%S")
    db.execute("UPDATE change_log SET changed_at = ?", (old,))
    db.commit()

    store.prune_change_log(db)
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM change_log").fetchone()[0] == 1


# --- cameras ------------------------------------------------------------------


def test_upsert_registers_then_updates_without_clobbering(db):
    """The capture thread and a config reload both call this, and neither
    should erase what the other knows."""
    store.upsert_camera(db, "drive", "rtsp", state="starting")
    store.upsert_camera(db, "drive", "rtsp", last_frame_at="2026-01-01T00:00:00")
    db.commit()

    row = db.execute("SELECT * FROM cameras WHERE id = 'drive'").fetchone()
    assert row["state"] == "starting"
    assert row["last_frame_at"] == "2026-01-01T00:00:00"
    assert db.execute("SELECT COUNT(*) FROM cameras").fetchone()[0] == 1


def test_counters_accumulate(db):
    store.upsert_camera(db, "drive", "rtsp")
    store.bump_camera_counters(db, "drive", frames_seen=10, frames_detected=2)
    store.bump_camera_counters(db, "drive", frames_seen=5, frames_detected=1)
    db.commit()
    row = db.execute("SELECT * FROM cameras WHERE id = 'drive'").fetchone()
    assert row["frames_seen"] == 15 and row["frames_detected"] == 3


def test_counts_since_groups_by_label(db):
    store.record_detections(db, "a", [_det(), _det(), _det(label="car")])
    db.commit()
    assert store.counts_since(db, "2000-01-01T00:00:00") == {"person": 2, "car": 1}


# --- people and their faces ---------------------------------------------------


def _print_bytes(seed=1.0):
    """A stand-in embedding: 128 float32, which is what the column expects."""
    import numpy as np

    return np.full(128, seed, dtype="<f4").tobytes()


def test_a_person_can_be_added_renamed_and_found(db):
    person_id = store.add_person(db, "Alice")
    assert store.rename_person(db, person_id, "Alice B") is True
    db.commit()

    everyone = store.people(db)
    assert [p["name"] for p in everyone] == ["Alice B"]
    assert everyone[0]["prints"] == 0


def test_two_people_cannot_share_a_name(db):
    """A duplicate is somebody typing the same name twice, not a fault — so the
    caller gets None to turn into a 409 rather than an exception in a route."""
    assert store.add_person(db, "Alice") is not None
    assert store.add_person(db, "Alice") is None


def test_prints_carry_the_model_that_made_them(db):
    """A vector from one recogniser scored against another's would not error, it
    would quietly rate strangers as matches — so the gallery is filtered."""
    person_id = store.add_person(db, "Alice")
    store.add_face_print(db, person_id, _print_bytes(), "sface_2021dec", "snapshot")
    store.add_face_print(db, person_id, _print_bytes(2.0), "some_other_model", "snapshot")
    db.commit()

    assert len(store.face_prints(db, model="sface_2021dec")) == 1
    assert len(store.face_prints(db)) == 2


def test_the_gallery_skips_archived_people(db):
    """Archived means removed as far as matching is concerned; the row survives
    only so old detections still read as somebody."""
    person_id = store.add_person(db, "Alice")
    store.add_face_print(db, person_id, _print_bytes(), "sface_2021dec", "snapshot")
    store.record_detections(db, "drive", [_det()])
    store.set_detection_identity(db, 1, person_id, 0.7, "matched")
    db.commit()

    store.delete_person(db, person_id)
    db.commit()
    assert store.face_prints(db, model="sface_2021dec") == []


def test_deleting_a_person_really_deletes_the_biometrics(db, tmp_path):
    """The promise this feature makes. It is only honest because face_prints
    never entered the change feed — a published row could not be recalled."""
    directory = str(tmp_path / "faces")
    person_id = store.add_person(db, "Alice")
    print_id = store.add_face_print(db, person_id, _print_bytes(), "sface_2021dec",
                                    "snapshot", crop_jpeg=b"\xff\xd8jpeg",
                                    directory=directory)
    db.commit()
    assert os.path.exists(store.face_path(print_id, directory))

    outcome = store.delete_person(db, person_id, directory=directory)
    db.commit()
    assert outcome == {"prints": 1, "person": "deleted"}
    assert db.execute("SELECT COUNT(*) FROM face_prints").fetchone()[0] == 0
    assert not os.path.exists(store.face_path(print_id, directory))


def test_a_person_with_history_is_archived_rather_than_erased(db):
    """Deleting the name out from under old detections would leave rows pointing
    at nobody; the history stays readable, the template does not survive."""
    person_id = store.add_person(db, "Alice")
    store.add_face_print(db, person_id, _print_bytes(), "sface_2021dec", "snapshot")
    store.record_detections(db, "drive", [_det()])
    store.set_detection_identity(db, 1, person_id, 0.72, "matched")
    db.commit()

    assert store.delete_person(db, person_id) == {"prints": 1, "person": "archived"}
    db.commit()
    assert db.execute("SELECT archived FROM people WHERE id = ?", (person_id,)).fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM face_prints").fetchone()[0] == 0


def test_an_identified_detection_carries_the_name(db):
    person_id = store.add_person(db, "Alice")
    store.record_detections(db, "drive", [_det()])
    store.set_detection_identity(db, 1, person_id, 0.68, "matched")
    db.commit()

    row = store.recent_detections(db)[0]
    assert row["person"] == "Alice"
    assert row["face_state"] == "matched"
    assert row["person_score"] == 0.68


def test_the_four_face_states_stay_distinct(db):
    """Collapsing these would leave a reader unable to tell a stranger from a
    camera that never sees a face — different problems with different fixes."""
    store.record_detections(db, "drive", [_det(), _det(), _det(), _det()])
    store.set_detection_identity(db, 2, None, None, "no_face")
    store.set_detection_identity(db, 3, None, 0.31, "unknown")
    store.set_detection_identity(db, 4, store.add_person(db, "Alice"), 0.8, "matched")
    db.commit()

    rows = {r["id"]: r for r in store.recent_detections(db)}
    assert rows[1]["face_state"] is None, "nothing looked at this one"
    assert rows[2]["face_state"] == "no_face" and rows[2]["person_score"] is None
    assert rows[3]["face_state"] == "unknown" and rows[3]["person_score"] == 0.31
    assert rows[4]["person"] == "Alice"


def test_enrolled_prints_survive_a_prune_that_deletes_everything_else(db, tmp_path):
    """Prints are curated data, not observations. Retention is about what the
    camera saw, and a person you enrolled is not something it saw."""
    old = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%S")
    person_id = store.add_person(db, "Alice")
    snap = store.save_snapshot(db, b"\xff\xd8jpeg", 640, 480,
                               directory=str(tmp_path / "snaps"))
    store.add_face_print(db, person_id, _print_bytes(), "sface_2021dec", "snapshot",
                         source_snapshot_id=snap, directory=str(tmp_path / "faces"))
    store.record_detections(db, "drive", [_det()], snapshot_id=snap, at=old)
    db.execute("UPDATE snapshots SET taken_at = ?", (old,))
    db.commit()

    store.prune(db, detection_days=1, snapshot_days=1, directory=str(tmp_path / "snaps"))
    db.commit()

    assert db.execute("SELECT COUNT(*) FROM detections").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 0
    assert len(store.face_prints(db, model="sface_2021dec")) == 1, "prune ate an enrolment"
    # The image it came from is gone, so the pointer says so rather than naming
    # a row that no longer exists.
    assert db.execute("SELECT source_snapshot_id FROM face_prints").fetchone()[0] is None


def test_an_orphan_face_crop_is_swept(db, tmp_path):
    """A crash between deleting the row and the file must not leave a picture of
    somebody's face on disk that nothing references."""
    directory = str(tmp_path / "faces")
    os.makedirs(directory)
    with open(os.path.join(directory, "999.jpg"), "wb") as handle:
        handle.write(b"\xff\xd8jpeg")

    assert store.sweep_orphan_face_files(db, directory) == 1
    assert not os.path.exists(os.path.join(directory, "999.jpg"))


def test_an_embedding_never_reaches_the_change_feed(db):
    """The load-bearing privacy property: a biometric template that entered the
    feed would be copied into Delta history and a replica, where a local DELETE
    can never reach it."""
    person_id = store.add_person(db, "Alice")
    store.add_face_print(db, person_id, _print_bytes(7.5), "sface_2021dec", "snapshot")
    db.commit()

    payload = store.export(db)
    assert "face_prints" not in payload["tables"]
    assert "people" in payload["tables"], "the name is meant to be exported"

    serialised = str(store.changes(db)) + str(payload)
    assert "embedding" not in serialised
    assert _print_bytes(7.5).hex()[:16] not in serialised


def test_identifications_are_counted_per_person(db):
    alice, bob = store.add_person(db, "Alice"), store.add_person(db, "Bob")
    store.record_detections(db, "drive", [_det(), _det(), _det()])
    store.set_detection_identity(db, 1, alice, 0.7, "matched")
    store.set_detection_identity(db, 2, alice, 0.8, "matched")
    store.set_detection_identity(db, 3, bob, 0.9, "matched")
    db.commit()

    assert store.person_counts_since(db, "2000-01-01T00:00:00") == {"Alice": 2, "Bob": 1}
    assert store.last_identified(db)["name"] == "Bob"


def test_recording_detections_returns_the_ids_it_wrote(db):
    """Identification comes back a few frames later and has to name the row that
    was written on arrival."""
    ids = store.record_detections(db, "drive", [_det(), _det(label="car")])
    db.commit()
    assert ids == [1, 2]
    assert store.record_detections(db, "drive", []) == []


def test_object_samples_never_appear_in_the_feed(db):
    """The crops and their vectors are training material and images — the same
    argument that keeps face_prints out. The class *names* are in the feed so a
    custom label on a detection is readable; what taught it is not."""
    store.create_object_class(db, "cargo bike")
    db.commit()
    payload = store.export(db)
    assert "object_classes" in payload["tables"]
    assert "object_samples" not in payload["tables"]
