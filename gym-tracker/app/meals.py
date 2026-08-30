"""Meal adherence: which meals were eaten, which were skipped, and — kept
carefully distinct from both — which were never recorded at all.

This exists because a skipped meal is data about a goal, and a weight trend with
no idea whether the person was eating is a trend you cannot reason about. It is
deliberately *not* nutrition tracking: no calories, no portions, no food. Those
are numbers this user cannot reliably produce, and a field nobody fills in
honestly is worse than no field, because it looks like evidence.

## The three states, which is the whole design

    ate       an explicit record that the meal happened
    skipped   an explicit record that it did not
    unknown   NO ROW — nothing was recorded

The daily challenge next door stores a tick per item per day and treats absence
as "not done", which is right for a challenge: not ticking a press-up is not
doing it. That model cannot be borrowed here. If a missing row meant "skipped",
then every day the app was not opened would read as skipping every meal, and a
fortnight on holiday would become the largest apparent deficit in the history —
poisoning exactly the correlation this is built to support.

So absence means *unknown* and is never counted as a skip anywhere. The cost is
that adherence figures have to carry a denominator: `skipped 3 of 18 recorded`
is a fact, `skipped 3 of 21` would be a guess. Every function here that returns
a rate returns what it is out of.
"""
import datetime

# The default meals. Held here rather than in the schema so the set can change
# without a migration: rows carry their meal as text, and a meal removed from
# this list keeps its history rather than losing it.
DEFAULT_MEALS = ("Breakfast", "Lunch", "Dinner")

ATE = "ate"
SKIPPED = "skipped"
STATUSES = (ATE, SKIPPED)

SCHEMA = """
    CREATE TABLE IF NOT EXISTS meal_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day TEXT NOT NULL,
        -- The meal's name as text, not a foreign key. Renaming or retiring a
        -- meal must not rewrite or orphan what was already recorded about it.
        meal TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('ate', 'skipped')),
        -- When it was recorded, which is not the same as the day it belongs
        -- to: logging last night's skipped dinner this morning is normal.
        ts TEXT NOT NULL,
        note TEXT,
        UNIQUE(day, meal)
    )
"""

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_meal_logs_day ON meal_logs(day)",
    "CREATE INDEX IF NOT EXISTS idx_meal_logs_status ON meal_logs(status, day)",
)


def create_schema(conn):
    conn.execute(SCHEMA)
    for statement in INDEXES:
        conn.execute(statement)


def _now():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def configured_meals(raw):
    """The meal names from settings, or the defaults.

    Free text rather than a fixed list because meal names are personal and
    cultural — someone eating four times, or calling the evening meal 'tea',
    should not have to argue with the app about it. Blanks are dropped and
    order is kept, since the page renders them in the order given.
    """
    if not raw:
        return list(DEFAULT_MEALS)
    names, seen = [], set()
    for part in str(raw).replace("\n", ",").split(","):
        name = part.strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    return names or list(DEFAULT_MEALS)


def log_meal(conn, day, meal, status, note=None):
    """Record a meal as eaten or skipped. Re-logging the same meal replaces it.

    Replacing rather than appending: there is one truth about whether lunch
    happened, and a mis-tap should be correctable by tapping the other button
    rather than by finding a delete.
    """
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, not {status!r}")
    meal = (meal or "").strip()
    if not meal:
        raise ValueError("meal is required")
    note = (note or "").strip() or None

    conn.execute(
        "INSERT INTO meal_logs (day, meal, status, ts, note) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(day, meal) DO UPDATE SET status = excluded.status, "
        "ts = excluded.ts, note = excluded.note",
        (day, meal, status, _now(), note),
    )
    return get_day(conn, day)


def clear_meal(conn, day, meal):
    """Remove a record, returning the meal to unknown.

    Distinct from logging a skip, and the difference matters: this is 'I should
    not have recorded that', not 'I did not eat'.
    """
    conn.execute("DELETE FROM meal_logs WHERE day = ? AND meal = ?", (day, (meal or "").strip()))
    return get_day(conn, day)


