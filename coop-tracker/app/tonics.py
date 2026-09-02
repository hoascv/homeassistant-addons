"""Herbal tonics: what the flock gets, how often, and when it is due.

Keepers give their birds things beyond feed — garlic in the water, dried
oregano in with the pellets, cider vinegar a couple of days a week. It is
ordinary husbandry, and it is exactly the sort of thing that gets forgotten,
because unlike collecting eggs nothing reminds you and unlike stirring a ferment
nothing goes mouldy if you miss it. It just quietly stops happening.

**On what these are.** They are supplements, not medicine. The evidence for most
of them is thin — garlic and oregano have some support in poultry studies, cider
vinegar much less — and none of them treats a sick bird. A bird that is unwell
needs a vet, and this module deliberately says so on the card rather than
letting a tidy schedule of tonics imply the flock's health is handled.

Kept out of app.py, like ferment.py, for the same reason: it is already long
enough, and a schedule with a state machine belongs somewhere it can be read
whole.
"""
import datetime

# A week is the usual rhythm for most of these, and it is the cadence a
# household actually keeps: "Sunday is garlic day" survives, "every 3.5 days"
# does not.
DEFAULT_CADENCE_DAYS = 7

# How late a routine has to be before the card calls it overdue rather than
# due. A tonic missed by a day is not an event; missed by a week it has stopped
# being a routine.
OVERDUE_AFTER_DAYS = 3


SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS tonic_routines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        -- How much, in words. Free text rather than a number and a unit: "one
        -- crushed clove per litre of water" is the useful form and no schema
        -- would improve on it.
        dose TEXT,
        cadence_days INTEGER NOT NULL DEFAULT 7,
        -- What it is for and what to watch out for. Shipped filled in for the
        -- seeded ones, because the caution is the part worth having.
        notes TEXT,
        -- Kept rather than deleted when switched off, so a routine paused over
        -- winter comes back with its history intact.
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tonic_doses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        routine_id INTEGER NOT NULL REFERENCES tonic_routines(id) ON DELETE CASCADE,
        given_at TEXT NOT NULL,
        notes TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tonic_doses ON tonic_doses(routine_id, given_at)",
)


# Shipped so the feature is useful before anyone has typed anything, with the
# amounts keepers actually use. Every claim here is deliberately modest: these
# are common practice, not established treatment.
SEEDS = (
    {
        "name": "Garlic in the water",
        "dose": "1 crushed clove per litre. Leave 4 hours, then swap for fresh water.",
        "cadence_days": 7,
        "notes": "The most commonly used of these and the one with the most support "
                 "behind it. More is not better: garlic is an allium, and alliums in "
                 "quantity cause anaemia in birds. A clove per litre once a week is "
                 "the usual amount and there is no reason to exceed it.",
    },
    {
        "name": "Oregano and thyme",
        "dose": "A tablespoon of dried herbs per kg of feed, or a fresh handful in the run.",
        "cadence_days": 7,
        "notes": "Used for respiratory condition. Oregano has some real support in "
                 "poultry work; thyme rather less. Harmless either way, and the birds "
                 "pick through fresh sprigs happily.",
    },
    {
        "name": "Cider vinegar in the water",
        "dose": "20 ml (about a tablespoon) per litre, for two or three days.",
        "cadence_days": 14,
        "notes": "NEVER in a galvanised metal drinker — the acid leaches zinc out of "
                 "the coating and that genuinely poisons birds. Plastic or ceramic "
                 "only. The gut-health claims are thin; the zinc risk is not.",
    },
    {
        "name": "Fresh greens",
        "dose": "A generous handful: nettle tops (wilted), dandelion, kale, chickweed.",
        "cadence_days": 3,
        "notes": "The least exotic and probably the most useful. Nettles must be "
                 "wilted or dried first, never fed fresh and stinging.",
    },
)


def create_schema(conn):
    for statement in SCHEMA:
        conn.execute(statement)


def seed_if_empty(conn, now=None):
    """Put the shipped routines in on first use only.

    Only when the table is completely empty, so a keeper who deleted the ones
    they do not want does not find them back after a restart — which is a worse
    failure than shipping nothing, because it looks like the app fighting you.
    """
    if conn.execute("SELECT COUNT(*) AS n FROM tonic_routines").fetchone()["n"]:
        return 0
    for seed in SEEDS:
        conn.execute(
            "INSERT INTO tonic_routines (name, dose, cadence_days, notes, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (seed["name"], seed["dose"], seed["cadence_days"], seed["notes"],
             _now_iso(now)),
        )
    return len(SEEDS)


def _now_iso(now=None):
    return (now or datetime.datetime.now()).isoformat(timespec="seconds")


