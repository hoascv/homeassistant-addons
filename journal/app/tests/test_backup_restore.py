"""Taking the journal off this machine and putting it back.

The database *is* the backup here, which is the whole point: what comes out is
the same AES-256-GCM ciphertext that sits on disk, so the file is unreadable
without the master password and is safe to keep where the plain-text export is
not. These tests hold that line — a backup that quietly contained readable text
would look identical to a working one until the day it mattered.

The lock rules are the other half, and they are not a plain `@unlocked`:
restoring destroys writing that cannot be recovered, but a fresh install has no
vault to unlock and is exactly the case the feature exists for.
"""
import io
import sqlite3

import pytest

import app as journalapp
import store
from conftest import PASSWORD, an_entry


SECRET = "the coast road, into a headwind the whole way"

@pytest.fixture
def live_client(db_path, options, monkeypatch):
    """A client that opens a connection per request, as production does.

    The shared `client` fixture pins one connection through `get_db`, which is
    right for every other test here and wrong for this one: restore swaps the
    file with os.replace, and a handle opened before the swap goes on reading
    the old inode quite happily. Testing restore through that fixture would be
    testing the fixture.
    """
    journalapp.app.config.update(TESTING=True)
    monkeypatch.setattr(journalapp, "DB_PATH", db_path)
    journalapp.SESSIONS.close_all()
    journalapp.UNLOCK_THROTTLE.record_success()
    with journalapp.app.test_client() as test_client:
        test_client.environ_base["HTTP_X_REMOTE_USER_ID"] = "test-user"
        yield test_client


@pytest.fixture
def live_unlocked(live_client):
    """The same client, past the password."""
    live_client.post("/api/vault", json={"password": PASSWORD})
    token = live_client.post("/api/unlock", json={"password": PASSWORD}).get_json()["token"]
    live_client.environ_base["HTTP_X_JOURNAL_SESSION"] = token
    return live_client


def _unlock(client, password=PASSWORD):
    """Unlock and carry the new token on the client."""
    response = client.post("/api/unlock", json={"password": password})
    assert response.status_code == 200, response.get_data(as_text=True)
    client.environ_base["HTTP_X_JOURNAL_SESSION"] = response.get_json()["token"]
    return client


def _text_on(client, day="2026-08-30"):
    """The written text for a day, or None if the day is blank."""
    body = client.get(f"/api/entry?day={day}").get_json()
    entry = body.get("entry")
    return None if not entry else entry["sections"][0]["text"]




def _write_entry(client, text=SECRET, day="2026-08-30"):
    payload = dict(an_entry(text=text), day=day)
    response = client.put("/api/entry", json=payload)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response


def _backup_bytes(client):
    response = client.get("/api/backup")
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_data()


def _upload(client, data, filename="journal-backup.db"):
    return client.post(
        "/api/restore",
        data={"file": (io.BytesIO(data), filename)},
        content_type="multipart/form-data",
    )


# --- what a backup is ---------------------------------------------------------


def test_a_backup_is_a_sqlite_database(unlocked_client):
    _write_entry(unlocked_client)
    data = _backup_bytes(unlocked_client)
    assert data.startswith(b"SQLite format 3\x00")


def test_a_backup_carries_no_readable_entry_text(unlocked_client, tmp_path):
    """The property that makes this file safe to keep, and the reason it is
    offered alongside the plain-text export rather than instead of it."""
    _write_entry(unlocked_client)
    data = _backup_bytes(unlocked_client)

    assert SECRET.encode() not in data
    assert b"cycling" not in data, "not even a tag"


def test_a_backup_still_contains_the_rows_encrypted(unlocked_client, tmp_path):
    """Unreadable is not the same as empty — the ciphertext has to be in there,
    or the file restores to a journal with nothing in it."""
    _write_entry(unlocked_client)
    path = tmp_path / "backup.db"
    path.write_bytes(_backup_bytes(unlocked_client))

    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute("SELECT day, blob FROM entries").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "2026-08-30"
    assert rows[0][1], "the row is there but the ciphertext is not"


