"""The change feed an external pipeline reads for incremental refresh."""
import app as coopapp


def _changes(client, since=0, **kw):
    qs = "&".join(f"{k}={v}" for k, v in {"since": since, **kw}.items())
    return client.get(f"/api/changes?{qs}").get_json()


def _seq(conn):
    return conn.execute("SELECT COALESCE(MAX(seq), 0) n FROM change_log").fetchone()["n"]


# --- The triggers -----------------------------------------------------------


def test_insert_update_delete_are_each_recorded_once(client, conn):
    start = _seq(conn)
    log_id = client.post("/api/log", json={"type": "egg", "count": 3}).get_json()["id"]
    client.put(f"/api/entries/{log_id}", json={"count": 5})
    client.delete(f"/api/entries/{log_id}")

    rows = conn.execute(
        "SELECT op, row_id, seq FROM change_log WHERE seq > ? AND table_name = 'logs' ORDER BY seq",
        (start,),
    ).fetchall()
    assert [r["op"] for r in rows] == ["I", "U", "D"]
    assert {r["row_id"] for r in rows} == {str(log_id)}
    assert [r["seq"] for r in rows] == sorted(r["seq"] for r in rows)


def test_a_delete_is_visible_with_no_row(client, conn):
    log_id = client.post("/api/log", json={"type": "egg", "count": 2}).get_json()["id"]
    before = _seq(conn)
    client.delete(f"/api/entries/{log_id}")

    entry = _changes(client, since=before)["changes"][0]
    assert entry["op"] == "D" and entry["table"] == "logs"
    assert entry["row_id"] == str(log_id)
    # The row is gone — which a "last modified" column could never tell you.
    assert entry["row"] is None


def test_an_update_carries_the_current_row(client, conn):
    log_id = client.post("/api/log", json={"type": "egg", "count": 2}).get_json()["id"]
    before = _seq(conn)
    client.put(f"/api/entries/{log_id}", json={"count": 7})

    entry = _changes(client, since=before)["changes"][0]
    assert entry["op"] == "U" and entry["row"]["count"] == 7


def test_internal_tables_are_not_in_the_feed(client, conn):
    before = _seq(conn)
    conn.execute("INSERT OR REPLACE INTO app_state (key, value) VALUES ('x', 'y')")
    conn.execute(
        "INSERT INTO egg_vision_samples (created_at, photo, image_width, image_height, "
        "original_detection, corrected_result) VALUES ('2026-07-31T10:00:00', X'00', 1, 1, '{}', '{}')"
    )
    conn.commit()
    # Bookkeeping and ML training material: this app's machinery, not data to
    # analyse.
    assert _changes(client, since=before)["changes"] == []


# --- The endpoints ----------------------------------------------------------


def test_export_is_a_snapshot_with_the_sequence_it_matches(client, conn):
    client.post("/api/log", json={"type": "egg", "count": 3})
    payload = client.get("/api/export").get_json()

    assert payload["max_seq"] == _seq(conn)
    assert set(payload["tables"]) == set(coopapp.TRACKED_TABLES)
    assert len(payload["tables"]["logs"]) == conn.execute(
        "SELECT COUNT(*) n FROM logs"
    ).fetchone()["n"]
    assert _changes(client, since=payload["max_seq"])["changes"] == []


def test_export_leaves_out_a_chicken_photo(client, conn):
    conn.execute(
        "INSERT INTO chickens (name, status, photo) VALUES ('Henrietta', 'active', X'89504E47')"
    )
    conn.commit()
    row = [c for c in client.get("/api/export").get_json()["tables"]["chickens"]
           if c["name"] == "Henrietta"][0]
    assert "photo" not in row  # bytes come from the photo endpoint
    assert row["status"] == "active"


def test_since_is_exclusive_and_limit_is_honoured(client, conn):
    client.post("/api/log", json={"type": "egg", "count": 1})
    mid = _seq(conn)
    for i in range(3):
        client.post("/api/log", json={"type": "egg", "count": i + 1})

    payload = _changes(client, since=mid)
    assert payload["changes"] and all(c["seq"] > mid for c in payload["changes"])
    assert len(_changes(client, since=0, limit=2)["changes"]) == 2


def test_bad_query_values_are_a_bad_request(client):
    assert client.get("/api/changes?since=abc").status_code == 400
    assert client.get("/api/changes?limit=abc").status_code == 400


def test_pruning_keeps_the_latest_entry_for_every_row(client, conn):
    log_id = client.post("/api/log", json={"type": "egg", "count": 1}).get_json()["id"]
    client.put(f"/api/entries/{log_id}", json={"count": 2})
    conn.execute("UPDATE change_log SET changed_at = '2020-01-01T00:00:00'")
    conn.commit()

    coopapp._prune_change_log(conn, keep_days=30)
    conn.commit()

    kept = conn.execute(
        "SELECT COUNT(*) n FROM change_log GROUP BY table_name, row_id"
    ).fetchall()
    assert kept and all(r["n"] == 1 for r in kept)


# --- Token auth -------------------------------------------------------------


def test_a_token_authenticates_a_pipeline_with_no_ingress_session(client, set_options):
    set_options(restrict_to_user_ids="abc123", api_token="s3cret")

    assert client.get("/api/changes").status_code == 403
    assert client.get("/api/changes", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.get("/api/changes", headers={"Authorization": "Bearer wrong"}).status_code == 403


def test_no_token_configured_means_no_token_access(client, set_options):
    set_options(restrict_to_user_ids="abc123")
    assert client.get("/api/changes", headers={"Authorization": "Bearer "}).status_code == 403
    assert client.get("/api/changes").status_code == 403


def test_export_names_the_key_column_for_every_table(client):
    """A consumer can't infer which column identifies a row: jsonify sorts the
    keys, so the id is rarely the first one in the payload."""
    payload = client.get("/api/export").get_json()

    assert payload["keys"] == dict(coopapp.TRACKED_TABLES)
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