def get_day(conn, day, meals=None):
    """Every configured meal for a day, each with its status or None.

    Returns a row per *configured* meal rather than per stored row, so the page
    can render the unknown ones as untouched buttons without having to know
    which are missing. A stored meal no longer in the configured list is
    appended, so history stays visible after a rename.
    """
    stored = {
        row["meal"]: row
        for row in conn.execute(
            "SELECT meal, status, ts, note FROM meal_logs WHERE day = ?", (day,)
        )
    }
    names = list(meals) if meals is not None else list(DEFAULT_MEALS)
    extra = [name for name in stored if name not in names]

    out = []
    for name in names + sorted(extra):
        row = stored.get(name)
        out.append({
            "meal": name,
            "status": row["status"] if row else None,
            "ts": row["ts"] if row else None,
            "note": row["note"] if row else None,
            # True for a meal that is only present because it was logged under
            # a name no longer configured — the page greys these out.
            "retired": name in extra,
        })
    return out


def day_summary(conn, day, meals=None):
    """Counts for one day, with the denominator stated."""
    rows = get_day(conn, day, meals)
    live = [r for r in rows if not r["retired"]]
    return {
        "day": day,
        "meals": rows,
        "ate": sum(1 for r in rows if r["status"] == ATE),
        "skipped": sum(1 for r in rows if r["status"] == SKIPPED),
        # Of the configured meals, how many have any record at all. The page
        # uses this to say "2 of 3 recorded" rather than implying the third
        # was eaten.
        "recorded": sum(1 for r in live if r["status"] is not None),
        "expected": len(live),
    }


def range_summary(conn, start, end, meals=None):
    """Adherence over a window, as counts — never as an inferred rate.

    `skip_rate` is skipped ÷ recorded, not skipped ÷ expected. The difference
    is the point of this module: days nobody logged are absent from both halves
    rather than silently counted as compliance or as failure.
    """
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM meal_logs WHERE day >= ? AND day <= ? GROUP BY status",
        (start, end),
    ).fetchall()
    counts = {row["status"]: row["n"] for row in rows}
    ate = counts.get(ATE, 0)
    skipped = counts.get(SKIPPED, 0)
    recorded = ate + skipped

    days = (datetime.date.fromisoformat(end) - datetime.date.fromisoformat(start)).days + 1
    per_day = len(meals) if meals is not None else len(DEFAULT_MEALS)

    return {
        "start": start,
        "end": end,
        "ate": ate,
        "skipped": skipped,
        "recorded": recorded,
        # What could have been recorded had every meal been logged every day.
        # Reported so the caller can show coverage; never used as a denominator
        # for the skip rate.
        "possible": days * per_day,
        "skip_rate": round(skipped / recorded, 3) if recorded else None,
        "coverage": round(recorded / (days * per_day), 3) if days and per_day else None,
    }


def skipped_days(conn, start, end):
    """Days in the window with at least one skipped meal, and which ones.

    This is what the weight chart marks. Only days with an explicit skip
    appear — a day with no records produces nothing to draw, which is correct:
    there is nothing known about it to show.
    """
    rows = conn.execute(
        "SELECT day, meal FROM meal_logs WHERE status = ? AND day >= ? AND day <= ? "
        "ORDER BY day, meal",
        (SKIPPED, start, end),
    ).fetchall()
    out = {}
    for row in rows:
        out.setdefault(row["day"], []).append(row["meal"])
    return [{"day": day, "meals": meals_} for day, meals_ in sorted(out.items())]


def current_streak(conn, meals=None, today=None):
    """Consecutive days back from today with every configured meal eaten.

    A day with an unknown meal ends the streak rather than being skipped over.
    Counting it as a success would make the streak a measure of how often the
    app was opened; ending it is the honest reading, since nobody can say what
    happened on a day nobody recorded.
    """
    names = list(meals) if meals is not None else list(DEFAULT_MEALS)
    if not names:
        return 0
    day = datetime.date.fromisoformat(today) if today else datetime.date.today()

    streak = 0
    while True:
        rows = get_day(conn, day.isoformat(), names)
        live = [r for r in rows if not r["retired"]]
        if live and all(r["status"] == ATE for r in live):
            streak += 1
            day -= datetime.timedelta(days=1)
            continue
        return streak


def recent_notes(conn, limit=20):
    """The most recent meals carrying a note, newest first.

    Notes are the qualitative half — 'skipped lunch, long meeting' is what
    makes a run of skips interpretable months later.
    """
    return [
        dict(row)
        for row in conn.execute(
            "SELECT day, meal, status, note, ts FROM meal_logs "
            "WHERE note IS NOT NULL AND note != '' ORDER BY day DESC, ts DESC LIMIT ?",
            (limit,),
        )
    ]
