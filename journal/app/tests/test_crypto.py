"""The vault primitives. If any of these give, the add-on's one promise does."""
import pytest

import crypto


def test_the_shipped_kdf_is_the_expensive_one(shipped_kdf):
    """The suite runs at a fraction of the work (see conftest). This is the
    guard that the *shipped* parameters were not lowered to make tests quick:
    2**15 is 32 MiB of scrypt per guess, which is the whole defence for a
    database someone has walked off with."""
    assert shipped_kdf["name"] == "scrypt"
    assert shipped_kdf["n"] >= 1 << 14
    assert shipped_kdf["dklen"] == 32


def test_the_real_parameters_actually_derive(shipped_kdf):
    """Paid once, so the expensive path is covered too: OpenSSL's own memory
    cap sits exactly at these parameters, and getting `maxmem` wrong would
    fail only in production."""
    key = crypto.derive_key("a real password", crypto.new_salt(), shipped_kdf)
    assert len(key) == 32


def test_the_same_password_and_salt_give_the_same_key():
    salt = crypto.new_salt()
    kdf = {"name": "scrypt", "n": 1 << 10, "r": 8, "p": 1, "dklen": 32}
    assert crypto.derive_key("hunter2 hunter2", salt, kdf) == crypto.derive_key("hunter2 hunter2", salt, kdf)


def test_a_different_salt_gives_a_different_key():
    """Two journals with the same password must not share a key — the salt is
    what stops one precomputed table opening both."""
    kdf = {"name": "scrypt", "n": 1 << 10, "r": 8, "p": 1, "dklen": 32}
    password = "hunter2 hunter2"
    assert crypto.derive_key(password, crypto.new_salt(), kdf) != crypto.derive_key(password, crypto.new_salt(), kdf)


def test_a_round_trip_returns_the_payload(fast_key):
    payload = {"sections": [{"key": "did", "title": "What I did", "text": "walked the dog"}], "mood": 4}
    blob = crypto.encrypt(fast_key, payload, "entry:2026-08-29")
    assert crypto.decrypt(fast_key, blob, "entry:2026-08-29") == payload


def test_the_ciphertext_does_not_contain_the_plaintext(fast_key):
    blob = crypto.encrypt(fast_key, {"text": "a very distinctive phrase"}, "entry:2026-08-29")
    assert b"distinctive" not in blob


def test_encrypting_twice_gives_different_ciphertext(fast_key):
    """A fresh nonce every time. Identical blobs for identical days would say
    'nothing happened today, again' to anyone holding the file."""
    payload = {"text": "same words"}
    first = crypto.encrypt(fast_key, payload, "entry:2026-08-29")
    second = crypto.encrypt(fast_key, payload, "entry:2026-08-29")
    assert first != second
    assert crypto.decrypt(fast_key, first, "entry:2026-08-29") == crypto.decrypt(fast_key, second, "entry:2026-08-29")


def test_a_blob_will_not_decrypt_under_another_row(fast_key):
    """The binding that stops someone moving yesterday's ciphertext onto
    today's row in sqlite and having it read back as today."""
    blob = crypto.encrypt(fast_key, {"text": "yesterday"}, "entry:2026-08-28")
    with pytest.raises(crypto.CorruptRecord):
        crypto.decrypt(fast_key, blob, "entry:2026-08-29")


def test_a_tampered_blob_is_refused_not_returned(fast_key):
    blob = bytearray(crypto.encrypt(fast_key, {"text": "unaltered"}, "entry:2026-08-29"))
    blob[-1] ^= 0x01
    with pytest.raises(crypto.CorruptRecord):
        crypto.decrypt(fast_key, bytes(blob), "entry:2026-08-29")


def test_a_truncated_blob_is_refused(fast_key):
    with pytest.raises(crypto.CorruptRecord):
        crypto.decrypt(fast_key, b"\x00" * 4, "entry:2026-08-29")


def test_the_verifier_accepts_only_its_own_key(fast_key):
    other = crypto.derive_key("a different password", crypto.new_salt(), crypto.DEFAULT_KDF)
    verifier = crypto.make_verifier(fast_key)
    assert crypto.check_verifier(fast_key, verifier)
    assert not crypto.check_verifier(other, verifier)


