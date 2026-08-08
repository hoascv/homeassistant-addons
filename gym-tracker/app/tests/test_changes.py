"""The change feed a downstream pipeline reads for incremental refresh."""
import app as gymapp


def _changes(client, since=0, **kw):
    qs = "&".join(f"{k}={v}" for k, v in {"since": since, **kw}.items())
    return client.get(f"/api/changes?{qs}").get_json()


def _seq(conn):
    return conn.execute("SELECT COALESCE(MAX(seq), 0) n FROM change_log").fetchone()["n"]


# --- The triggers -----------------------------------------------------------


def test_insert_update_delete_are_each_recorded_once(client, conn):
    start = _seq(conn)
    wid = client.post("/api/weight", json={"weight_kg": 100.0}).get_json()["id"]
    client.put(f"/api/weight/{wid}", json={"weight_kg": 100.5})
    client.delete(f"/api/weight/{wid}")

    rows = conn.execute(
        "SELECT seq, table_name, row_id, op FROM change_log WHERE seq > ? "
        "AND table_name = 'weight_logs' ORDER BY seq",
        (start,),
    ).fetchall()
    assert [r["op"] for r in rows] == ["I", "U", "D"]
    assert {r["row_id"] for r in rows} == {str(wid)}
    assert [r["seq"] for r in rows] == sorted(r["seq"] for r in rows)  # monotonic


def test_a_delete_is_visible_with_no_row(client, conn):
    wid = client.post("/api/weight", json={"weight_kg": 100.0}).get_json()["id"]
    before = _seq(conn)
    client.delete(f"/api/weight/{wid}")

    entry = _changes(client, since=before)["changes"][0]
    assert entry["op"] == "D"
    assert entry["table"] == "weight_logs" and entry["row_id"] == str(wid)
    # The row is gone, which is exactly what an updated_at column could never say.
    assert entry["row"] is None


def test_an_update_carries_the_current_row(client, conn):
    wid = client.post("/api/weight", json={"weight_kg": 100.0}).get_json()["id"]
    before = _seq(conn)
    client.put(f"/api/weight/{wid}", json={"weight_kg": 101.25, "notes": "after coffee"})

    entry = _changes(client, since=before)["changes"][0]
    assert entry["op"] == "U"
    assert entry["row"]["weight_kg"] == 101.25
    assert entry["row"]["notes"] == "after coffee"


