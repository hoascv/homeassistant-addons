"""Fermented feed: batches, the stirring they need, and when they are ready.

Fermenting feed means soaking grain in water for a few days and letting the wild
lactobacillus get to work. It is worth doing — the birds digest it better and
waste less of it — and it has one hard requirement that makes it a poor fit for
memory alone:

    **An unstirred batch grows mould on top and has to be thrown away.**

Stirring pushes the grain back under the water and lets the gas out. Miss it for
a day in a warm kitchen and the batch is compost. That is why the reminder is
the load-bearing part of this feature rather than a nicety, and why "when was
this last stirred" is the question the whole module is arranged around.

Kept out of app.py, which is already 3,600 lines: the schema, the state machine
and the arithmetic all live here, and app.py gains routes only.
"""
import datetime

# How long a batch ferments before it is ready to feed. Three days is the usual
# answer at room temperature; it is a setting because a cold Danish utility room
# in February and a warm kitchen in July are not the same place.
DEFAULT_FERMENT_DAYS = 3

# How long a batch may go unstirred before it is at risk. Twice a day is the
# common advice, so twelve hours is the interval that produces it.
DEFAULT_STIR_HOURS = 12

# Dry feed per bird per day, in grams. Roughly a quarter-cup of layer pellets,
# which is the figure most keepers land on. It expands with the water it takes
# up, so this is what goes *in*, not what comes out.
GRAMS_PER_BIRD_PER_DAY = 45

ACTIVE, READY, FED, DISCARDED = "fermenting", "ready", "fed", "discarded"

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS ferment_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        -- Which tub this is. Free text because people label them "1"/"2"/"3",
        -- or "blue lid", and the app has no business preferring one.
        container TEXT NOT NULL,
        started_at TEXT NOT NULL,
        ferment_days INTEGER NOT NULL,
        grams REAL,
        notes TEXT,
        -- Set when the batch leaves the rotation. Null means it is still going,
        -- which is what every "is anything due" query keys off.
        closed_at TEXT,
        -- 'fed' or 'discarded'. A batch thrown away for mould is not the same
        -- event as one the birds ate, and a keeper wanting to know how often
        -- that happens should be able to find out.
        outcome TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ferment_stirs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id INTEGER NOT NULL REFERENCES ferment_batches(id) ON DELETE CASCADE,
        stirred_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ferment_open ON ferment_batches(closed_at, started_at)",
    "CREATE INDEX IF NOT EXISTS idx_ferment_stirs ON ferment_stirs(batch_id, stirred_at)",
)


def create_schema(conn):
    for statement in SCHEMA:
        conn.execute(statement)


def _now_iso(now=None):
    return (now or datetime.datetime.now()).replace(microsecond=0).isoformat()


def _parse(value):
    try:
        return datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


# --- how much to make ---------------------------------------------------------


def suggested_grams(birds, days=DEFAULT_FERMENT_DAYS, per_bird=GRAMS_PER_BIRD_PER_DAY):
    """Dry feed for a batch that will cover the flock while it ferments.

    A rotation only works if each batch covers the days until the next one is
    ready, so the suggestion is birds x days rather than birds x one meal.
    Returned as a number the page can show alongside the input rather than
    silently filling it in: the keeper knows their birds and this does not.
    """
    if birds <= 0 or days <= 0:
        return 0
    return int(round(birds * days * per_bird))


# --- batches ------------------------------------------------------------------


def start_batch(conn, container, grams=None, ferment_days=DEFAULT_FERMENT_DAYS,
                notes=None, now=None):
    container = (container or "").strip()
    if not container:
        raise ValueError("a batch needs a container")
    days = max(1, min(14, int(ferment_days or DEFAULT_FERMENT_DAYS)))

    cursor = conn.execute(
        "INSERT INTO ferment_batches (container, started_at, ferment_days, grams, notes) "
        "VALUES (?, ?, ?, ?, ?)",
        (container, _now_iso(now), days, grams, (notes or "").strip() or None),
    )
    # A batch starts stirred. You just mixed the grain into the water, and
    # without this the first reminder fires an interval after starting rather
    # than an interval after the last time anybody touched it.
    conn.execute(
        "INSERT INTO ferment_stirs (batch_id, stirred_at) VALUES (?, ?)",
        (cursor.lastrowid, _now_iso(now)),
    )
    return cursor.lastrowid


