"""Where the bytes live, and how the database behaves under several threads.

Two subjects that look unrelated and are not: both are about this add-on writing
from many threads into one small file, and both were got wrong first time.
"""
import os
import sqlite3
import threading
import time

import pytest

import store


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "detections.db")
    store.init_db(path)
    conn = store.connect(path, actor="user")
    yield conn
    conn.close()


JPEG = b"\xff\xd8" + b"fake image bytes" * 40


# --- the pragmas --------------------------------------------------------------


def test_wal_is_on(db):
    """The whole tuning is worthless if a later refactor drops the pragma, and
    nothing else would notice."""
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_synchronous_is_normal(db):
    """1 is NORMAL. Durable across a crash of this process under WAL, one fewer
    fsync per commit — which matters on a host measured at 286 durable
    commits/s."""
    assert db.execute("PRAGMA synchronous").fetchone()[0] == 1


def test_busy_timeout_is_not_zero(db):
    """Python's sqlite3.connect defaults to timeout=5.0, so this is 5000 rather
    than the C library's 0. Asserted because it is easy to assume otherwise and
    then "fix" a bug that never existed — as happened here."""
    assert db.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000


def test_a_second_writer_waits_rather_than_failing(tmp_path):
    """A held write transaction must not make another thread's write fail."""
    path = str(tmp_path / "d.db")
    store.init_db(path)
    holder = store.connect(path, actor="camera")
    holder.execute("BEGIN IMMEDIATE")
    holder.execute(
        "INSERT INTO detections (camera,label,confidence,detected_at)"
        " VALUES ('a','person',0.9,'x')"
    )

    outcome = {}

    def writer():
        conn = store.connect(path, actor="user")
        try:
            conn.execute(
                "INSERT INTO detections (camera,label,confidence,detected_at)"
                " VALUES ('b','car',0.8,'y')"
            )
            conn.commit()
            outcome["ok"] = True
        except sqlite3.OperationalError as exc:
            outcome["error"] = str(exc)
        finally:
            conn.close()

    thread = threading.Thread(target=writer)
    thread.start()
    time.sleep(0.3)
    holder.commit()
    holder.close()
    thread.join(timeout=10)

    assert outcome.get("ok"), f"second writer failed: {outcome.get('error')}"


def test_many_writers_and_a_reader_lose_nothing(tmp_path):
    """The add-on's real shape: a camera thread per stream, request threads
    serving the feed, and the prune loop."""
    path = str(tmp_path / "d.db")
    store.init_db(path)
    errors, stop = [], threading.Event()

    def writer(n):
        conn = store.connect(path, actor="camera")
        try:
            for _ in range(40):
                store.record_detections(
                    conn, f"cam{n}",
                    [{"label": "person", "confidence": 0.9, "box": [1, 2, 3, 4]}],
                )
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"writer{n}: {exc}")
        finally:
            conn.close()

    def reader():
        conn = store.connect(path, actor="user")
        try:
            while not stop.is_set():
                store.export(conn)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"reader: {exc}")
        finally:
            conn.close()

    r = threading.Thread(target=reader)
    r.start()
    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    stop.set()
    r.join(timeout=5)

    assert errors == []
    conn = store.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM detections").fetchone()[0] == 160
    conn.close()


def test_checkpoint_needs_no_open_transaction(db):
    """It answers "database table is locked" inside one, which is why it is not
    part of prune()."""
    assert store.checkpoint(db) is None


# --- images as files ----------------------------------------------------------


def test_a_snapshot_is_written_beside_the_database(db, tmp_path):
    snapshot_id = store.save_snapshot(db, JPEG, 640, 480)
    db.commit()

    path = tmp_path / "snapshots" / f"{snapshot_id}.jpg"
    assert path.exists()
    assert path.read_bytes() == JPEG


def test_the_row_records_the_size_not_the_bytes(db):
    """`bytes` is what makes disk use a SUM instead of a directory walk."""
    snapshot_id = store.save_snapshot(db, JPEG, 640, 480)
    db.commit()
    row = db.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    assert row["bytes"] == len(JPEG)
    assert "image" not in row.keys(), "the blob column should be gone"


def test_images_follow_the_connection_not_a_global(tmp_path):
    """Two databases in one process must not write pictures into each other's
    directory — which they did until the directory was derived from the
    connection."""
    for name in ("one", "two"):
        path = str(tmp_path / name / "detections.db")
        store.init_db(path)
        conn = store.connect(path)
        store.save_snapshot(conn, JPEG, 1, 1)
        conn.commit()
        conn.close()
        assert (tmp_path / name / "snapshots" / "1.jpg").exists()


def test_snapshot_returns_a_path_that_exists(db):
    snapshot_id = store.save_snapshot(db, JPEG, 640, 480)
    db.commit()
    found = store.snapshot(db, snapshot_id)
    assert found["width"] == 640 and found["bytes"] == len(JPEG)
    assert os.path.exists(found["path"])


