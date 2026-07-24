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
