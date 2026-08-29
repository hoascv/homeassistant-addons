import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as journalapp  # noqa: E402
import crypto  # noqa: E402
import store  # noqa: E402

PASSWORD = "correct horse battery"

# Captured at import, before any fixture lowers it, so a test can still assert
# on what actually ships.
SHIPPED_KDF = dict(crypto.DEFAULT_KDF)


@pytest.fixture
def shipped_kdf():
    return SHIPPED_KDF


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    """scrypt at real parameters costs ~0.1s per derivation, and this suite
    derives keys in almost every test. The cost is the point in production and
    noise here, so tests run at a fraction of the work — with one test
    (test_crypto.py) asserting the shipped parameters are the expensive ones."""
    monkeypatch.setattr(crypto, "DEFAULT_KDF", {"name": "scrypt", "n": 1 << 10, "r": 8, "p": 1, "dklen": 32})


@pytest.fixture
def fast_key(fast_kdf):
    """A key, without the ceremony of a database — for testing the primitives
    themselves."""
    return crypto.derive_key(PASSWORD, crypto.new_salt(), crypto.DEFAULT_KDF)


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "journal.db")
    store.init_db(path)
    return path


@pytest.fixture
def conn(db_path):
    connection = store.connect(db_path)
    yield connection
    connection.close()


@pytest.fixture
def key(conn):
    """A vault with a password already set, and the key to it."""
    return store.create_vault(conn, PASSWORD)


@pytest.fixture
def options(monkeypatch, tmp_path):
    """Write add-on options the way Supervisor does, and point the app at them."""
    path = tmp_path / "options.json"
    monkeypatch.setattr(journalapp, "OPTIONS_PATH", str(path))

    def _write(**values):
        path.write_text(json.dumps(values))
        return values

    _write()
    return _write


@pytest.fixture
def client(conn, db_path, options, monkeypatch):
    """A Flask test client past the ingress door but still locked."""
    journalapp.app.config.update(TESTING=True)
    monkeypatch.setattr(journalapp, "DB_PATH", db_path)
    monkeypatch.setattr(journalapp, "get_db", lambda: conn)
    journalapp.SESSIONS.close_all()
    journalapp.UNLOCK_THROTTLE.record_success()
    with journalapp.app.test_client() as test_client:
        test_client.environ_base["HTTP_X_REMOTE_USER_ID"] = "test-user"
        yield test_client


@pytest.fixture
def unlocked_client(client, conn):
    """The same client, past the password too."""
    client.post("/api/vault", json={"password": PASSWORD})
    res = client.post("/api/unlock", json={"password": PASSWORD})
    client.environ_base["HTTP_X_JOURNAL_SESSION"] = res.get_json()["token"]
    return client


def an_entry(text="rode the bike to the coast", mood=4, tags=("cycling",), goals=()):
    return {
        "sections": [{"key": "did", "title": "What I did", "text": text}],
        "mood": mood,
        "tags": list(tags),
        "goals": [dict(g) for g in goals],
    }