def test_a_row_whose_file_is_gone_reads_as_missing(db):
    """The normal state after restoring a backup: rows come back, images do
    not. It must not be an error."""
    snapshot_id = store.save_snapshot(db, JPEG, 640, 480)
    db.commit()
    os.remove(store.snapshot(db, snapshot_id)["path"])
    assert store.snapshot(db, snapshot_id) is None


def test_an_unwritable_directory_loses_the_image_not_the_detection(db, monkeypatch):
    """A full disk must cost the picture, never the row."""
    def explode(*args, **kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr("builtins.open", explode)
    snapshot_id = store.save_snapshot(db, JPEG, 640, 480)

    assert snapshot_id is None
    assert db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 0, (
        "the half-made row should have been removed"
    )


# --- retention over files -----------------------------------------------------


def test_pruning_removes_the_file_as_well_as_the_row(db, tmp_path):
    ids = [store.save_snapshot(db, JPEG, 1, 1) for _ in range(5)]
    db.commit()

    store.prune(db, snapshot_max=2)
    db.commit()

    for gone in ids[:3]:
        assert not (tmp_path / "snapshots" / f"{gone}.jpg").exists()
    for kept in ids[-2:]:
        assert (tmp_path / "snapshots" / f"{kept}.jpg").exists()


def test_an_orphan_file_is_swept(db, tmp_path):
    """What makes the two-step delete self-healing: a crash between removing the
    row and removing the file would otherwise leak an image that nothing
    references and no retention rule can ever find again."""
    store.save_snapshot(db, JPEG, 1, 1)
    db.commit()
    orphan = tmp_path / "snapshots" / "9999.jpg"
    orphan.write_bytes(JPEG)

    assert store.sweep_orphan_files(db) == 1
    assert not orphan.exists()


def test_the_sweep_leaves_files_it_does_not_understand(db, tmp_path):
    """Only `<id>.jpg` is ours. Deleting anything else in that directory would
    be overreach."""
    directory = tmp_path / "snapshots"
    directory.mkdir(exist_ok=True)
    stranger = directory / "notes.txt"
    stranger.write_text("not mine")

    store.sweep_orphan_files(db)
    assert stranger.exists()


def test_stats_reports_image_bytes_separately_from_the_database(db):
    """They are backed up differently — the database is, the images are not —
    so one combined size would hide the distinction that matters."""
    store.save_snapshot(db, JPEG, 1, 1)
    store.save_snapshot(db, JPEG, 1, 1)
    db.commit()

    result = store.stats(db, db_path=db.db_path)
    assert result["snapshot_bytes"] == 2 * len(JPEG)
    assert result["db_bytes"] > 0


# --- migrating an older database ----------------------------------------------


def test_blobs_from_an_older_database_are_written_out(tmp_path):
    """Images lived in an `image` column until 1.3.0. Anything already stored
    has to survive the move, not be discarded because the add-on is young."""
    path = str(tmp_path / "old.db")
    os.makedirs(tmp_path, exist_ok=True)
    legacy = sqlite3.connect(path)
    legacy.execute(
        "CREATE TABLE snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " image BLOB NOT NULL, width INTEGER, height INTEGER, taken_at TEXT NOT NULL)"
    )
    legacy.execute(
        "INSERT INTO snapshots (image, width, height, taken_at) VALUES (?,?,?,?)",
        (sqlite3.Binary(JPEG), 640, 480, "2026-01-01T00:00:00"),
    )
    legacy.commit()
    legacy.close()

    store.init_db(path)

    moved = tmp_path / "snapshots" / "1.jpg"
    assert moved.exists() and moved.read_bytes() == JPEG

    conn = store.connect(path)
    row = conn.execute("SELECT * FROM snapshots WHERE id = 1").fetchone()
    assert "image" not in row.keys(), "the blob column should be gone"
    assert row["bytes"] == len(JPEG), "size should be carried across"
    assert row["taken_at"] == "2026-01-01T00:00:00"
    conn.close()


def test_the_migration_is_idempotent(tmp_path):
    path = str(tmp_path / "old.db")
    legacy = sqlite3.connect(path)
    legacy.execute(
        "CREATE TABLE snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " image BLOB NOT NULL, width INTEGER, height INTEGER, taken_at TEXT NOT NULL)"
    )
    legacy.execute(
        "INSERT INTO snapshots (image, width, height, taken_at) VALUES (?,?,?,?)",
        (sqlite3.Binary(JPEG), 1, 1, "2026-01-01T00:00:00"),
    )
    legacy.commit()
    legacy.close()

    store.init_db(path)
    store.init_db(path)  # a second boot must not undo or duplicate anything

    conn = store.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
    conn.close()
    assert (tmp_path / "snapshots" / "1.jpg").read_bytes() == JPEG