def log_stir(conn, batch_id, now=None):
    row = conn.execute(
        "SELECT closed_at FROM ferment_batches WHERE id = ?", (batch_id,)).fetchone()
    if row is None:
        raise ValueError("no such batch")
    if row["closed_at"]:
        raise ValueError("that batch is finished")
    conn.execute(
        "INSERT INTO ferment_stirs (batch_id, stirred_at) VALUES (?, ?)",
        (batch_id, _now_iso(now)),
    )


def close_batch(conn, batch_id, outcome, now=None):
    """Take a batch out of the rotation, as fed or as thrown away."""
    if outcome not in (FED, DISCARDED):
        raise ValueError(f"outcome must be {FED!r} or {DISCARDED!r}")
    conn.execute(
        "UPDATE ferment_batches SET closed_at = ?, outcome = ? WHERE id = ? AND closed_at IS NULL",
        (_now_iso(now), outcome, batch_id),
    )


def _state(row, last_stir, now):
    if row["closed_at"]:
        return row["outcome"] or FED
    started = _parse(row["started_at"])
    if started and now >= started + datetime.timedelta(days=row["ferment_days"]):
        return READY
    return ACTIVE


def batches(conn, include_closed=False, now=None, stir_hours=DEFAULT_STIR_HOURS):
    """Every batch with its derived state and how overdue a stir is.

    Derived rather than stored: a batch becomes ready by the clock moving, not
    by anything happening, and a stored state would need something to notice.
    """
    now = now or datetime.datetime.now()
    sql = "SELECT * FROM ferment_batches"
    if not include_closed:
        sql += " WHERE closed_at IS NULL"
    sql += " ORDER BY started_at"

    out = []
    for row in conn.execute(sql):
        stir_row = conn.execute(
            "SELECT MAX(stirred_at) AS last FROM ferment_stirs WHERE batch_id = ?",
            (row["id"],),
        ).fetchone()
        last_stir = _parse(stir_row["last"]) if stir_row else None
        started = _parse(row["started_at"])
        state = _state(row, last_stir, now)

        hours_since = ((now - last_stir).total_seconds() / 3600.0) if last_stir else None
        ready_at = (started + datetime.timedelta(days=row["ferment_days"])) if started else None

        out.append({
            "id": row["id"],
            "container": row["container"],
            "started_at": row["started_at"],
            "ferment_days": row["ferment_days"],
            "grams": row["grams"],
            "notes": row["notes"],
            "state": state,
            "outcome": row["outcome"],
            "ready_at": ready_at.isoformat() if ready_at else None,
            "last_stirred_at": stir_row["last"] if stir_row else None,
            "hours_since_stir": round(hours_since, 1) if hours_since is not None else None,
            # Only ever true for an open batch. A finished one cannot need
            # stirring, and a reminder naming it would be nonsense.
            "stir_due": bool(
                not row["closed_at"] and hours_since is not None and hours_since >= stir_hours
            ),
            "stirs": conn.execute(
                "SELECT COUNT(*) AS n FROM ferment_stirs WHERE batch_id = ?", (row["id"],)
            ).fetchone()["n"],
        })
    return out


def due_for_stir(conn, now=None, stir_hours=DEFAULT_STIR_HOURS):
    """Open batches that have gone too long without a stir."""
    return [b for b in batches(conn, now=now, stir_hours=stir_hours) if b["stir_due"]]


def ready_batches(conn, now=None):
    return [b for b in batches(conn, now=now) if b["state"] == READY]


def stir_message(due):
    """What the notification says.

    It names the containers, because a keeper with three tubs on the go needs
    to know which — and a reminder that says only "stir something" is one you
    learn to dismiss.
    """
    if not due:
        return None
    names = ", ".join(b["container"] for b in due)
    if len(due) == 1:
        return f"Stir the fermenting feed in {names} — it has been {int(due[0]['hours_since_stir'])}h."
    return f"Stir the fermenting feed: {names} ({len(due)} containers waiting)."


def summary(conn, birds, now=None, stir_hours=DEFAULT_STIR_HOURS):
    """The figures the card and the sensor both show."""
    open_batches = batches(conn, now=now, stir_hours=stir_hours)
    return {
        "batches": open_batches,
        "open": len(open_batches),
        "ready": sum(1 for b in open_batches if b["state"] == READY),
        "stir_due": sum(1 for b in open_batches if b["stir_due"]),
        "suggested_grams": suggested_grams(birds),
        "birds": birds,
    }
