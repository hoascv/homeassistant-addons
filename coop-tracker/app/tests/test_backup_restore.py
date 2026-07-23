import io
import sqlite3

import app as coopapp


def test_backup_returns_sqlite_file(client):
    client.post("/api/log", json={"type": "egg", "count": 3})
    res = client.get("/api/backup")
    assert res.status_code == 200
    assert res.data.startswith(b"SQLite format 3")


def test_restore_without_file_returns_400(client):
    res = client.post("/api/restore", data={})
    assert res.status_code == 400
    assert res.get_json()["error"] == "no file provided"


def test_restore_rejects_non_sqlite_file(client):
    res = client.post(
        "/api/restore",
        data={"file": (io.BytesIO(b"not a database"), "backup.db")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 400
    assert "not a valid" in res.get_json()["error"]


def test_restore_round_trip_preserves_entries(client, tmp_path, monkeypatch):
    client.post("/api/log", json={"type": "egg", "count": 7, "notes": "before restore"})
    backup_bytes = client.get("/api/backup").data

    # Point the app at a brand new, empty database and restore into it —
    # this also exercises DB_PATH being re-read live, with no restart.
    fresh_db = str(tmp_path / "fresh.db")
    monkeypatch.setattr(coopapp, "DB_PATH", fresh_db)
    coopapp.init_db()

    res = client.post(
        "/api/restore",
        data={"file": (io.BytesIO(backup_bytes), "backup.db")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    assert res.get_json()["status"] == "restored"

    entries = client.get("/api/entries").get_json()
    assert len(entries) == 1
    assert entries[0]["count"] == 7
    assert entries[0]["notes"] == "before restore"


def test_restore_round_trip_preserves_nesting_boxes_and_training_samples(client, tmp_path, monkeypatch, conn):
    box_id = client.post("/api/nesting-boxes", json={"name": "Coop A", "width_mm": 320}).get_json()["id"]
    conn.execute(
        "INSERT INTO egg_vision_samples (created_at, photo, image_width, image_height, box_id, "
        "original_detection, corrected_result) VALUES ('2024-01-01', ?, 100, 100, ?, '{}', '{}')",
        (b"fake-jpeg-bytes", box_id),
    )
    conn.commit()
    backup_bytes = client.get("/api/backup").data

    fresh_db = str(tmp_path / "fresh.db")
    monkeypatch.setattr(coopapp, "DB_PATH", fresh_db)
    coopapp.init_db()

    res = client.post(
        "/api/restore",
        data={"file": (io.BytesIO(backup_bytes), "backup.db")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200

    boxes = client.get("/api/nesting-boxes").get_json()
    assert len(boxes) == 1
    assert boxes[0]["name"] == "Coop A"

    restored_conn = coopapp._db_connect_standalone()
    try:
        row = restored_conn.execute("SELECT photo, box_id FROM egg_vision_samples").fetchone()
        assert row["photo"] == b"fake-jpeg-bytes"
        assert row["box_id"] == box_id
    finally:
        restored_conn.close()


def test_restore_backfills_columns_from_older_schema(client, tmp_path):
    # Simulate a backup taken before price/cost/category existed (pre-1.3.0).
    old_backup = tmp_path / "old.db"
    conn = sqlite3.connect(old_backup)
    conn.execute(
        """
        CREATE TABLE logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            ts TEXT NOT NULL,
            count INTEGER,
            food_type TEXT,
            amount TEXT,
            notes TEXT
        )
        """
    )
    conn.execute("INSERT INTO logs (type, ts, count) VALUES ('egg', '2026-01-01T10:00:00', 4)")
    conn.commit()
    conn.close()

    res = client.post(
        "/api/restore",
        data={"file": (io.BytesIO(old_backup.read_bytes()), "old-backup.db")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200

    entries = client.get("/api/entries").get_json()
    assert entries[0]["count"] == 4
    assert entries[0]["price"] is None  # backfilled column, defaults to NULL
    assert entries[0]["egg_sizes"] is None  # backfilled column, defaults to NULL

    # the backfilled column must also be usable going forward, not just present
    res = client.put(f"/api/entries/{entries[0]['id']}", json={"egg_sizes": "M,L"})
    assert res.status_code == 200
    assert client.get("/api/entries").get_json()[0]["egg_sizes"] == "M,L"
