"""Upgrading a database that predates the current schema.

The riskiest code in the add-on, and the least exercised by every other test:
`CREATE TABLE` on a fresh install already has every column, so the
`if "x" not in cols` branches in `_migrate_columns` never fire. Nothing here
was covered, which means nothing proved that a real database — the one someone
has been ticking into for months — survives an upgrade.

Each test builds a table at an older shape, puts representative rows in it,
runs `init_db()` the way a container start does, and asserts both that the new
column arrived and that the old rows still say what they said.
"""
import sqlite3

import pytest

import app as gymapp


@pytest.fixture
def old_db(tmp_path, monkeypatch):
    """An empty database file that init_db() has never seen."""
    path = str(tmp_path / "old.db")
    monkeypatch.setattr(gymapp, "DB_PATH", path)
    return path


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _upgrade(path):
    """Start the app against this file, as a container restart would."""
    gymapp.init_db()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


# --- garmin_daily: the columns added across 1.4x–1.54 -----------------------


def test_garmin_columns_arrive_and_the_nights_survive(old_db):
    """resting_hr, steps, distance and floors were all added to a table that
    already held months of nights."""
    raw = sqlite3.connect(old_db)
    raw.execute("""
        CREATE TABLE garmin_daily (
            day TEXT PRIMARY KEY,
            sleep_seconds INTEGER,
            stress_avg INTEGER,
            body_battery_high INTEGER,
            synced_at TEXT
        )""")
    raw.execute("INSERT INTO garmin_daily (day, sleep_seconds, stress_avg, body_battery_high) "
                "VALUES ('2026-08-01', 27000, 31, 88)")
    raw.commit(); raw.close()

    conn = _upgrade(old_db)

    cols = _cols(conn, "garmin_daily")
    for added in ("resting_hr", "steps", "step_goal", "distance_m", "floors_up", "floors_goal"):
        assert added in cols, f"{added} was not added by the migration"
    row = conn.execute("SELECT * FROM garmin_daily WHERE day = '2026-08-01'").fetchone()
    assert row["sleep_seconds"] == 27000 and row["stress_avg"] == 31
    assert row["body_battery_high"] == 88
    assert row["resting_hr"] is None, "a column with no history must be empty, not zero"


def test_a_sleep_score_column_is_dropped_and_the_nights_keep_everything_else(old_db):
    """1.56.0 dropped sleep_score because the watch never populated it. The
    drop has to take the column and nothing else."""
    raw = sqlite3.connect(old_db)
    raw.execute("""
        CREATE TABLE garmin_daily (
            day TEXT PRIMARY KEY,
            sleep_seconds INTEGER,
            sleep_deep_seconds INTEGER,
            sleep_score INTEGER,
            stress_avg INTEGER,
            resting_hr INTEGER,
            synced_at TEXT
        )""")
    raw.execute("INSERT INTO garmin_daily (day, sleep_seconds, sleep_deep_seconds, "
                "sleep_score, stress_avg, resting_hr) "
                "VALUES ('2026-08-02', 26100, 3600, NULL, 29, 57)")
    raw.commit(); raw.close()

    conn = _upgrade(old_db)

    assert "sleep_score" not in _cols(conn, "garmin_daily")
    row = conn.execute("SELECT * FROM garmin_daily WHERE day = '2026-08-02'").fetchone()
    assert row["sleep_seconds"] == 26100
    assert row["sleep_deep_seconds"] == 3600
    assert row["stress_avg"] == 29
    assert row["resting_hr"] == 57


# --- challenges: scheduling, repeats, scoring, forfeits ---------------------


def test_an_old_challenge_becomes_a_daily_one(old_db):
    """Schedules arrived after challenges did. A challenge from before then
    behaved as daily, so that is what it has to become — anything else
    silently changes which days it was due."""
    raw = sqlite3.connect(old_db)
    raw.execute("""
        CREATE TABLE challenges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            start_date TEXT,
            end_date TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )""")
    raw.execute("INSERT INTO challenges (name, start_date, end_date) "
                "VALUES ('Old habit', '2026-06-01', '2026-07-01')")
    raw.commit(); raw.close()

    conn = _upgrade(old_db)

    cols = _cols(conn, "challenges")
    for added in ("repeat_of", "schedule_kind", "schedule_interval", "schedule_weekdays",
                  "forfeit_unit", "forfeit_from"):
        assert added in cols, f"{added} was not added"
    row = conn.execute("SELECT * FROM challenges WHERE name = 'Old habit'").fetchone()
    assert row["schedule_kind"] == "daily", "an existing challenge changed which days it is due"
    assert row["start_date"] == "2026-06-01" and row["end_date"] == "2026-07-01"
    assert row["schedule_weekdays"] is None


def test_the_upgraded_challenge_is_readable_through_the_api(old_db, monkeypatch, tmp_path):
    """The real test of a migration is not the column list — it is whether the
    app can serve the row afterwards."""
    raw = sqlite3.connect(old_db)
    raw.execute("""
        CREATE TABLE challenges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            start_date TEXT,
            end_date TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )""")
    raw.execute("INSERT INTO challenges (name, start_date) VALUES ('Old habit', '2026-06-01')")
    raw.commit(); raw.close()
    monkeypatch.setattr(gymapp, "OPTIONS_PATH", str(tmp_path / "options.json"))
    gymapp.init_db()

    gymapp.app.testing = True
    with gymapp.app.test_client() as client:
        client.environ_base["HTTP_X_REMOTE_USER_ID"] = "test-ingress-user"
        res = client.get("/api/challenges")

    assert res.status_code == 200, res.get_json()
    names = [c["name"] for c in res.get_json()]
    assert "Old habit" in names


