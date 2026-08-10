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

**Backups.** Snapshots are files under `<data>/snapshots/`, indexed by a row but
never stored in it. Home Assistant copies `/data` into every backup, and at
~68 KB a frame the 2000-image default would carry ~136 MB of pictures into every
one, forever — so `config.yaml` excludes that directory. The detections stay in
the database and are backed up; a restore brings back every row and none of the
images, which the nullable `snapshot_id` already allows for.

Keeping images out of the table also keeps them out of the change feed by
construction rather than by remembering to filter a blob.
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
    # The file this connection actually opened. Snapshots are stored beside it,
    # so the images always follow the database rather than a module-level
    # default — a connection to another path must not write its pictures next
    # to someone else's data.
    db_path = None

    def commit(self):
        if self.actor:
            self.execute(
                "UPDATE change_log SET actor = ? WHERE actor IS NULL", (self.actor,)
            )
        super().commit()


def snapshot_dir(db_path=None):
    """Where the JPEGs live: beside the database, not inside it.

    Derived from the database path rather than configured separately, so the two
    cannot be pointed at different volumes by accident — and so a test that
    redirects the database gets its images redirected with it.
    """
    return os.path.join(os.path.dirname(db_path or DB_PATH), "snapshots")


def connect(path=None, actor="user"):
    resolved = path or DB_PATH
    conn = sqlite3.connect(resolved, factory=AttributedConnection)
    conn.actor = actor
    conn.db_path = resolved
    conn.row_factory = sqlite3.Row

    # WAL, because this add-on writes from several threads at once — a camera
    # worker per stream, four waitress threads, and the prune loop. Measured on
    # four concurrent writers: 1,500 commits/s on the rollback journal against
    # 2,161 with WAL and synchronous=NORMAL, a 44% gain with no errors and no
    # lost rows either way.
    #
    # It is a throughput improvement, not a repair: the default configuration
    # loses nothing under contention. `busy_timeout` in particular is already
    # 5000 ms, because Python's sqlite3.connect defaults to timeout=5.0 — the
    # C library's own default of 0 is what it is not.
    #
    # NORMAL is durable across a crash of this process under WAL; only an OS
    # crash or power loss can lose the last commits, and it removes an fsync per
    # commit. That matters on this host specifically: STORAGE-IO.md measures it
    # at 286 durable commits/s, a budget Postgres shares.
    #
    # Both must run before any transaction opens — SQLite refuses to change the
    # safety level inside one, and journal_mode is persisted in the file header
    # so the WAL line is a no-op after the first connection.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
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

    # An index, not a blob store: the bytes live in files beside the database.
    # `bytes` is the file's size, so disk use is a SUM rather than a directory
    # walk, and a row whose file failed to write is visibly incomplete.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            width INTEGER,
            height INTEGER,
            taken_at TEXT NOT NULL,
            bytes INTEGER
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_time ON snapshots(taken_at)")
    _migrate_snapshots_to_files(conn, target)

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


