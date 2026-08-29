"""Schema and domain logic: entries, goals, the section template, and the
statistics the app and Home Assistant read.

Two rules run through all of it.

*What is in the clear.* Only the skeleton: which dates have an entry, which
goals exist, whether a goal is active, and when rows were written. Everything
authored — prose, mood, tags, goal titles, the section headings — is inside an
encrypted blob. The skeleton is what lets a locked add-on still know it is on
a fourteen-day streak and send a reminder that gives nothing away.

*Where the key is needed.* Any function that takes a `key` decrypts, and can
only run while a session is unlocked. Functions that do not take one work
while locked, and are the only things the background loop is allowed to call.
"""
import json
import sqlite3
import uuid
from datetime import date, datetime, timedelta

import crypto

# The template a fresh vault starts with. Four sections that cover a day
# without turning it into a form: what happened, what you made of it, one good
# thing, and what is next. All of it is editable in settings, and every entry
# stores the heading it was written under, so renaming a section later does
# not rewrite the past.
DEFAULT_SECTIONS = [
    {"key": "did", "title": "What I did", "hint": "The facts of the day — where you were, who with, what got done."},
    {"key": "thought", "title": "What I was thinking", "hint": "What was on your mind, and why it was there."},
    {"key": "grateful", "title": "Grateful for", "hint": "One thing, however small."},
    {"key": "tomorrow", "title": "Tomorrow", "hint": "The one thing that matters most next."},
]

SETTINGS_SECTIONS_KEY = "sections"

GOAL_STATUSES = ("active", "done", "dropped")