# --- change_log -------------------------------------------------------------


def test_the_change_log_gains_an_actor_and_keeps_its_history(old_db):
    """Existing rows are marked 'unknown' rather than claimed by the migration.

    They were null until 1.59.1, which read as "no idea" but was not stable:
    the commit ending the migration stamps every null-actor row with its own
    actor, and the column had just been added, so it was null on the whole
    table — attributing the entire history to 'migration'.
    """
    raw = sqlite3.connect(old_db)
    raw.execute("""
        CREATE TABLE change_log (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT NOT NULL,
            row_id TEXT NOT NULL,
            op TEXT NOT NULL,
            changed_at TEXT NOT NULL
        )""")
    raw.execute("INSERT INTO change_log (table_name, row_id, op, changed_at) "
                "VALUES ('weight_logs', '1', 'I', '2026-06-01T08:00:00')")
    raw.commit(); raw.close()

    conn = _upgrade(old_db)

    assert "actor" in _cols(conn, "change_log")
    row = conn.execute("SELECT * FROM change_log WHERE row_id = '1'").fetchone()
    assert row["table_name"] == "weight_logs" and row["op"] == "I"
    assert row["actor"] == "unknown"
    assert row["actor"] != "migration", "the migration claimed a change it did not make"


# --- workout_logs: hr_scope, added in 1.58.0 --------------------------------


def test_existing_heart_rates_are_stamped_as_session_readings(old_db):
    """1.58.0 made heart rate per-exercise. Readings taken before it are
    session-wide, and the column exists to say so — a mixed column that does
    not distinguish them cannot be reasoned about later."""
    raw = sqlite3.connect(old_db)
    raw.execute("""
        CREATE TABLE workout_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            exercise_id INTEGER NOT NULL,
            sets INTEGER,
            reps INTEGER,
            weight_kg REAL,
            duration_sec INTEGER,
            notes TEXT,
            source TEXT NOT NULL DEFAULT 'manual',
            ts_exact INTEGER NOT NULL DEFAULT 0,
            hr_avg INTEGER,
            hr_max INTEGER
        )""")
    raw.execute("INSERT INTO workout_logs (ts, exercise_id, sets, reps, hr_avg, hr_max, ts_exact) "
                "VALUES ('2026-08-03T18:00:00', 1, 3, 8, 131, 141, 1)")
    raw.execute("INSERT INTO workout_logs (ts, exercise_id, sets, reps, ts_exact) "
                "VALUES ('2026-08-03T18:10:00', 1, 3, 8, 1)")
    raw.commit(); raw.close()

    conn = _upgrade(old_db)

    assert "hr_scope" in _cols(conn, "workout_logs")
    rows = list(conn.execute("SELECT hr_avg, hr_scope FROM workout_logs ORDER BY ts"))
    assert rows[0]["hr_avg"] == 131
    assert rows[0]["hr_scope"] == "session", "an existing reading was not stamped"
    assert rows[1]["hr_scope"] is None, "a row with no reading was given a scope"


# --- the whole thing, twice -------------------------------------------------


def test_upgrading_twice_changes_nothing_the_second_time(old_db):
    """Every container restart runs this. It has to be idempotent, or a restart
    is a data migration."""
    raw = sqlite3.connect(old_db)
    raw.execute("""
        CREATE TABLE garmin_daily (
            day TEXT PRIMARY KEY, sleep_seconds INTEGER, stress_avg INTEGER,
            body_battery_high INTEGER, synced_at TEXT
        )""")
    raw.execute("INSERT INTO garmin_daily (day, sleep_seconds) VALUES ('2026-08-04', 25200)")
    raw.commit(); raw.close()

    gymapp.init_db()
    first = sqlite3.connect(old_db)
    first.row_factory = sqlite3.Row
    cols_after_one = _cols(first, "garmin_daily")
    rows_after_one = [dict(r) for r in first.execute("SELECT * FROM garmin_daily")]
    first.close()

    gymapp.init_db()          # a restart
    second = sqlite3.connect(old_db)
    second.row_factory = sqlite3.Row
    assert _cols(second, "garmin_daily") == cols_after_one
    assert len(list(second.execute("SELECT * FROM garmin_daily"))) == len(rows_after_one)
    assert second.execute(
        "SELECT sleep_seconds FROM garmin_daily WHERE day = '2026-08-04'"
    ).fetchone()["sleep_seconds"] == 25200


def test_a_fresh_install_needs_no_migration(old_db):
    """The other half of idempotence: CREATE TABLE already has every column, so
    a new install must reach the same schema an upgraded one does."""
    gymapp.init_db()
    fresh = sqlite3.connect(old_db)
    fresh_cols = _cols(fresh, "workout_logs")
    fresh.close()

    assert "hr_scope" in fresh_cols
    assert {"hr_avg", "hr_max", "session_start", "session_end"} <= fresh_cols
