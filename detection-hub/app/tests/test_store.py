"""The store and the change feed.

The feed's shape is a contract with `pipeline-airflow/jobs/trackers_feed.py`,
which reads specific JSON paths and fails obscurely if they move. These tests
assert that contract rather than whatever the code happens to emit.
"""
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
    assert payload["keys"] == {"detections": "id", "cameras": "id"}


def test_export_carries_every_tracked_table_and_the_watermark(db):
    store.record_detections(db, "drive", [_det()])
    store.upsert_camera(db, "drive", "rtsp")
    db.commit()

    payload = store.export(db)
    assert set(payload["tables"]) == {"detections", "cameras"}
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