SCHEMA = (
    # One row, id 1. The salt and KDF parameters needed to turn a password
    # back into the key, and the verifier that says whether it worked.
    """
    CREATE TABLE IF NOT EXISTS vault (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        salt BLOB NOT NULL,
        kdf TEXT NOT NULL,
        verifier BLOB NOT NULL,
        created_at TEXT NOT NULL,
        password_changed_at TEXT
    )
    """,
    # Keyed by the day itself: one entry per calendar day, which is what makes
    # "go back to 3 March" a primary-key lookup rather than a search.
    """
    CREATE TABLE IF NOT EXISTS entries (
        day TEXT PRIMARY KEY,
        blob BLOB NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    # A text id, not an autoincrement one, so the id exists before the blob is
    # encrypted — the ciphertext is bound to it as additional authenticated
    # data, and that cannot be done to a row number the database has yet to
    # hand out.
    """
    CREATE TABLE IF NOT EXISTS goals (
        id TEXT PRIMARY KEY,
        blob BLOB NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        position INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        closed_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        blob BLOB NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_state (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_entries_day ON entries (day)",
    "CREATE INDEX IF NOT EXISTS idx_goals_status ON goals (status, position)",
)


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path):
    import os

    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = connect(path)
    try:
        for statement in SCHEMA:
            conn.execute(statement)
        conn.commit()
    finally:
        conn.close()


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def get_app_state(conn, key, default=None):
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_app_state(conn, key, value):
    conn.execute(
        "INSERT INTO app_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# --- The vault ---


def vault_row(conn):
    return conn.execute("SELECT * FROM vault WHERE id = 1").fetchone()


def vault_exists(conn):
    return vault_row(conn) is not None


def create_vault(conn, password):
    """First run. Derives the key, stores salt + verifier, and writes the
    default section template — the one thing a brand-new vault needs
    encrypted, and the only moment the key is in hand to do it."""
    if vault_exists(conn):
        raise ValueError("this journal already has a master password")
    if not password or len(password) < 8:
        raise ValueError("the master password must be at least 8 characters")
    salt = crypto.new_salt()
    key = crypto.derive_key(password, salt, crypto.DEFAULT_KDF)
    conn.execute(
        "INSERT INTO vault (id, salt, kdf, verifier, created_at) VALUES (1, ?, ?, ?, ?)",
        (salt, json.dumps(crypto.DEFAULT_KDF), crypto.make_verifier(key), now_iso()),
    )
    save_sections(conn, key, DEFAULT_SECTIONS)
    conn.commit()
    return key


def unlock_key(conn, password):
    """The key for this password, or WrongPassword. Nothing here compares the
    password with anything: either it reproduces the key that authenticates
    the verifier, or it does not."""
    row = vault_row(conn)
    if row is None:
        raise ValueError("no master password has been set yet")
    key = crypto.derive_key(password, row["salt"], json.loads(row["kdf"]))
    if not crypto.check_verifier(key, row["verifier"]):
        raise crypto.WrongPassword("wrong master password")
    return key


def change_password(conn, old_password, new_password):
    """Re-key the whole journal in one transaction. Every blob is decrypted
    with the old key and re-encrypted with the new one; a failure anywhere
    rolls back to the old password rather than leaving half a journal
    readable by neither."""
    if not new_password or len(new_password) < 8:
        raise ValueError("the master password must be at least 8 characters")
    old_key = unlock_key(conn, old_password)
    new_salt = crypto.new_salt()
    new_key = crypto.derive_key(new_password, new_salt, crypto.DEFAULT_KDF)

    try:
        conn.execute("BEGIN")
        for entry in conn.execute("SELECT day, blob FROM entries").fetchall():
            aad = _entry_aad(entry["day"])
            payload = crypto.decrypt(old_key, entry["blob"], aad)
            conn.execute(
                "UPDATE entries SET blob = ? WHERE day = ?",
                (crypto.encrypt(new_key, payload, aad), entry["day"]),
            )
        for goal in conn.execute("SELECT id, blob FROM goals").fetchall():
            aad = _goal_aad(goal["id"])
            payload = crypto.decrypt(old_key, goal["blob"], aad)
            conn.execute("UPDATE goals SET blob = ? WHERE id = ?", (crypto.encrypt(new_key, payload, aad), goal["id"]))
        for setting in conn.execute("SELECT key, blob FROM settings").fetchall():
            aad = _settings_aad(setting["key"])
            payload = crypto.decrypt(old_key, setting["blob"], aad)
            conn.execute("UPDATE settings SET blob = ? WHERE key = ?", (crypto.encrypt(new_key, payload, aad), setting["key"]))
        conn.execute(
            "UPDATE vault SET salt = ?, kdf = ?, verifier = ?, password_changed_at = ? WHERE id = 1",
            (new_salt, json.dumps(crypto.DEFAULT_KDF), crypto.make_verifier(new_key), now_iso()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return new_key


def _entry_aad(day):
    return f"entry:{day}"


def _goal_aad(goal_id):
    return f"goal:{goal_id}"


def _settings_aad(key):
    return f"settings:{key}"


# --- The section template ---


def get_sections(conn, key):
    row = conn.execute("SELECT blob FROM settings WHERE key = ?", (SETTINGS_SECTIONS_KEY,)).fetchone()
    if row is None:
        return [dict(section) for section in DEFAULT_SECTIONS]
    return crypto.decrypt(key, row["blob"], _settings_aad(SETTINGS_SECTIONS_KEY))["sections"]


def save_sections(conn, key, sections):
    cleaned = normalise_sections(sections)
    if not cleaned:
        raise ValueError("a journal needs at least one section")
    blob = crypto.encrypt(key, {"sections": cleaned}, _settings_aad(SETTINGS_SECTIONS_KEY))
    conn.execute(
        "INSERT INTO settings (key, blob) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET blob = excluded.blob",
        (SETTINGS_SECTIONS_KEY, blob),
    )
    conn.commit()
    return cleaned


def slugify(title, taken=()):
    base = "".join(c if c.isalnum() else "-" for c in str(title).lower()).strip("-")
    base = "-".join(part for part in base.split("-") if part)[:32] or "section"
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def normalise_sections(sections):
    """Titles are what the person sees; keys are what entries are stored
    under. A section keeps its key for life, so renaming "Grateful for" to
    "Wins" leaves every past entry still attached to the same section."""
    cleaned, taken = [], set()
    for section in sections or []:
        title = str(section.get("title", "")).strip()
        if not title:
            continue
        key = str(section.get("key", "")).strip() or slugify(title, taken)
        if key in taken:
            continue
        taken.add(key)
        cleaned.append({"key": key, "title": title[:80], "hint": str(section.get("hint", "")).strip()[:200]})
    return cleaned


# --- Entries ---


def normalise_entry(payload):
    """Everything the client may send for a day, reduced to what is worth
    keeping. Sections with nothing written in them are dropped rather than
    stored empty — an entry should be what was said, not the shape of the
    form it was said in."""
    sections = []
    for section in payload.get("sections") or []:
        text = str(section.get("text", "")).strip()
        if not text:
            continue
        sections.append(
            {
                "key": str(section.get("key", "")).strip() or slugify(section.get("title", "")),
                # The heading is snapshotted with the text. Renaming a section
                # tomorrow must not retitle what was written under it today.
                "title": str(section.get("title", "")).strip()[:80],
                "text": text,
            }
        )

    mood = payload.get("mood")
    try:
        mood = int(mood)
        mood = mood if 1 <= mood <= 5 else None
    except (TypeError, ValueError):
        mood = None

    tags, seen = [], set()
    for tag in payload.get("tags") or []:
        cleaned = str(tag).strip().lstrip("#").lower()[:40]
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            tags.append(cleaned)

    checkins = []
    for checkin in payload.get("goals") or []:
        goal_id = str(checkin.get("id", "")).strip()
        note = str(checkin.get("note", "")).strip()
        moved = bool(checkin.get("moved"))
        if goal_id and (note or moved):
            checkins.append({"id": goal_id, "note": note, "moved": moved})

    return {"sections": sections, "mood": mood, "tags": tags, "goals": checkins}


def entry_is_empty(payload):
    return not (payload["sections"] or payload["mood"] or payload["tags"] or payload["goals"])


def save_entry(conn, key, day, payload):
    """Write (or replace) a day. An entry emptied of everything is deleted, so
    that opening a past day to read it and closing it again cannot silently
    add a blank to the streak."""
    day = normalise_day(day)
    clean = normalise_entry(payload)
    if entry_is_empty(clean):
        conn.execute("DELETE FROM entries WHERE day = ?", (day,))
        conn.commit()
        return None
    blob = crypto.encrypt(key, clean, _entry_aad(day))
    now = now_iso()
    conn.execute(
        "INSERT INTO entries (day, blob, created_at, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(day) DO UPDATE SET blob = excluded.blob, updated_at = excluded.updated_at",
        (day, blob, now, now),
    )
    conn.commit()
    return clean


def get_entry(conn, key, day):
    day = normalise_day(day)
    row = conn.execute("SELECT * FROM entries WHERE day = ?", (day,)).fetchone()
    if row is None:
        return None
    payload = crypto.decrypt(key, row["blob"], _entry_aad(day))
    payload["day"] = day
    payload["created_at"] = row["created_at"]
    payload["updated_at"] = row["updated_at"]
    return payload


def delete_entry(conn, day):
    conn.execute("DELETE FROM entries WHERE day = ?", (normalise_day(day),))
    conn.commit()


def normalise_day(day):
    """Accepts a date, a datetime or an ISO string, and insists on a real
    calendar day — a bad date must fail here rather than become a row nothing
    will ever look up again."""
    if isinstance(day, datetime):
        return day.date().isoformat()
    if isinstance(day, date):
        return day.isoformat()
    text = str(day).strip()[:10]
    return date.fromisoformat(text).isoformat()


def entry_days(conn, start=None, end=None):
    """Which days have an entry. Metadata only — no key needed, which is what
    lets the streak sensor keep working while the journal is locked."""
    sql = "SELECT day FROM entries"
    params = []
    if start and end:
        sql += " WHERE day BETWEEN ? AND ?"
        params = [normalise_day(start), normalise_day(end)]
    elif start:
        sql += " WHERE day >= ?"
        params = [normalise_day(start)]
    elif end:
        sql += " WHERE day <= ?"
        params = [normalise_day(end)]
    sql += " ORDER BY day"
    return [row["day"] for row in conn.execute(sql, params).fetchall()]


def calendar(conn, key, start, end):
    """The days in a range with enough of each to draw a strip: mood and how
    much was written. Decrypts, so it is an unlocked-only view."""
    out = []
    rows = conn.execute(
        "SELECT day, blob FROM entries WHERE day BETWEEN ? AND ? ORDER BY day",
        (normalise_day(start), normalise_day(end)),
    ).fetchall()
    for row in rows:
        payload = crypto.decrypt(key, row["blob"], _entry_aad(row["day"]))
        out.append(
            {
                "day": row["day"],
                "mood": payload.get("mood"),
                "words": sum(len(section["text"].split()) for section in payload.get("sections", [])),
                "tags": payload.get("tags", []),
            }
        )
    return out


def _snippet(text, needle, width=90):
    lowered = text.lower()
    at = lowered.find(needle)
    if at < 0:
        return text[:width] + ("…" if len(text) > width else "")
    start = max(0, at - width // 3)
    end = min(len(text), at + len(needle) + width)
    return ("…" if start else "") + text[start:end].strip() + ("…" if end < len(text) else "")


def search(conn, key, query, limit=50):
    """Plain substring search over every entry.

    It decrypts each one in turn, which is the honest cost of a journal the
    database cannot read: there is no index to build over ciphertext without
    leaking what is in it. A decade of daily entries is a few thousand rows of
    a few hundred bytes, so this stays well under a blink.
    """
    needle = str(query or "").strip().lower()
    if not needle:
        return []
    results = []
    for row in conn.execute("SELECT day, blob FROM entries ORDER BY day DESC").fetchall():
        payload = crypto.decrypt(key, row["blob"], _entry_aad(row["day"]))
        hit = None
        for section in payload.get("sections", []):
            if needle in section["text"].lower() or needle in section["title"].lower():
                hit = {"section": section["title"], "snippet": _snippet(section["text"], needle)}
                break
        if hit is None and any(needle in tag for tag in payload.get("tags", [])):
            hit = {"section": "Tags", "snippet": " ".join("#" + tag for tag in payload["tags"])}
        if hit is None:
            for checkin in payload.get("goals", []):
                if needle in checkin.get("note", "").lower():
                    hit = {"section": "Goal check-in", "snippet": _snippet(checkin["note"], needle)}
                    break
        if hit:
            results.append({"day": row["day"], "mood": payload.get("mood"), **hit})
        if len(results) >= limit:
            break
    return results


def on_this_day(conn, key, day, max_years=10):
    """The same date in earlier years — the reason to keep a journal at all."""
    day = normalise_day(day)
    target = date.fromisoformat(day)
    out = []
    for years in range(1, max_years + 1):
        try:
            past = target.replace(year=target.year - years)
        except ValueError:
            # 29 February in a year that has no 29 February.
            continue
        entry = get_entry(conn, key, past)
        if entry:
            out.append({"years_ago": years, **entry})
    return out


# --- Goals ---


def create_goal(conn, key, title, why="", target_date=None):
    title = str(title or "").strip()
    if not title:
        raise ValueError("a goal needs a title")
    goal_id = uuid.uuid4().hex
    payload = {"title": title[:120], "why": str(why or "").strip()[:500], "target_date": _clean_target(target_date)}
    position = (conn.execute("SELECT COALESCE(MAX(position), 0) FROM goals").fetchone()[0] or 0) + 1
    now = now_iso()
    conn.execute(
        "INSERT INTO goals (id, blob, status, position, created_at, updated_at) VALUES (?, ?, 'active', ?, ?, ?)",
        (goal_id, crypto.encrypt(key, payload, _goal_aad(goal_id)), position, now, now),
    )
    conn.commit()
    return goal_id


def _clean_target(target_date):
    if not target_date:
        return None
    try:
        return normalise_day(target_date)
    except ValueError:
        return None


def update_goal(conn, key, goal_id, title=None, why=None, target_date=None, status=None):
    row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if row is None:
        raise ValueError("no such goal")
    payload = crypto.decrypt(key, row["blob"], _goal_aad(goal_id))
    if title is not None:
        cleaned = str(title).strip()
        if not cleaned:
            raise ValueError("a goal needs a title")
        payload["title"] = cleaned[:120]
    if why is not None:
        payload["why"] = str(why).strip()[:500]
    if target_date is not None:
        payload["target_date"] = _clean_target(target_date)

    new_status = row["status"]
    if status is not None:
        if status not in GOAL_STATUSES:
            raise ValueError(f"status must be one of {', '.join(GOAL_STATUSES)}")
        new_status = status
    closed_at = row["closed_at"]
    if new_status != row["status"]:
        closed_at = now_iso() if new_status != "active" else None

    conn.execute(
        "UPDATE goals SET blob = ?, status = ?, closed_at = ?, updated_at = ? WHERE id = ?",
        (crypto.encrypt(key, payload, _goal_aad(goal_id)), new_status, closed_at, now_iso(), goal_id),
    )
    conn.commit()
    return goal_id


def delete_goal(conn, goal_id):
    """Deletes the goal itself. Check-ins written against it stay inside the
    entries that mention them — those are what someone wrote on the day, and
    dropping a goal is not a reason to rewrite their diary."""
    conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
    conn.commit()


def list_goals(conn, key, status=None, today=None):
    today = today or date.today()
    sql = "SELECT * FROM goals"
    params = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY position"
    goals = []
    for row in conn.execute(sql, params).fetchall():
        payload = crypto.decrypt(key, row["blob"], _goal_aad(row["id"]))
        goals.append(
            {
                "id": row["id"],
                "title": payload.get("title", ""),
                "why": payload.get("why", ""),
                "target_date": payload.get("target_date"),
                "status": row["status"],
                "created_at": row["created_at"],
                "closed_at": row["closed_at"],
                "days_left": _days_left(payload.get("target_date"), today),
            }
        )
    return goals


def _days_left(target_date, today):
    if not target_date:
        return None
    return (date.fromisoformat(target_date) - today).days


def goal_activity(conn, key, since=None):
    """Every goal check-in ever written, grouped by goal id.

    Check-ins live inside the day they were written, not in a table of their
    own. A separate table would be an index of which goal you touched on which
    day sitting in the clear next to the encrypted words — the shape of your
    life without the content, which is more than this add-on should hold.
    """
    sql = "SELECT day, blob FROM entries"
    params = []
    if since:
        sql += " WHERE day >= ?"
        params.append(normalise_day(since))
    sql += " ORDER BY day DESC"
    activity = {}
    for row in conn.execute(sql, params).fetchall():
        payload = crypto.decrypt(key, row["blob"], _entry_aad(row["day"]))
        for checkin in payload.get("goals", []):
            activity.setdefault(checkin["id"], []).append(
                {"day": row["day"], "note": checkin.get("note", ""), "moved": bool(checkin.get("moved"))}
            )
    return activity


def goals_with_activity(conn, key, today=None, nudge_days=7):
    """Active goals, each with its last check-in and how long ago that was —
    the view the app opens on, and the one that says which goal has gone
    quiet."""
    today = today or date.today()
    activity = goal_activity(conn, key)
    out = []
    for goal in list_goals(conn, key, today=today):
        checkins = activity.get(goal["id"], [])
        last = checkins[0] if checkins else None
        days_since = (today - date.fromisoformat(last["day"])).days if last else None
        out.append(
            {
                **goal,
                "checkins": len(checkins),
                "moved_count": sum(1 for c in checkins if c["moved"]),
                "last_checkin": last,
                "days_since_checkin": days_since,
                "needs_attention": bool(
                    goal["status"] == "active"
                    and nudge_days
                    and (days_since is None or days_since >= nudge_days)
                ),
            }
        )
    return out


def goal_timeline(conn, key, goal_id, limit=100):
    return goal_activity(conn, key).get(goal_id, [])[:limit]


# --- Statistics ---


def streak(conn, today=None):
    """Consecutive days ending today — or ending yesterday, while today is
    still unwritten. A streak that breaks at breakfast because the day is not
    over yet would be a lie about the day before."""
    today = today or date.today()
    days = set(entry_days(conn))
    if not days:
        return 0
    cursor = today if today.isoformat() in days else today - timedelta(days=1)
    count = 0
    while cursor.isoformat() in days:
        count += 1
        cursor -= timedelta(days=1)
    return count


def longest_streak(conn):
    days = sorted(date.fromisoformat(d) for d in entry_days(conn))
    best = run = 0
    previous = None
    for day in days:
        run = run + 1 if previous is not None and (day - previous).days == 1 else 1
        best = max(best, run)
        previous = day
    return best


def stats(conn, today=None):
    """Counts only — no key, so this is what the sensor and the reminder see."""
    today = today or date.today()
    days = entry_days(conn)
    goals = conn.execute("SELECT status, COUNT(*) AS n FROM goals GROUP BY status").fetchall()
    by_status = {row["status"]: row["n"] for row in goals}
    return {
        "entries": len(days),
        "first_entry_on": days[0] if days else None,
        "last_entry_on": days[-1] if days else None,
        "has_entry_today": today.isoformat() in set(days),
        "streak": streak(conn, today),
        "longest_streak": longest_streak(conn),
        "goals_active": by_status.get("active", 0),
        "goals_done": by_status.get("done", 0),
    }


def export_all(conn, key):
    """Everything, decrypted, as plain JSON. The database is the backup — this
    is for reading the journal somewhere that is not this add-on, and it comes
    out of the machine in the clear, which the UI says out loud before it
    downloads."""
    return {
        "exported_at": now_iso(),
        "sections": get_sections(conn, key),
        "goals": list_goals(conn, key),
        "entries": [get_entry(conn, key, day) for day in entry_days(conn)],
    }
