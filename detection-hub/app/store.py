"""SQLite: what was seen, when, and a picture of it.

The schema is shaped by two constraints that the trackers do not have.

**Volume.** A driveway generates far more rows than someone logging eggs, so
retention is not optional here — it runs on the background loop and is bounded
by both age and count.

**Durability cost.** STORAGE-IO.md measures this host at 286 durable commits a
second, a budget Postgres' own commit path shares. A commit per detection would
compete directly with it and show up as rising write latency while utilisation
stayed flat. So the capture pipeline batches: callers insert, and commit on an
interval rather than per row.

Snapshots live in their own table rather than as a column on `detections`, which
is what keeps images out of the change feed by construction rather than by
remembering to filter them.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta

DB_PATH = os.environ.get("DETECTION_HUB_DB_PATH", "/data/detections.db")

# What a downstream pipeline cares about, and the column identifying a row.
# Deliberately absent: `snapshots`, whose rows are JPEG bytes — a change event
# should stay small, and a consumer that wants the image can ask for it by id.
TRACKED_TABLES = {
    "detections": "id",
    "cameras": "id",
}

# Belt and braces. Nothing in TRACKED_TABLES holds a blob today; this is what
# stops that being silently untrue the day someone adds one.
BLOB_COLUMNS = {("snapshots", "image")}

CHANGE_LOG_KEEP_DAYS = 90


def now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


class AttributedConnection(sqlite3.Connection):
    """Stamps who caused a change, on commit.

    The triggers cannot know: they see the row, not the caller. So each
    connection carries an actor and claims the rows its own transaction wrote —
    `user` for a request, `camera` for the capture threads, `migration` for
    schema work.
    """

    actor = None

    def commit(self):
        if self.actor:
            self.execute(
                "UPDATE change_log SET actor = ? WHERE actor IS NULL", (self.actor,)
            )
        super().commit()


def connect(path=None, actor="user"):
    conn = sqlite3.connect(path or DB_PATH, factory=AttributedConnection)
    conn.actor = actor
    conn.row_factory = sqlite3.Row
    return conn


# --- schema -------------------------------------------------------------------


def init_db(path=None):
    """Converge the schema. Idempotent, run on every boot.

    No migration framework and no version table: `CREATE TABLE IF NOT EXISTS`
    plus a `PRAGMA table_info` check per added column means an upgrade — or a
    restore of an older database — migrates itself with nothing to remember.
    """
    target = path or DB_PATH
    os.makedirs(os.path.dirname(target), exist_ok=True)
    conn = connect(target, actor="migration")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera TEXT NOT NULL,
            label TEXT NOT NULL,
            confidence REAL NOT NULL,
            box_x INTEGER, box_y INTEGER, box_w INTEGER, box_h INTEGER,
            detected_at TEXT NOT NULL,
            -- Nullable: a detection is worth keeping even once its snapshot has
            -- been pruned, and images age out far faster than rows.
            snapshot_id INTEGER
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_detections_time ON detections(detected_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_detections_camera ON detections(camera, detected_at)"
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image BLOB NOT NULL,
            width INTEGER,
            height INTEGER,
            taken_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_time ON snapshots(taken_at)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cameras (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            state TEXT,
            detail TEXT,
            last_frame_at TEXT,
            last_detection_at TEXT,
            frames_seen INTEGER NOT NULL DEFAULT 0,
            frames_detected INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS change_log (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT NOT NULL,
            row_id TEXT NOT NULL,
            op TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            actor TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_change_log_table ON change_log(table_name, seq)"
    )

    install_change_triggers(conn)
    conn.commit()
    conn.close()


def install_change_triggers(conn):
    """Recreate the CDC triggers, every boot.

    Triggers rather than application code because `app.py` has many write paths
    and will grow more, added by someone who is not thinking about a downstream
    pipeline at the time. A trigger cannot be forgotten, and it also catches
    writes that never go through a route.
    """
    existing = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for table, key in TRACKED_TABLES.items():
        if table not in existing:
            continue
        for op, when, ref in (("I", "INSERT", "NEW"), ("U", "UPDATE", "NEW"), ("D", "DELETE", "OLD")):
            name = f"trg_{table}_{op.lower()}_changelog"
            conn.execute(f"DROP TRIGGER IF EXISTS {name}")
            conn.execute(
                f"CREATE TRIGGER {name} AFTER {when} ON {table} BEGIN "
                f"INSERT INTO change_log (table_name, row_id, op, changed_at) "
                f"VALUES ('{table}', CAST({ref}.{key} AS TEXT), '{op}', "
                f"strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')); END"
            )


# --- writing ------------------------------------------------------------------


def save_snapshot(conn, jpeg, width, height):
    cur = conn.execute(
        "INSERT INTO snapshots (image, width, height, taken_at) VALUES (?, ?, ?, ?)",
        (sqlite3.Binary(jpeg), width, height, now()),
    )
    return cur.lastrowid


def record_detections(conn, camera, detections, snapshot_id=None, at=None):
    """Insert one row per detection. Does not commit — the caller batches."""
    stamp = at or now()
    rows = [
        (
            camera,
            det["label"],
            float(det["confidence"]),
            det["box"][0], det["box"][1], det["box"][2], det["box"][3],
            stamp,
            snapshot_id,
        )
        for det in detections
    ]
    conn.executemany(
        "INSERT INTO detections (camera, label, confidence, box_x, box_y, box_w,"
        " box_h, detected_at, snapshot_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def upsert_camera(conn, camera_id, kind, **fields):
    """Register a camera, or update its live state.

    Written as an upsert that only touches the columns given, because the
    capture thread and the configuration reload both call it and neither should
    clobber what the other knows.
    """
    conn.execute(
        "INSERT INTO cameras (id, kind) VALUES (?, ?) ON CONFLICT(id) DO NOTHING",
        (camera_id, kind),
    )
    if fields:
        assignments = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(
            f"UPDATE cameras SET {assignments} WHERE id = ?",
            (*fields.values(), camera_id),
        )


def bump_camera_counters(conn, camera_id, frames_seen=0, frames_detected=0):
    conn.execute(
        "UPDATE cameras SET frames_seen = frames_seen + ?,"
        " frames_detected = frames_detected + ? WHERE id = ?",
        (frames_seen, frames_detected, camera_id),
    )


# --- retention ----------------------------------------------------------------


def prune(conn, detection_days=30, snapshot_days=7, snapshot_max=2000):
    """Age and count limits, both enforced. Returns what it removed.

    Two limits because either alone fails: a quiet week keeps images longer than
    intended, and a busy hour blows past any size expectation well inside the
    age window. `/data` is inside Home Assistant's backups, which is what makes
    the ceiling matter rather than just being tidy.
    """
    removed = {}

    cutoff = (datetime.now() - timedelta(days=detection_days)).strftime("%Y-%m-%dT%H:%M:%S")
    removed["detections"] = conn.execute(
        "DELETE FROM detections WHERE detected_at < ?", (cutoff,)
    ).rowcount

    snap_cutoff = (datetime.now() - timedelta(days=snapshot_days)).strftime("%Y-%m-%dT%H:%M:%S")
    by_age = conn.execute(
        "DELETE FROM snapshots WHERE taken_at < ?", (snap_cutoff,)
    ).rowcount
    by_count = conn.execute(
        "DELETE FROM snapshots WHERE id NOT IN ("
        " SELECT id FROM snapshots ORDER BY id DESC LIMIT ?)",
        (snapshot_max,),
    ).rowcount
    removed["snapshots"] = by_age + by_count

    # Detections outlive their images, so the dangling reference is cleared
    # rather than left pointing at a row that is gone.
    conn.execute(
        "UPDATE detections SET snapshot_id = NULL WHERE snapshot_id IS NOT NULL"
        " AND snapshot_id NOT IN (SELECT id FROM snapshots)"
    )

    removed["change_log"] = prune_change_log(conn)
    return removed


def prune_change_log(conn, keep_days=CHANGE_LOG_KEEP_DAYS):
    """Drop old change rows, except each row's most recent entry.

    Keeping the last entry per row means a consumer rebuilding from the feed
    still lands on current state rather than on a partial history.
    """
    cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%dT%H:%M:%S")
    return conn.execute(
        "DELETE FROM change_log WHERE changed_at < ? AND seq NOT IN ("
        " SELECT MAX(seq) FROM change_log GROUP BY table_name, row_id)",
        (cutoff,),
    ).rowcount


# --- the feed -----------------------------------------------------------------


def _serialisable(row, table):
    """A row as a plain dict, with any blob column dropped."""
    out = {}
    for key in row.keys():
        if (table, key) in BLOB_COLUMNS:
            continue
        out[key] = row[key]
    return out


def max_seq(conn):
    return conn.execute("SELECT COALESCE(MAX(seq), 0) FROM change_log").fetchone()[0]


def export(conn):
    """A full snapshot of every tracked table, plus the seq it corresponds to.

    `keys` is included because a consumer cannot infer which column identifies a
    row — JSON object key order is not something to rely on, and guessing the
    first column is how you collapse a table into a handful of rows.
    """
    tables = {}
    for table in TRACKED_TABLES:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        tables[table] = [_serialisable(row, table) for row in rows]
    return {
        "taken_at": now(),
        "max_seq": max_seq(conn),
        "keys": dict(TRACKED_TABLES),
        "tables": tables,
    }


def changes(conn, since=0, limit=1000):
    """Everything after `since`, oldest first, with the row as it stands now.

    A delete carries `row: null` — the row is gone and there is nothing honest
    to attach. Consumers key on `op` for that, not on the absence of data.
    """
    limit = max(1, min(5000, int(limit)))
    rows = conn.execute(
        "SELECT seq, table_name, row_id, op, changed_at, actor FROM change_log"
        " WHERE seq > ? ORDER BY seq LIMIT ?",
        (int(since), limit),
    ).fetchall()

    out = []
    for row in rows:
        table = row["table_name"]
        payload = None
        if row["op"] != "D" and table in TRACKED_TABLES:
            key = TRACKED_TABLES[table]
            found = conn.execute(
                f"SELECT * FROM {table} WHERE CAST({key} AS TEXT) = ?", (row["row_id"],)
            ).fetchone()
            payload = _serialisable(found, table) if found else None
        out.append(
            {
                "seq": row["seq"],
                "table": table,
                "row_id": row["row_id"],
                "op": row["op"],
                "changed_at": row["changed_at"],
                "actor": row["actor"],
                "row": payload,
            }
        )

    bounds = conn.execute("SELECT MIN(seq) lo, MAX(seq) hi FROM change_log").fetchone()
    return {
        "changes": out,
        "since": int(since),
        "min_seq": bounds["lo"],
        "max_seq": bounds["hi"],
        # The consumer's watermark predates our oldest surviving row, so the
        # gap between them is unrecoverable and it must bootstrap instead.
        "full_reload_required": bool(
            since and bounds["lo"] and int(since) < bounds["lo"] - 1
        ),
    }


def stats(conn, db_path=None):
    """Row counts and size, without serialising a single row.

    The watchdog polls this every scan. Answering it with `export` would mean
    re-serialising the whole database once a minute to display a number.
    """
    counts, other = {}, {}
    for table in TRACKED_TABLES:
        counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    for table in ("snapshots", "change_log"):
        other[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    try:
        db_bytes = os.path.getsize(db_path or DB_PATH)
    except OSError:
        db_bytes = None

    return {
        "taken_at": now(),
        "db_bytes": db_bytes,
        "max_seq": max_seq(conn),
        "counts": counts,
        "other_counts": other,
        "total": sum(counts.values()),
        "other_total": sum(other.values()),
        "total_all": sum(counts.values()) + sum(other.values()),
    }


# --- reading for the UI and sensors -------------------------------------------


def recent_detections(conn, limit=50, camera=None):
    sql = "SELECT * FROM detections"
    params = []
    if camera:
        sql += " WHERE camera = ?"
        params.append(camera)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(500, int(limit))))
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def snapshot(conn, snapshot_id):
    row = conn.execute(
        "SELECT image, width, height FROM snapshots WHERE id = ?", (snapshot_id,)
    ).fetchone()
    return row


def counts_since(conn, since_iso):
    """Detections per label since a timestamp — what the HA sensors publish."""
    rows = conn.execute(
        "SELECT label, COUNT(*) n FROM detections WHERE detected_at >= ?"
        " GROUP BY label ORDER BY n DESC",
        (since_iso,),
    ).fetchall()
    return {row["label"]: row["n"] for row in rows}


def cameras(conn):
    return [dict(row) for row in conn.execute("SELECT * FROM cameras ORDER BY id")]