def test_a_missing_verifier_is_false_rather_than_an_exception(fast_key):
    assert not crypto.check_verifier(fast_key, b"")


# --- Throttling ---


def test_the_first_few_wrong_guesses_are_free():
    throttle = crypto.Throttle()
    for _ in range(crypto.Throttle.FREE_ATTEMPTS):
        throttle.record_failure()
    assert throttle.seconds_remaining() == 0


def test_guesses_past_that_start_costing_time():
    clock = [1000.0]
    throttle = crypto.Throttle(now=lambda: clock[0])
    for _ in range(crypto.Throttle.FREE_ATTEMPTS + 3):
        throttle.record_failure()
    assert throttle.seconds_remaining() > 0


def test_the_cooldown_is_capped():
    """Locked out for an hour by a fat-fingered phone would make the throttle
    the thing that loses you the journal."""
    clock = [1000.0]
    throttle = crypto.Throttle(now=lambda: clock[0])
    for _ in range(50):
        throttle.record_failure()
    assert throttle.seconds_remaining() <= crypto.Throttle.MAX_COOLDOWN_SECONDS


def test_one_success_clears_the_penalty():
    clock = [1000.0]
    throttle = crypto.Throttle(now=lambda: clock[0])
    for _ in range(20):
        throttle.record_failure()
    throttle.record_success()
    assert throttle.seconds_remaining() == 0


# --- Sessions ---


def test_a_session_holds_the_key_until_it_is_closed(fast_key):
    sessions = crypto.SessionStore(ttl_seconds=0)
    token = sessions.open(fast_key)
    assert sessions.key_for(token) == fast_key
    sessions.close(token)
    assert sessions.key_for(token) is None


def test_an_unknown_token_gets_nothing(fast_key):
    sessions = crypto.SessionStore()
    sessions.open(fast_key)
    assert sessions.key_for("not-a-real-token") is None
    assert sessions.key_for("") is None


def test_a_session_expires_once_idle(fast_key):
    clock = [0.0]
    sessions = crypto.SessionStore(ttl_seconds=60, now=lambda: clock[0])
    token = sessions.open(fast_key)
    clock[0] = 61
    assert sessions.key_for(token) is None


def test_using_a_session_keeps_it_alive(fast_key):
    """The timeout is about walking away, not about how long ago you sat
    down — a long entry must not lock under the writer's hands."""
    clock = [0.0]
    sessions = crypto.SessionStore(ttl_seconds=60, now=lambda: clock[0])
    token = sessions.open(fast_key)
    for _ in range(5):
        clock[0] += 50
        assert sessions.key_for(token) == fast_key


def test_a_ttl_of_zero_never_expires(fast_key):
    clock = [0.0]
    sessions = crypto.SessionStore(ttl_seconds=0, now=lambda: clock[0])
    token = sessions.open(fast_key)
    clock[0] = 10 ** 9
    assert sessions.key_for(token) == fast_key


def test_sweeping_forgets_idle_keys_without_anyone_asking(fast_key):
    """key_for refuses an expired session, but refusing is not forgetting: the
    key would sit in memory until the next request. Auto-lock has to happen
    while nobody is looking."""
    clock = [0.0]
    sessions = crypto.SessionStore(ttl_seconds=60, now=lambda: clock[0])
    sessions.open(fast_key)
    clock[0] = 61
    assert sessions.sweep() == 1
    assert sessions.count() == 0


def test_sweeping_leaves_live_sessions_alone(fast_key):
    clock = [0.0]
    sessions = crypto.SessionStore(ttl_seconds=60, now=lambda: clock[0])
    sessions.open(fast_key)
    clock[0] = 30
    assert sessions.sweep() == 0
    assert sessions.count() == 1


def test_locking_closes_every_session(fast_key):
    sessions = crypto.SessionStore()
    tokens = [sessions.open(fast_key) for _ in range(3)]
    sessions.close_all()
    assert all(sessions.key_for(t) is None for t in tokens)


def test_rekeying_keeps_open_sessions_working(fast_key):
    sessions = crypto.SessionStore()
    token = sessions.open(fast_key)
    new_key = crypto.derive_key("a new password", crypto.new_salt(), crypto.DEFAULT_KDF)
    sessions.rekey(new_key)
    assert sessions.key_for(token) == new_key