def _parse(value):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def add_routine(conn, name, dose=None, cadence_days=DEFAULT_CADENCE_DAYS,
                notes=None, now=None):
    name = (name or "").strip()
    if not name:
        raise ValueError("a routine needs a name")
    cadence = max(1, min(365, int(cadence_days or DEFAULT_CADENCE_DAYS)))
    cursor = conn.execute(
        "INSERT INTO tonic_routines (name, dose, cadence_days, notes, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (name[:120], (dose or "").strip()[:300] or None, cadence,
         (notes or "").strip()[:1000] or None, _now_iso(now)),
    )
    return cursor.lastrowid


def set_active(conn, routine_id, active):
    """Pause or resume. Kept rather than deleted, so a routine paused over
    winter comes back with its history."""
    if conn.execute("SELECT id FROM tonic_routines WHERE id = ?",
                    (routine_id,)).fetchone() is None:
        raise ValueError("no such routine")
    conn.execute("UPDATE tonic_routines SET active = ? WHERE id = ?",
                 (1 if active else 0, routine_id))


def delete_routine(conn, routine_id):
    """Remove a routine and everything recorded against it.

    The doses go explicitly, not by the REFERENCES clause above: this app does
    not enable PRAGMA foreign_keys (see ARCHITECTURE.md §18), so that clause is
    documentation and every cascade here is done by hand. Leaving them would
    orphan rows that nothing can ever reach or delete again.
    """
    conn.execute("DELETE FROM tonic_doses WHERE routine_id = ?", (routine_id,))
    conn.execute("DELETE FROM tonic_routines WHERE id = ?", (routine_id,))


def log_dose(conn, routine_id, now=None, notes=None):
    """Record that it was given today."""
    if conn.execute("SELECT id FROM tonic_routines WHERE id = ?",
                    (routine_id,)).fetchone() is None:
        raise ValueError("no such routine")
    conn.execute(
        "INSERT INTO tonic_doses (routine_id, given_at, notes) VALUES (?, ?, ?)",
        (routine_id, _now_iso(now), (notes or "").strip() or None),
    )


def routines(conn, now=None, include_inactive=False):
    """Every routine with when it was last given and when it is next due.

    Derived from the last dose rather than stored, because "due" is a fact
    about the clock moving and a stored flag would need something to notice.
    """
    now = now or datetime.datetime.now()
    sql = "SELECT * FROM tonic_routines"
    if not include_inactive:
        sql += " WHERE active = 1"
    sql += " ORDER BY name"

    out = []
    for row in conn.execute(sql):
        last_row = conn.execute(
            "SELECT MAX(given_at) AS last FROM tonic_doses WHERE routine_id = ?",
            (row["id"],),
        ).fetchone()
        last = _parse(last_row["last"]) if last_row else None
        next_due = (last + datetime.timedelta(days=row["cadence_days"])) if last else None
        # Never given is due now rather than never due — a routine you added
        # and have not started is exactly the one worth being reminded about.
        days_over = ((now - next_due).total_seconds() / 86400.0) if next_due else None

        out.append({
            "id": row["id"],
            "name": row["name"],
            "dose": row["dose"],
            "cadence_days": row["cadence_days"],
            "notes": row["notes"],
            "active": bool(row["active"]),
            "last_given_at": last_row["last"] if last_row else None,
            "next_due_at": next_due.isoformat(timespec="seconds") if next_due else None,
            "days_overdue": round(days_over, 1) if days_over and days_over > 0 else None,
            "due": bool(next_due is None or now >= next_due),
            "overdue": bool(days_over is not None and days_over >= OVERDUE_AFTER_DAYS),
            "never_given": last is None,
            "doses": conn.execute(
                "SELECT COUNT(*) AS n FROM tonic_doses WHERE routine_id = ?",
                (row["id"],)).fetchone()["n"],
        })
    return out


def due(conn, now=None):
    return [r for r in routines(conn, now=now) if r["due"]]


def history(conn, limit=50):
    return [dict(row) for row in conn.execute(
        "SELECT d.id, d.routine_id, d.given_at, d.notes, r.name "
        "FROM tonic_doses d JOIN tonic_routines r ON r.id = d.routine_id "
        "ORDER BY d.given_at DESC LIMIT ?", (limit,))]


def reminder_message(due_routines):
    """What the notification says.

    Names them, because "the chickens need something" is a reminder you learn
    to dismiss. Two at most in the text: a list of five is a wall, and the card
    is one tap away for the rest.
    """
    if not due_routines:
        return None
    names = [r["name"] for r in due_routines]
    if len(names) == 1:
        return f"Time for the flock's {names[0].lower()}."
    if len(names) == 2:
        return f"Time for the flock's {names[0].lower()} and {names[1].lower()}."
    return (f"{len(names)} tonics are due for the flock, including "
            f"{names[0].lower()} and {names[1].lower()}.")


def summary(conn, now=None):
    active = routines(conn, now=now)
    return {
        "routines": active,
        "due": sum(1 for r in active if r["due"]),
        "overdue": sum(1 for r in active if r["overdue"]),
    }