def test_the_download_is_named_for_the_day_it_was_taken(unlocked_client):
    response = unlocked_client.get("/api/backup")
    disposition = response.headers["Content-Disposition"]
    assert "attachment" in disposition
    assert disposition.endswith(f'journal-backup-{journalapp._today().isoformat()}.db"') or \
        f"journal-backup-{journalapp._today().isoformat()}.db" in disposition


def test_a_backup_leaves_no_copy_behind_in_the_temp_directory(unlocked_client, monkeypatch,
                                                              tmp_path):
    """A stray snapshot here is a complete copy of somebody's journal sitting in
    /tmp, which is why the file is read into memory and deleted rather than
    cleaned up in a response callback."""
    monkeypatch.setattr(journalapp.tempfile, "tempdir", str(tmp_path))
    _write_entry(unlocked_client)
    _backup_bytes(unlocked_client)
    assert list(tmp_path.glob("journal-backup-*")) == []


def test_a_locked_journal_will_not_hand_out_a_backup(client):
    """The file is safe on its own, but requiring the password to get it stops
    an ingress-admin who does not know it taking the ciphertext away to attack
    offline."""
    client.post("/api/vault", json={"password": PASSWORD})
    response = client.get("/api/backup")
    assert response.status_code == 401
    assert response.get_json()["error"] == "locked"


# --- the round trip -----------------------------------------------------------


def test_a_restored_journal_reads_back_with_the_same_password(live_unlocked):
    """The one test that matters: words in, file out, file in, same words."""
    _write_entry(live_unlocked)
    data = _backup_bytes(live_unlocked)

    assert _upload(live_unlocked, data).status_code == 200

    _unlock(live_unlocked)
    assert _text_on(live_unlocked, "2026-08-30") == SECRET


def test_restoring_replaces_what_is_there_rather_than_merging(live_unlocked):
    """A merge would need both keys at once and is not what "restore" means; the
    UI offers this as replace, so this pins replace."""
    _write_entry(live_unlocked, text="the day in the backup", day="2026-08-28")
    data = _backup_bytes(live_unlocked)

    _write_entry(live_unlocked, text="written after the backup", day="2026-08-29")
    assert _upload(live_unlocked, data).status_code == 200

    _unlock(live_unlocked)
    assert _text_on(live_unlocked, "2026-08-28") == "the day in the backup"
    assert _text_on(live_unlocked, "2026-08-29") is None, "the later entry survived a restore"


def test_goals_come_back_too(live_unlocked):
    """Entries are the obvious half; a restore that quietly dropped the goals
    would look fine on the day it was done."""
    live_unlocked.post("/api/goals", json={"title": "Ride 2000 km"})
    data = _backup_bytes(live_unlocked)
    assert _upload(live_unlocked, data).status_code == 200

    _unlock(live_unlocked)
    goals = live_unlocked.get("/api/goals").get_json()["goals"]
    assert any(g["title"] == "Ride 2000 km" for g in goals)


# --- restore locks the journal ------------------------------------------------


def test_a_restore_drops_every_open_session(live_unlocked):
    """The key in memory belongs to a vault that no longer exists. Keeping it
    would decrypt the new database with the old journal's key, which fails —
    at best confusingly, and only once somebody opened an entry."""
    _write_entry(live_unlocked)
    data = _backup_bytes(live_unlocked)

    response = _upload(live_unlocked, data)
    assert response.get_json()["locked"] is True
    assert journalapp.SESSIONS.count() == 0
    assert live_unlocked.get("/api/entry?day=2026-08-30").status_code == 401


# --- refusing the wrong file --------------------------------------------------


