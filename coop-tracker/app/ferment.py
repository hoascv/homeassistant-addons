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

# A batch seeded with liquid from the last one starts with a working culture
# rather than waiting for wild lactobacillus to find it, so it gets there
# sooner. Two days is the usual answer; it is a floor on the setting rather
# than a replacement for it, because a cold room still slows it down.
SEEDED_FERMENT_DAYS = 2

# How long saved liquid stays worth using, in days. It is a live culture in a
# jar in the fridge: it does not spoil so much as go quiet, and seeding with
# something exhausted gives you the wait you were trying to avoid plus a false
# sense that you did not.
STARTER_GOOD_FOR_DAYS = 7

# Generations before it is worth starting clean. Backslopping indefinitely lets
# whatever is most vigorous take over, which is not always what you want. This
# is advice on the page, never a refusal — it is the keeper's jar.
STARTER_GENERATION_HINT = 8

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
        outcome TEXT,
        -- How many times the culture in this batch has been carried forward.
        -- 0 is a batch started from nothing; 1 was seeded from a generation-0
        -- batch, and so on. Worth keeping because a culture drifts with each
        -- pass and the number is the only way to notice.
        generation INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ferment_starter (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        saved_at TEXT NOT NULL,
        -- The batch the liquid was drained from, and the one it later seeded.
        -- Kept as plain ids without a foreign key: a jar outlives the batch it
        -- came from, and deleting old history should not take the culture's
        -- provenance with it.
        from_batch_id INTEGER,
        used_at TEXT,
        used_by_batch_id INTEGER,
        generation INTEGER NOT NULL DEFAULT 0,
        notes TEXT
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
    "CREATE INDEX IF NOT EXISTS idx_ferment_starter ON ferment_starter(used_at, saved_at)",
)


def create_schema(conn):
    for statement in SCHEMA:
        conn.execute(statement)
    # CREATE TABLE IF NOT EXISTS handles a fresh install; an add-on that already
    # ran 1.45.0 has the table without this column.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(ferment_batches)")}
    if "generation" not in columns:
        conn.execute("ALTER TABLE ferment_batches ADD COLUMN generation INTEGER NOT NULL DEFAULT 0")


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
                notes=None, now=None, use_starter=False):
    """Begin a batch, optionally seeded with saved liquid from the last one.

    Seeding is the whole point of keeping the jar: the new grain arrives with a
    working culture instead of waiting for wild lactobacillus to find it, and
    gets there in about two days rather than three or four.

    The liquid is carried forward and the *grain* is not, which is the part
    that matters. Old wet grain is where spoilage organisms have had days to
    establish; the drained liquid is the culture without the substrate they
    were living on.
    """
    container = (container or "").strip()
    if not container:
        raise ValueError("a batch needs a container")
    days = max(1, min(14, int(ferment_days or DEFAULT_FERMENT_DAYS)))

    starter = current_starter(conn, now=now) if use_starter else None
    generation = 0
    if use_starter:
        if starter is None:
            raise ValueError("there is no saved liquid to start from")
        generation = starter["generation"] + 1
        # A seeded batch is faster, but the setting is still a ceiling: a cold
        # room slows a live culture down too, so this shortens rather than
        # overrides.
        days = min(days, SEEDED_FERMENT_DAYS)

    cursor = conn.execute(
        "INSERT INTO ferment_batches "
        "(container, started_at, ferment_days, grams, notes, generation) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (container, _now_iso(now), days, grams, (notes or "").strip() or None, generation),
    )
    if starter is not None:
        conn.execute(
            "UPDATE ferment_starter SET used_at = ?, used_by_batch_id = ? WHERE id = ?",
            (_now_iso(now), cursor.lastrowid, starter["id"]),
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


# --- the jar ------------------------------------------------------------------


def save_starter(conn, batch_id, notes=None, now=None):
    """Keep the liquid from a batch to seed the next one.

    Only one jar is kept. Saving again replaces what was there rather than
    accumulating: there is one jar in the fridge, and a list of five would be a
    model of something that does not exist.
    """
    row = conn.execute(
        "SELECT generation FROM ferment_batches WHERE id = ?", (batch_id,)).fetchone()
    if row is None:
        raise ValueError("no such batch")

    conn.execute("DELETE FROM ferment_starter WHERE used_at IS NULL")
    conn.execute(
        "INSERT INTO ferment_starter (saved_at, from_batch_id, generation, notes) "
        "VALUES (?, ?, ?, ?)",
        (_now_iso(now), batch_id, row["generation"], (notes or "").strip() or None),
    )


def discard_starter(conn):
    """Throw the jar out — the way back to a clean start."""
    conn.execute("DELETE FROM ferment_starter WHERE used_at IS NULL")


def current_starter(conn, now=None):
    """The jar in the fridge, or None.

    Carries its age and how many times the culture has been passed on, because
    both are things the keeper should decide about rather than have decided for
    them: this never refuses to seed a batch, it only says what it knows.
    """
    row = conn.execute(
        "SELECT * FROM ferment_starter WHERE used_at IS NULL ORDER BY saved_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None

    now = now or datetime.datetime.now()
    saved = _parse(row["saved_at"])
    age_days = ((now - saved).total_seconds() / 86400.0) if saved else None
    return {
        "id": row["id"],
        "saved_at": row["saved_at"],
        "from_batch_id": row["from_batch_id"],
        "generation": row["generation"],
        "notes": row["notes"],
        "age_days": round(age_days, 1) if age_days is not None else None,
        # Not "bad" — a quiet culture gives you the wait you were avoiding plus
        # a false sense that you were not waiting.
        "stale": bool(age_days is not None and age_days > STARTER_GOOD_FOR_DAYS),
        "many_generations": row["generation"] + 1 >= STARTER_GENERATION_HINT,
    }


def close_batch(conn, batch_id, outcome, now=None, save_liquid=False):
    """Take a batch out of the rotation, as fed or as thrown away.

    `save_liquid` drains the brine into the jar on the way out, which is the
    step that makes the next batch a two-day one. It is deliberately refused on
    a discarded batch: a batch thrown out for mould is exactly the culture you
    do not want to carry into the next bucket, and the one moment someone would
    be tempted to save it is the moment it is most expensive to.
    """
    if outcome not in (FED, DISCARDED):
        raise ValueError(f"outcome must be {FED!r} or {DISCARDED!r}")
    if save_liquid and outcome != FED:
        raise ValueError("only keep the liquid from a batch the birds ate")
    conn.execute(
        "UPDATE ferment_batches SET closed_at = ?, outcome = ? WHERE id = ? AND closed_at IS NULL",
        (_now_iso(now), outcome, batch_id),
    )
    if save_liquid:
        save_starter(conn, batch_id, now=now)


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
            "generation": row["generation"],
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
        "starter": current_starter(conn, now=now),
        "open": len(open_batches),
        "ready": sum(1 for b in open_batches if b["state"] == READY),
        "stir_due": sum(1 for b in open_batches if b["stir_due"]),
        "suggested_grams": suggested_grams(birds),
        "birds": birds,
    }