def _migrate_snapshots_to_files(conn, db_path):
    """Move blobs out of an older database, then drop the column.

    Images lived in the `image` column until 1.3.0. Anything already stored is
    written out to its file rather than discarded — the add-on is new enough
    that this will usually find nothing, but "usually" is not a reason to lose
    somebody's data.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(snapshots)")}
    if "image" not in columns:
        return 0

    directory = snapshot_dir(db_path)
    os.makedirs(directory, exist_ok=True)
    moved = 0
    for row in conn.execute("SELECT id, image FROM snapshots WHERE image IS NOT NULL"):
        path = snapshot_path(row[0], directory)
        if os.path.exists(path):
            continue
        try:
            with open(path, "wb") as handle:
                handle.write(bytes(row[1]))
            moved += 1
        except OSError:
            # Leave the row; the file is simply missing and the endpoint 404s,
            # which is the same state as a snapshot pruned early.
            pass

    # SQLite cannot drop a NOT NULL column in place, so the table is rebuilt.
    # `snapshots` is not in TRACKED_TABLES, so no trigger has to be rebuilt with
    # it and the change feed is untouched.
    conn.executescript(
        """
        CREATE TABLE snapshots_rebuilt (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            width INTEGER,
            height INTEGER,
            taken_at TEXT NOT NULL,
            bytes INTEGER
        );
        INSERT INTO snapshots_rebuilt (id, width, height, taken_at, bytes)
            SELECT id, width, height, taken_at, length(image) FROM snapshots;
        DROP TABLE snapshots;
        ALTER TABLE snapshots_rebuilt RENAME TO snapshots;
        CREATE INDEX IF NOT EXISTS idx_snapshots_time ON snapshots(taken_at);
        """
    )
    return moved


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


def snapshot_dir_for(conn):
    """The image directory belonging to whatever database this connection opened."""
    return snapshot_dir(getattr(conn, "db_path", None))


def snapshot_path(snapshot_id, directory=None):
    return os.path.join(directory or snapshot_dir(), f"{snapshot_id}.jpg")


def save_snapshot(conn, jpeg, width, height, directory=None):
    """Write the image beside the database and index it. Returns the id, or None.

    The row is inserted first because its id names the file. If the write then
    fails — a full disk, a read-only volume — the row is removed and None comes
    back, so the caller still stores the detection with a null snapshot_id. A
    picture is not worth losing the detection over.
    """
    directory = directory or snapshot_dir_for(conn)
    cur = conn.execute(
        "INSERT INTO snapshots (width, height, taken_at, bytes) VALUES (?, ?, ?, NULL)",
        (width, height, now()),
    )
    snapshot_id = cur.lastrowid
    try:
        os.makedirs(directory, exist_ok=True)
        with open(snapshot_path(snapshot_id, directory), "wb") as handle:
            handle.write(jpeg)
    except OSError:
        conn.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))
        return None
    conn.execute(
        "UPDATE snapshots SET bytes = ? WHERE id = ?", (len(jpeg), snapshot_id)
    )
    return snapshot_id


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


def prune(conn, detection_days=30, snapshot_days=7, snapshot_max=2000, directory=None):
    """Age and count limits, both enforced, on rows and on files.

    Two limits because either alone fails: a quiet week keeps images longer than
    intended, and a busy hour blows past any size expectation well inside the
    age window. With a 30-second cooldown one busy camera reaches the count in
    under a day, so in practice the count is what binds.
    """
    directory = directory or snapshot_dir_for(conn)
    removed = {}

    cutoff = (datetime.now() - timedelta(days=detection_days)).strftime("%Y-%m-%dT%H:%M:%S")
    removed["detections"] = conn.execute(
        "DELETE FROM detections WHERE detected_at < ?", (cutoff,)
    ).rowcount

    # Ids first, because the files are named after them and the rows are about
    # to be gone.
    snap_cutoff = (datetime.now() - timedelta(days=snapshot_days)).strftime("%Y-%m-%dT%H:%M:%S")
    doomed = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM snapshots WHERE taken_at < ? OR id NOT IN ("
            " SELECT id FROM snapshots ORDER BY id DESC LIMIT ?)",
            (snap_cutoff, snapshot_max),
        )
    ]
    if doomed:
        conn.executemany(
            "DELETE FROM snapshots WHERE id = ?", [(i,) for i in doomed]
        )
        for snapshot_id in doomed:
            _remove_quietly(snapshot_path(snapshot_id, directory))
    removed["snapshots"] = len(doomed)

    # Detections outlive their images, so the dangling reference is cleared
    # rather than left pointing at a row that is gone.
    conn.execute(
        "UPDATE detections SET snapshot_id = NULL WHERE snapshot_id IS NOT NULL"
        " AND snapshot_id NOT IN (SELECT id FROM snapshots)"
    )

    removed["orphan_files"] = sweep_orphan_files(conn, directory)
    removed["change_log"] = prune_change_log(conn)
    return removed


def checkpoint(conn):
    """Fold the write-ahead log back into the database and truncate it.

    Must run with no transaction open — SQLite answers "database table is
    locked" otherwise — so this is deliberately not part of `prune`, whose
    contract is that the caller commits. The background loop calls it after its
    commit.

    Worth doing at all because the WAL grows until something checkpoints it, and
    because a Home Assistant backup copies the database and its WAL as separate
    files: the smaller the WAL, the smaller the window in which those two
    disagree.
    """
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return None
    except sqlite3.OperationalError as exc:
        # Contended by another connection's reader. It will be checkpointed on
        # the next pass; this is housekeeping, not correctness.
        return str(exc)


def _remove_quietly(path):
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def sweep_orphan_files(conn, directory=None):
    """Delete image files with no row.

    This is what makes the two-step delete self-healing: a crash between
    removing the row and removing the file would otherwise leak an image that
    nothing references and no retention rule can ever find again.
    """
    directory = directory or snapshot_dir_for(conn)
    try:
        names = os.listdir(directory)
    except OSError:
        return 0

    known = {row[0] for row in conn.execute("SELECT id FROM snapshots")}
    removed = 0
    for name in names:
        if not name.endswith(".jpg"):
            continue
        try:
            snapshot_id = int(name[:-4])
        except ValueError:
            continue
        if snapshot_id not in known and _remove_quietly(os.path.join(directory, name)):
            removed += 1
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

    # Reported separately from db_bytes because they are backed up differently:
    # the database goes into every Home Assistant backup and the images
    # deliberately do not. A single "size" would hide that distinction.
    snapshot_bytes = conn.execute(
        "SELECT COALESCE(SUM(bytes), 0) FROM snapshots"
    ).fetchone()[0]

    return {
        "taken_at": now(),
        "db_bytes": db_bytes,
        "snapshot_bytes": snapshot_bytes,
        "max_seq": max_seq(conn),
        "counts": counts,
        "other_counts": other,
        "total": sum(counts.values()),
        "other_total": sum(other.values()),
        "total_all": sum(counts.values()) + sum(other.values()),
    }


# --- reading for the UI and sensors -------------------------------------------


def recent_detections(conn, limit=50, camera=None, date_from=None, date_to=None,
                       labels=None):
    """Recent detections, newest first, optionally by camera, time and label.

    `date_from`/`date_to` are inclusive bounds compared directly against
    `detected_at`, which is stored as local `YYYY-MM-DDTHH:MM:SS`. That format
    sorts lexicographically, so a plain string comparison is an exact
    chronological one — no parsing, and it works whether the caller passes a
    whole timestamp or just a date. The caller is responsible for widening a
    date-only bound to the right edge of the day (see the endpoint); here the
    bounds are used as given. Any filter may be omitted.

    `labels` restricts to those object types (a list); an empty list is treated
    as no restriction, so "show everything" and "show nothing" don't collapse
    into the same call.
    """
    clauses, params = [], []
    if camera:
        clauses.append("camera = ?")
        params.append(camera)
    if date_from:
        clauses.append("detected_at >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("detected_at <= ?")
        params.append(date_to)
    if labels:
        clauses.append("label IN (%s)" % ",".join("?" for _ in labels))
        params.extend(labels)

    sql = "SELECT * FROM detections"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(500, int(limit))))
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def distinct_labels(conn):
    """The object types actually recorded, most frequent first.

    Feeds the UI's object filter so it offers only what has been seen — no
    "giraffe" on a driveway — and puts the labels you care about at the top.
    """
    rows = conn.execute(
        "SELECT label, COUNT(*) n FROM detections GROUP BY label ORDER BY n DESC, label"
    ).fetchall()
    return [row["label"] for row in rows]


def snapshot(conn, snapshot_id, directory=None):
    """Where a snapshot's file is, and what it is — or None.

    Returns None both when the row is unknown and when the file is missing. The
    second is a normal state, not a fault: images are excluded from backups, so
    after a restore every detection is present and none of the pictures are.
    """
    row = conn.execute(
        "SELECT id, width, height, bytes, taken_at FROM snapshots WHERE id = ?",
        (snapshot_id,),
    ).fetchone()
    if row is None:
        return None
    path = snapshot_path(row["id"], directory or snapshot_dir_for(conn))
    if not os.path.exists(path):
        return None
    return {**dict(row), "path": path}


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