def test_another_addons_database_is_refused(unlocked_client, tmp_path):
    """Swapping a journal for a database with none of the right tables has no
    undo — the file it overwrote was the only copy on the machine."""
    other = tmp_path / "coop.db"
    conn = sqlite3.connect(str(other))
    conn.execute("CREATE TABLE eggs (id INTEGER PRIMARY KEY, collected_on TEXT)")
    conn.commit()
    conn.close()

    response = _upload(unlocked_client, other.read_bytes(), "coop-tracker-backup.db")
    assert response.status_code == 400
    assert "not a valid Journal backup" in response.get_json()["error"]


def test_a_refused_restore_changes_nothing(unlocked_client, tmp_path):
    _write_entry(unlocked_client)
    other = tmp_path / "other.db"
    conn = sqlite3.connect(str(other))
    conn.execute("CREATE TABLE unrelated (id INTEGER)")
    conn.commit()
    conn.close()

    _upload(unlocked_client, other.read_bytes())
    assert _text_on(unlocked_client) == SECRET, "the journal was disturbed by a rejected file"


def test_a_truncated_upload_is_refused(unlocked_client):
    """Half a database is not a database, and the swap must not happen."""
    _write_entry(unlocked_client)
    truncated = _backup_bytes(unlocked_client)[:200]
    assert _upload(unlocked_client, truncated).status_code == 400
    assert unlocked_client.get("/api/entry?day=2026-08-30").status_code == 200


def test_a_file_that_is_not_a_database_at_all_is_refused(unlocked_client):
    assert _upload(unlocked_client, b"this is a text file").status_code == 400


def test_a_request_with_no_file_is_refused(unlocked_client):
    response = unlocked_client.post("/api/restore", data={},
                                    content_type="multipart/form-data")
    assert response.status_code == 400
    assert "no file" in response.get_json()["error"]


def test_a_leftover_upload_is_not_left_on_disk_after_a_refusal(unlocked_client, db_path):
    """It is written beside the real database, so a rejected one that stayed
    would sit in /data forever."""
    import os

    _upload(unlocked_client, b"not a database")
    assert not os.path.exists(db_path + ".upload")


# --- the lock rule ------------------------------------------------------------


def test_an_existing_journal_must_be_unlocked_before_it_is_replaced(client):
    """Restoring destroys writing that cannot be recovered. The password is the
    proof that it is yours to destroy."""
    client.post("/api/vault", json={"password": PASSWORD})
    response = _upload(client, b"anything at all")
    assert response.status_code == 401
    assert response.get_json()["error"] == "locked"


def test_a_fresh_install_can_restore_without_unlocking_first(live_client, db_path, tmp_path):
    """The case the feature exists for. A new install has no vault, so nothing
    can ever unlock it — requiring a password here would make moving a journal
    to another machine impossible."""
    donor = tmp_path / "donor.db"
    store.init_db(str(donor))
    donor_conn = store.connect(str(donor))
    key = store.create_vault(donor_conn, PASSWORD)
    store.save_entry(donor_conn, key, "2026-08-30", an_entry(text=SECRET))
    donor_conn.commit()
    donor_conn.close()

    assert live_client.get("/api/state").get_json()["vault_exists"] is False, \
        "this test needs an empty vault"
    assert _upload(live_client, donor.read_bytes()).status_code == 200

    _unlock(live_client)
    assert _text_on(live_client, "2026-08-30") == SECRET


def test_neither_route_is_reachable_without_ingress(conn, options, monkeypatch, db_path):
    """Same door as everything else: no ingress header, no answer. These two are
    worth stating outright because between them they take the journal out and
    put a new one in."""
    journalapp.app.config.update(TESTING=True)
    monkeypatch.setattr(journalapp, "DB_PATH", db_path)
    monkeypatch.setattr(journalapp, "get_db", lambda: conn)
    with journalapp.app.test_client() as bare:
        assert bare.get("/api/backup").status_code == 401
        assert bare.post("/api/restore").status_code == 401
