import io
import sqlite3


def test_backup_downloads_db(client):
    res = client.get("/api/backup")
    assert res.status_code == 200
    assert res.data[:16].startswith(b"SQLite format 3")
    assert "attachment" in res.headers["Content-Disposition"]


def test_restore_rejects_non_backup(client):
    res = client.post(
        "/api/restore",
        data={"file": (io.BytesIO(b"not a database"), "junk.db")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 400


def test_restore_requires_file(client):
    assert client.post("/api/restore").status_code == 400


def test_restore_replaces_data(client, db_path, tmp_path):
    # Log a custom weight, back up, change data, then restore the backup.
    client.post("/api/weight", json={"weight_kg": 100.9})
    backup = client.get("/api/backup").data
    assert client.delete(
        f"/api/weight/{client.get('/api/weight').get_json()['logs'][-1]['id']}"
    ).status_code == 204

    res = client.post(
        "/api/restore",
        data={"file": (io.BytesIO(backup), "gym.db")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    weights = [l["weight_kg"] for l in client.get("/api/weight").get_json()["logs"]]
    assert 100.9 in weights


def test_valid_backup_detection(client, tmp_path):
    good = str(tmp_path / "good.db")
    conn = sqlite3.connect(good)
    conn.execute("CREATE TABLE weight_logs (id INTEGER)")
    conn.execute("CREATE TABLE challenge_items (id INTEGER)")
    conn.execute("CREATE TABLE exercises (id INTEGER)")
    conn.commit()
    conn.close()

    import app as gymapp
    assert gymapp._is_valid_backup(good) is True

    bad = str(tmp_path / "bad.db")
    conn = sqlite3.connect(bad)
    conn.execute("CREATE TABLE something_else (id INTEGER)")
    conn.commit()
    conn.close()
    assert gymapp._is_valid_backup(bad) is False


def test_restoring_a_pre_challenges_backup_migrates_it(client, db_path, tmp_path):
    """A backup taken before challenges existed must come back usable: the
    restore re-runs the migration, so its items land in a default challenge."""
    import io
    import sqlite3

    import app as gymapp

    old = str(tmp_path / "old.db")
    conn = sqlite3.connect(old)
    conn.executescript(
        """
        CREATE TABLE weight_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
            weight_kg REAL NOT NULL, body_fat_pct REAL, notes TEXT);
        CREATE TABLE goal (id INTEGER PRIMARY KEY, target_date TEXT, target_weight_kg REAL,
            target_body_fat_pct REAL, start_date TEXT, start_weight_kg REAL);
        CREATE TABLE exercises (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            equipment TEXT, category TEXT, is_custom INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0, notes TEXT);
        CREATE TABLE supplements (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            dose TEXT, is_custom INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE workout_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
            exercise_id INTEGER NOT NULL, sets INTEGER, reps INTEGER, weight_kg REAL,
            duration_sec INTEGER, notes TEXT);
        CREATE TABLE challenge_items (id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE challenge_completions (id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL, day TEXT NOT NULL, UNIQUE(item_id, day));
        CREATE TABLE app_state (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO exercises (id, name) VALUES (1, 'Push-up');
        INSERT INTO challenge_items (id, label, sort_order) VALUES (7, 'Push-up × 40', 0);
        INSERT INTO challenge_completions (item_id, day) VALUES (7, '2026-07-05');
        """
    )
    conn.commit()
    conn.close()

    with open(old, "rb") as f:
        payload = {"file": (io.BytesIO(f.read()), "old.db")}
    assert client.post("/api/restore", data=payload, content_type="multipart/form-data").status_code == 200

    challenges = client.get("/api/challenges").get_json()
    assert len(challenges) == 1
    # Backdated to the earliest tick, so the restored history is inside it.
    assert challenges[0]["start_date"] == "2026-07-05"

    restored = sqlite3.connect(db_path)
    restored.row_factory = sqlite3.Row
    orphans = restored.execute(
        "SELECT COUNT(*) FROM challenge_items WHERE challenge_id IS NULL"
    ).fetchone()[0]
    # The legacy untyped item is archived by the 1.0.0 conversion, but it still
    # belongs to a challenge so its completions stay attached to something.
    assert orphans == 0
    restored.close()