def test_a_text_primary_key_is_recorded(conn, monkeypatch):
    # garmin_daily is keyed by day, not an integer id.
    from datetime import date

    day = date.today().isoformat()
    conn.execute("INSERT INTO garmin_daily (day, stress_avg) VALUES (?, 30)", (day,))
    conn.commit()
    row = conn.execute(
        "SELECT row_id, op FROM change_log WHERE table_name = 'garmin_daily' ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    assert row["row_id"] == day and row["op"] == "I"


def test_internal_bookkeeping_is_not_in_the_feed(client, conn):
    before = _seq(conn)
    gymapp._set_app_state(conn, "garmin_last_sync", "2026-07-31T20:00:00")
    conn.commit()
    # app_state churns on every sync and means nothing downstream.
    assert _changes(client, since=before)["changes"] == []


def test_un_ticking_a_challenge_item_reports_both_deletions(client, conn):
    ex = conn.execute("SELECT id FROM exercises WHERE archived = 0 LIMIT 1").fetchone()["id"]
    item = client.post("/api/challenge/items", json={
        "item_type": "exercise", "exercise_id": ex, "target_reps": 10,
    }).get_json()["id"]
    client.post("/api/challenge/toggle", json={"item_id": item})
    before = _seq(conn)
    client.post("/api/challenge/toggle", json={"item_id": item})  # un-tick

    ops = {(c["table"], c["op"]) for c in _changes(client, since=before)["changes"]}
    # Both the completion and the workout log it created are gone.
    assert ("challenge_completions", "D") in ops
    assert ("workout_logs", "D") in ops


# --- The endpoints ----------------------------------------------------------


def test_since_is_exclusive_and_ordered(client, conn):
    client.post("/api/weight", json={"weight_kg": 100.0})
    mid = _seq(conn)
    client.post("/api/weight", json={"weight_kg": 101.0})

    payload = _changes(client, since=mid)
    assert payload["changes"]
    assert all(c["seq"] > mid for c in payload["changes"])
    assert [c["seq"] for c in payload["changes"]] == sorted(c["seq"] for c in payload["changes"])
    assert payload["max_seq"] == _seq(conn)


def test_limit_is_honoured_and_bounded(client):
    for i in range(5):
        client.post("/api/weight", json={"weight_kg": 100 + i})
    assert len(_changes(client, since=0, limit=2)["changes"]) == 2
    # Absurd values are clamped rather than rejected.
    assert len(_changes(client, since=0, limit=99999)["changes"]) > 0


def test_bad_query_values_are_a_bad_request(client):
    assert client.get("/api/changes?since=abc").status_code == 400
    assert client.get("/api/changes?limit=abc").status_code == 400


def test_export_is_a_snapshot_with_the_sequence_it_matches(client, conn):
    client.post("/api/weight", json={"weight_kg": 100.0})
    payload = client.get("/api/export").get_json()

    assert payload["max_seq"] == _seq(conn)
    assert set(payload["tables"]) == set(gymapp.TRACKED_TABLES)
    assert len(payload["tables"]["weight_logs"]) == conn.execute(
        "SELECT COUNT(*) n FROM weight_logs"
    ).fetchone()["n"]
    # Nothing appended since the snapshot, so the feed from it is empty.
    assert _changes(client, since=payload["max_seq"])["changes"] == []


def test_export_leaves_out_image_bytes(client, conn):
    import io

    from test_exercises import PNG_1PX

    eid = client.get("/api/exercises").get_json()[0]["exercises"][0]["id"]
    client.post(
        f"/api/exercises/{eid}/image",
        data={"file": (io.BytesIO(PNG_1PX), "p.png")},
        content_type="multipart/form-data",
    )
    row = client.get("/api/export").get_json()["tables"]["exercise_images"][0]
    assert "image" not in row  # bytes are fetched from the image endpoint
    assert row["exercise_id"] == eid and row["mime"] == "image/png"


# --- Pruning ----------------------------------------------------------------


def test_pruning_keeps_the_latest_entry_for_every_row(client, conn):
    wid = client.post("/api/weight", json={"weight_kg": 100.0}).get_json()["id"]
    client.put(f"/api/weight/{wid}", json={"weight_kg": 100.5})
    conn.execute("UPDATE change_log SET changed_at = '2020-01-01T00:00:00'")
    conn.commit()

    gymapp._prune_change_log(conn, keep_days=30)
    conn.commit()

    kept = conn.execute(
        "SELECT table_name, row_id, COUNT(*) n FROM change_log GROUP BY table_name, row_id"
    ).fetchall()
    # Old entries collapse to one per row rather than vanishing entirely: a
    # consumer rebuilding from the feed still needs each row's latest state.
    assert kept and all(r["n"] == 1 for r in kept)


def test_a_watermark_below_the_retained_window_asks_for_a_reload(client, conn):
    client.post("/api/weight", json={"weight_kg": 100.0})
    conn.execute("DELETE FROM change_log WHERE seq < (SELECT MAX(seq) FROM change_log)")
    conn.commit()

    lo = conn.execute("SELECT MIN(seq) n FROM change_log").fetchone()["n"]
    assert _changes(client, since=lo)["full_reload_required"] is False
    if lo > 2:
        assert _changes(client, since=1)["full_reload_required"] is True


# --- Token auth -------------------------------------------------------------


def test_a_token_authenticates_a_pipeline_that_has_no_ingress_session(client, set_options):
    set_options(restrict_to_user_ids="abc123", api_token="s3cret")

    assert client.get("/api/changes").status_code == 403  # no session, no token
    assert client.get("/api/changes", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.get("/api/changes", headers={"Authorization": "Bearer wrong"}).status_code == 403
    assert client.get("/api/changes", headers={"Authorization": "s3cret"}).status_code == 403


def test_no_token_configured_means_no_token_access(client, set_options):
    set_options(restrict_to_user_ids="abc123")
    # An empty option must not turn into "any empty bearer works".
    assert client.get("/api/changes", headers={"Authorization": "Bearer "}).status_code == 403
    assert client.get("/api/changes").status_code == 403


def test_export_names_the_key_column_for_every_table(client):
    """A consumer can't infer which column identifies a row: jsonify sorts the
    keys, so the id is rarely first — and for challenge_completions the first
    key (day) repeats across rows, which would collapse them on merge."""
    payload = client.get("/api/export").get_json()

    assert payload["keys"] == dict(gymapp.TRACKED_TABLES)
    for table, rows in payload["tables"].items():
        key = payload["keys"][table]
        assert all(key in row for row in rows), f"{table} rows lack {key}"
        ids = [str(row[key]) for row in rows]
        assert len(ids) == len(set(ids)), f"{table}: key column is not unique"


def test_a_token_with_non_ascii_characters_still_works(client, set_options):
    """compare_digest refuses non-ASCII str — a passphrase with an accent in it
    used to raise inside before_request, turning every authenticated request
    into a 500 instead of letting it through."""
    set_options(restrict_to_user_ids="abc123", api_token="café-très-sécurisé")

    assert client.get(
        "/api/changes", headers={"Authorization": "Bearer café-très-sécurisé"}
    ).status_code == 200
    # A near miss is still a clean refusal, not an error.
    assert client.get(
        "/api/changes", headers={"Authorization": "Bearer cafe-tres-securise"}
    ).status_code == 403


# --- Who made the change ----------------------------------------------------


def _actors(conn, since=0):
    return [
        (r["table_name"], r["op"], r["actor"])
        for r in conn.execute(
            "SELECT table_name, op, actor FROM change_log WHERE seq > ? ORDER BY seq", (since,)
        )
    ]


def test_a_request_is_recorded_as_a_user(client, conn):
    start = _seq(conn)
    client.post("/api/weight", json={"weight_kg": 100.4})

    assert _actors(conn, start) == [("weight_logs", "I", "user")]


def test_the_background_loop_is_recorded_as_automation(conn, monkeypatch):
    from datetime import date

    start = _seq(conn)
    # _db_connect_standalone is what the loop uses, and what decides the actor.
    standalone = gymapp._db_connect_standalone()
    try:
        assert standalone.actor == "automation"
        standalone.execute(
            "INSERT INTO garmin_daily (day, stress_avg) VALUES (?, 31)", (date.today().isoformat(),)
        )
        standalone.commit()
    finally:
        standalone.close()

    assert _actors(conn, start) == [("garmin_daily", "I", "automation")]


def test_a_migration_is_recorded_as_a_migration(client, conn, db_path):
    # An entry with a heart rate read from a placeholder timestamp: the
    # one-off fix that clears these runs inside init_db.
    conn.execute("INSERT OR IGNORE INTO exercises (id, name) VALUES (1, 'Push-up')")
    conn.execute(
        "INSERT INTO workout_logs (ts, exercise_id, source, ts_exact, hr_avg, hr_max) "
        "VALUES ('2026-07-09T12:00:00', 1, 'manual', 0, 78, 81)"
    )
    gymapp._set_app_state(conn, "placeholder_hr_cleared", "")
    conn.commit()
    start = _seq(conn)

    gymapp.init_db()

    recorded = _actors(conn, start)
    assert recorded, "the migration made no recorded change"
    assert {actor for _, _, actor in recorded} == {"migration"}
    assert ("workout_logs", "U", "migration") in recorded


def test_a_connection_that_claims_nothing_says_so(conn, db_path):
    import sqlite3

    start = _seq(conn)
    plain = sqlite3.connect(db_path, factory=gymapp._AttributedConnection)
    try:
        # Deliberately sets no actor. It must not inherit the last one used —
        # attribution that is confidently wrong is worse than none.
        plain.execute("INSERT INTO weight_logs (ts, weight_kg, ts_exact) VALUES ('x', 1, 0)")
        plain.commit()
    finally:
        plain.close()

    assert _actors(conn, start) == [("weight_logs", "I", "unknown")]


def test_the_same_row_touched_twice_keeps_both_actors(client, conn):
    wid = client.post("/api/weight", json={"weight_kg": 100.0}).get_json()["id"]
    start = _seq(conn)

    standalone = gymapp._db_connect_standalone()
    try:
        standalone.execute("UPDATE weight_logs SET notes = 'by the loop' WHERE id = ?", (wid,))
        standalone.commit()
    finally:
        standalone.close()
    client.put(f"/api/weight/{wid}", json={"weight_kg": 100.6})

    # The history keeps both, which a single column on the table could not.
    assert _actors(conn, start) == [
        ("weight_logs", "U", "automation"),
        ("weight_logs", "U", "user"),
    ]


def test_the_feed_carries_the_actor(client, conn):
    start = _seq(conn)
    client.post("/api/weight", json={"weight_kg": 100.9})

    entry = _changes(client, since=start)["changes"][0]
    assert entry["actor"] == "user"


# --- /api/stats -------------------------------------------------------------


def test_stats_counts_match_the_database(client, conn):
    client.post("/api/weight", json={"weight_kg": 100.0})
    payload = client.get("/api/stats").get_json()

    assert set(payload["counts"]) == set(gymapp.TRACKED_TABLES)
    for table, reported in payload["counts"].items():
        actual = conn.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"]
        assert reported == actual, table


def test_stats_agrees_with_export_without_serialising_it(client):
    """The whole point of the endpoint: the same numbers as /api/export, for a
    fraction of the bytes."""
    stats = client.get("/api/stats").get_json()
    export = client.get("/api/export").get_json()

    assert stats["max_seq"] == export["max_seq"]
    for table, rows in export["tables"].items():
        assert stats["counts"][table] == len(rows), table


def test_stats_total_ignores_unavailable_tables(client):
    payload = client.get("/api/stats").get_json()
    assert payload["total"] == sum(n for n in payload["counts"].values() if n is not None)


def test_stats_reports_the_database_size(client):
    assert client.get("/api/stats").get_json()["db_bytes"] > 0
