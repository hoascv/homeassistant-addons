"""Downloading the database and putting one back.

A backup nobody can restore is half a feature, and a restore that accepts the
wrong file leaves the add-on with no database at all — so the validation is the
part that matters most here.
"""
import sqlite3

import app as electricityapp


def _seed_a_price(conn, spot=1.23, time_dk="2026-08-16T12:00:00"):
    conn.execute(
        "INSERT OR REPLACE INTO prices (time_dk, price_area, spot_price_dkk_kwh, fetched_at) "
        "VALUES (?, 'DK2', ?, '2026-08-16T00:00:00+00:00')",
        (time_dk, spot),
    )
    conn.commit()


def test_backup_returns_a_real_sqlite_database(conn, client, tmp_path):
    _seed_a_price(conn)
    res = client.get("/api/backup")
    assert res.status_code == 200
    assert res.data[:16].startswith(b"SQLite format 3")

    # And it is a usable database holding what was in the original.
    path = tmp_path / "downloaded.db"
    path.write_bytes(res.data)
    restored = sqlite3.connect(str(path))
    assert restored.execute("SELECT spot_price_dkk_kwh FROM prices").fetchone()[0] == 1.23
    restored.close()


def test_backup_is_offered_as_a_dated_file(conn, client):
    disposition = client.get("/api/backup").headers.get("Content-Disposition", "")
    assert "attachment" in disposition
    assert "electricity-tracker-backup-" in disposition
    assert disposition.rstrip('"').endswith(".db")


def test_no_temporary_copy_survives_a_download(conn, client, tmp_path):
    """The snapshot is a full second copy of the database. Werkzeug's
    call_on_close does not reliably fire, so cleanup cannot depend on it —
    otherwise every download leaves another copy on disk forever."""
    import glob
    import os
    import tempfile

    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "electricity-backup-*")))
    for _ in range(3):
        assert client.get("/api/backup").status_code == 200
    after = set(glob.glob(os.path.join(tempfile.gettempdir(), "electricity-backup-*")))
    assert after == before
    assert not os.path.exists(electricityapp.DB_PATH + ".snapshot")


def test_restore_puts_a_backup_back(conn, client, tmp_path):
    _seed_a_price(conn, spot=9.99, time_dk="2026-08-16T05:00:00")
    saved = client.get("/api/backup").data

    # Move on, then restore: the later state must be replaced by the backup.
    conn.execute("DELETE FROM prices")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 0

    res = client.post("/api/restore", data={"file": (__import__("io").BytesIO(saved), "backup.db")},
                      content_type="multipart/form-data")
    assert res.status_code == 200
    assert res.get_json()["status"] == "restored"

    fresh = electricityapp._db_connect_standalone()
    assert fresh.execute("SELECT spot_price_dkk_kwh FROM prices").fetchone()[0] == 9.99
    fresh.close()


def test_restore_refuses_a_file_that_is_not_this_add_ons_database(conn, client, tmp_path):
    """Restoring a Goal Tracker backup here would swap a working database for
    one with none of the right tables, and the add-on would come back empty."""
    import io

    other = tmp_path / "other.db"
    wrong = sqlite3.connect(str(other))
    wrong.execute("CREATE TABLE weight_logs (id INTEGER PRIMARY KEY)")
    wrong.commit()
    wrong.close()

    res = client.post("/api/restore",
                      data={"file": (io.BytesIO(other.read_bytes()), "gym.db")},
                      content_type="multipart/form-data")
    assert res.status_code == 400
    assert "not a valid" in res.get_json()["error"]


def test_restore_refuses_something_that_is_not_a_database_at_all(conn, client):
    import io

    res = client.post("/api/restore",
                      data={"file": (io.BytesIO(b"just some text, not a database"), "notes.txt")},
                      content_type="multipart/form-data")
    assert res.status_code == 400


def test_a_rejected_restore_leaves_the_original_untouched(conn, client):
    """The whole point of validating before the swap."""
    import io

    _seed_a_price(conn, spot=4.44)
    client.post("/api/restore", data={"file": (io.BytesIO(b"nope"), "x.db")},
                content_type="multipart/form-data")
    fresh = electricityapp._db_connect_standalone()
    assert fresh.execute("SELECT spot_price_dkk_kwh FROM prices").fetchone()[0] == 4.44
    fresh.close()


def test_a_rejected_restore_leaves_no_upload_behind(conn, client):
    import io
    import os

    client.post("/api/restore", data={"file": (io.BytesIO(b"nope"), "x.db")},
                content_type="multipart/form-data")
    assert not os.path.exists(electricityapp.DB_PATH + ".upload")


def test_restore_without_a_file_is_a_clear_error(conn, client):
    assert client.post("/api/restore", data={}, content_type="multipart/form-data").status_code == 400


def test_restore_reruns_the_migrations(conn, client, tmp_path):
    """A backup taken before a column existed must come back usable, not with a
    schema the current code cannot query."""
    import io

    old = tmp_path / "old.db"
    legacy = sqlite3.connect(str(old))
    legacy.execute("CREATE TABLE prices (time_dk TEXT, price_area TEXT, spot_price_dkk_kwh REAL, "
                   "fetched_at TEXT, PRIMARY KEY (time_dk, price_area))")
    legacy.execute("CREATE TABLE consumption (time_utc TEXT, metering_point TEXT, kwh REAL, "
                   "quality TEXT, fetched_at TEXT, PRIMARY KEY (time_utc, metering_point))")
    # Predates reason_for_no_current, added in 1.6.2.
    legacy.execute("CREATE TABLE easee_samples (ts_utc TEXT, charger_id TEXT, status TEXT, "
                   "session_energy_kwh REAL, total_power_w REAL, fetched_at TEXT, "
                   "PRIMARY KEY (ts_utc, charger_id))")
    legacy.commit()
    legacy.close()

    res = client.post("/api/restore", data={"file": (io.BytesIO(old.read_bytes()), "old.db")},
                      content_type="multipart/form-data")
    assert res.status_code == 200

    fresh = electricityapp._db_connect_standalone()
    columns = {r[1] for r in fresh.execute("PRAGMA table_info(easee_samples)")}
    assert "reason_for_no_current" in columns
    fresh.close()
