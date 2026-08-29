"""The vault: a master password in, ciphertext on disk, and a key that lives
only in memory.

The shape of it:

- The password is never stored, in any form. A random 16-byte salt is, and the
  key is `scrypt(password, salt)` — recomputed on every unlock and held in RAM
  for as long as the session lasts. Restarting the add-on locks it, because
  the key was never anywhere else.
- Whether a password is right is decided by decrypting a *verifier*: a known
  plaintext encrypted with the real key when the vault was created. AES-GCM
  either authenticates it or it does not, so there is no separate password
  hash to leak and no comparison of our own to get wrong.
- Everything the person wrote — entry text, mood, tags, goal titles, even the
  section headings they chose — is AES-256-GCM inside a blob. The database
  keeps only the skeleton in the clear: which dates have an entry, which goals
  exist and whether they are active. That skeleton is what lets the add-on
  publish a streak sensor and send a reminder while locked.
- Every ciphertext is bound to the row it belongs in through GCM's additional
  authenticated data (`entry:2026-08-29`, `goal:<id>`). Moving a blob from one
  row to another — by hand, in sqlite — makes it fail to authenticate rather
  than quietly appear under the wrong date.

There is no recovery. No backdoor key, no reset, no hint. A forgotten password
is a lost journal, and that is the point of the thing; DOCS.md says so in as
many words, and the first-run screen says it before accepting a password.
"""
import hashlib
import json
import os
import secrets
import threading
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# scrypt at 32 MiB (128 * n * r). On a Pi-class board an unlock takes a
# noticeable fraction of a second, which is the intended trade: it is paid
# once per session by the owner, and once per guess by anyone brute-forcing a
# stolen copy of the database.
DEFAULT_KDF = {"name": "scrypt", "n": 1 << 15, "r": 8, "p": 1, "dklen": 32}
SALT_BYTES = 16
NONCE_BYTES = 12

# What the verifier decrypts to. Its content is not secret and does not need
# to be — only the fact that it authenticates matters.
VERIFIER_PLAINTEXT = b"journal vault v1"
VERIFIER_AAD = "vault:verifier"


class WrongPassword(Exception):
    """The password did not decrypt the verifier."""


class Locked(Exception):
    """No key in memory for this session — unlock first."""


class CorruptRecord(Exception):
    """A blob failed to authenticate under the right key: the row was edited,
    truncated, or moved from somewhere else."""


def new_salt():
    return secrets.token_bytes(SALT_BYTES)


def derive_key(password, salt, kdf=None):
    """scrypt from the standard library. `maxmem` has to be passed explicitly:
    OpenSSL's own default cap sits right at the memory this costs, so leaving
    it out fails at exactly the parameters worth using."""
    params = dict(DEFAULT_KDF if kdf is None else kdf)
    if params.get("name") != "scrypt":
        raise ValueError(f"unsupported kdf: {params.get('name')!r}")
    n, r, p = int(params["n"]), int(params["r"]), int(params["p"])
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=int(params["dklen"]),
        maxmem=(128 * n * r) * 2,
    )


def encrypt(key, payload, aad):
    """A JSON-serialisable payload to nonce||ciphertext||tag."""
    nonce = os.urandom(NONCE_BYTES)
    plaintext = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad.encode("utf-8"))


def decrypt(key, blob, aad):
    if not blob or len(blob) <= NONCE_BYTES:
        raise CorruptRecord("blob too short to hold a nonce and a tag")
    nonce, body = bytes(blob[:NONCE_BYTES]), bytes(blob[NONCE_BYTES:])
    try:
        plaintext = AESGCM(key).decrypt(nonce, body, aad.encode("utf-8"))
    except InvalidTag as exc:
        raise CorruptRecord(f"{aad} did not authenticate") from exc
    return json.loads(plaintext)


def make_verifier(key):
    nonce = os.urandom(NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, VERIFIER_PLAINTEXT, VERIFIER_AAD.encode("utf-8"))


def check_verifier(key, verifier):
    """True when `key` is the key the verifier was made with."""
    if not verifier or len(verifier) <= NONCE_BYTES:
        return False
    nonce, body = bytes(verifier[:NONCE_BYTES]), bytes(verifier[NONCE_BYTES:])
    try:
        return AESGCM(key).decrypt(nonce, body, VERIFIER_AAD.encode("utf-8")) == VERIFIER_PLAINTEXT
    except InvalidTag:
        return False


# --- Unlock throttling ---
#
# scrypt already makes guessing expensive, but it is the *browser* side that
# needs slowing here: someone on the network with the ingress path open should
# not get to try passwords at HTTP speed. Consecutive failures buy an
# increasing cooldown; one success clears it.


class Throttle:
    FREE_ATTEMPTS = 5
    MAX_COOLDOWN_SECONDS = 300

    def __init__(self, now=time.monotonic):
        self._now = now
        self._failures = 0
        self._blocked_until = 0.0
        self._lock = threading.Lock()

    def seconds_remaining(self):
        with self._lock:
            return max(0.0, self._blocked_until - self._now())

    def record_failure(self):
        with self._lock:
            self._failures += 1
            over = self._failures - self.FREE_ATTEMPTS
            if over > 0:
                wait = min(2 ** min(over, 10), self.MAX_COOLDOWN_SECONDS)
                self._blocked_until = self._now() + wait
            return self._failures

    def record_success(self):
        with self._lock:
            self._failures = 0
            self._blocked_until = 0.0


# --- Sessions ---
#
# One unlock opens one session, addressed by a random token in an HttpOnly
# cookie. The key itself never leaves this process. Several browsers can be
# unlocked at once — each derived the same key from the same password — and
# locking from any of them drops all of them, because "lock my journal" means
# the journal, not this tab.


class SessionStore:
    def __init__(self, ttl_seconds=3600, now=time.monotonic):
        self._sessions = {}
        self._ttl = ttl_seconds
        self._now = now
        self._lock = threading.Lock()

    def set_ttl(self, ttl_seconds):
        with self._lock:
            self._ttl = ttl_seconds

    def open(self, key):
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = {"key": key, "touched": self._now()}
        return token

    def key_for(self, token):
        """The session's key, or None if there is no such session or it has
        gone idle for longer than the timeout. Reading it counts as activity:
        the timeout is about someone walking away, not about elapsed time."""
        if not token:
            return None
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if self._ttl and self._now() - session["touched"] > self._ttl:
                del self._sessions[token]
                return None
            session["touched"] = self._now()
            return session["key"]

    def close(self, token):
        with self._lock:
            self._sessions.pop(token, None)

    def close_all(self):
        with self._lock:
            self._sessions.clear()

    def rekey(self, key):
        """After a password change: every open session keeps working, now
        holding the new key. Locking the owner out of their own journal for
        having rotated its password would be an odd way to reward it."""
        with self._lock:
            for session in self._sessions.values():
                session["key"] = key

    def sweep(self):
        """Drop keys that have gone idle past the timeout.

        `key_for` already refuses an expired session, but refusing is not
        forgetting: without this the key would sit in memory until someone
        happened to ask for it again. An auto-lock has to happen when nobody
        is looking, which is the only time it matters.
        """
        if not self._ttl:
            return 0
        with self._lock:
            cutoff = self._now() - self._ttl
            stale = [token for token, s in self._sessions.items() if s["touched"] <= cutoff]
            for token in stale:
                del self._sessions[token]
            return len(stale)

    def count(self):
        with self._lock:
            return len(self._sessions)
