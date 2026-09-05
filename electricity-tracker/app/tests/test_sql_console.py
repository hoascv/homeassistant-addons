"""The read-only SQL console.

It exists because some questions are not a chart. "Why is 29 August 2.35 kWh
short" is a look at saveeye_samples for a counter reset, and without this the
only route to that answer is downloading a backup and opening it elsewhere.

Which makes the tests that matter here the ones about what it refuses. The
guarantee is not that the endpoint inspects the SQL carefully — it is that the
connection is opened `mode=ro`, so SQLite refuses a write however it is
spelled. The string checks on top only exist to give a better message.
"""
import sqlite3


def _rows(client, sql):
    response = client.post("/api/debug/query", json={"sql": sql})
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def test_a_select_comes_back_with_columns_and_rows(client, conn):
    conn.execute("INSERT INTO app_state (key, value) VALUES ('a', '1')")
    conn.commit()
    body = _rows(client, "SELECT key, value FROM app_state")
    assert body["columns"] == ["key", "value"]
    assert ["a", "1"] in body["rows"]


def test_null_comes_back_as_null_not_as_empty_string(client):
    """A console that draws them the same way is lying about what is stored."""
    assert _rows(client, "SELECT NULL AS blank")["rows"] == [[None]]


# --- what it will not do ------------------------------------------------------


def test_every_kind_of_write_is_refused(client, conn, db_path):
    conn.execute("INSERT INTO app_state (key, value) VALUES ('keep', 'me')")
    conn.commit()
    for sql in (
        "DELETE FROM app_state",
        "DROP TABLE app_state",
        "UPDATE app_state SET value = 'gone'",
        "INSERT INTO app_state (key, value) VALUES ('x', 'y')",
        "CREATE TABLE sneaky (a)",
        "ALTER TABLE app_state ADD COLUMN c",
    ):
        assert client.post("/api/debug/query", json={"sql": sql}).status_code == 400, sql

    after = sqlite3.connect(db_path)
    assert after.execute("SELECT value FROM app_state WHERE key = 'keep'").fetchone()[0] == "me"
    after.close()


def test_a_write_hidden_behind_a_second_statement_does_not_run(client, conn, db_path):
    """execute() runs one statement, and mode=ro would refuse the write anyway.
    Both are true; this pins that neither has quietly stopped being true."""
    conn.execute("INSERT INTO app_state (key, value) VALUES ('keep', 'me')")
    conn.commit()
    client.post("/api/debug/query", json={"sql": "SELECT 1; DROP TABLE app_state"})
    after = sqlite3.connect(db_path)
    assert after.execute("SELECT value FROM app_state WHERE key = 'keep'").fetchone()[0] == "me"
    after.close()


def test_attach_is_refused_by_name(client):
    """A read-only main database does not stop ATTACH reaching another file."""
    body = client.post("/api/debug/query",
                       json={"sql": "ATTACH DATABASE '/data/other.db' AS other"})
    assert body.status_code == 400
    assert "ATTACH" in body.get_json()["error"]


def test_pragma_is_refused(client):
    """It changes how the engine behaves rather than asking it anything."""
    assert client.post("/api/debug/query",
                       json={"sql": "PRAGMA journal_mode = DELETE"}).status_code == 400


def test_the_refusal_says_what_was_wrong_with_it(client):
    error = client.post("/api/debug/query", json={"sql": "DELETE FROM app_state"}).get_json()["error"]
    assert "SELECT" in error and "DELETE" in error


def test_nothing_at_all_is_a_400_not_a_crash(client):
    assert client.post("/api/debug/query", json={"sql": "   "}).status_code == 400
    assert client.post("/api/debug/query", json={}).status_code == 400


def test_broken_sql_comes_back_as_its_own_message(client):
    body = client.post("/api/debug/query", json={"sql": "SELECT * FROM no_such_table"})
    assert body.status_code == 400
    assert "no_such_table" in body.get_json()["error"]


# --- the things that make it usable -------------------------------------------


def test_a_leading_comment_does_not_read_as_a_forbidden_keyword(client):
    """`-- look at the samples\\nSELECT ...` starting with a dash and being
    refused for the wrong reason is a maddening thing to debug in a text box."""
    assert _rows(client, "-- why is the 29th short?\nSELECT 1 AS n")["rows"] == [[1]]
    assert _rows(client, "/* a note */ SELECT 2 AS n")["rows"] == [[2]]


def test_with_and_explain_are_allowed(client):
    assert _rows(client, "WITH x AS (SELECT 1 AS n) SELECT n FROM x")["rows"] == [[1]]
    assert client.post("/api/debug/query",
                       json={"sql": "EXPLAIN SELECT 1"}).status_code == 200


def test_a_huge_result_is_capped_and_says_so(client, conn):
    """Returning a million rows to a phone is not an answer."""
    import app as electricityapp
    limit = electricityapp.SQL_MAX_ROWS
    body = _rows(client, f"WITH RECURSIVE c(n) AS "
                         f"(SELECT 1 UNION ALL SELECT n + 1 FROM c WHERE n < {limit + 50}) "
                         f"SELECT n FROM c")
    assert len(body["rows"]) == limit
    assert body["truncated"] is True


def test_a_runaway_query_is_stopped_rather_than_hanging_the_add_on(client, monkeypatch):
    """One cross join left running would otherwise hold the request thread for
    as long as it takes, on a box that is also serving the panel."""
    import app as electricityapp
    monkeypatch.setattr(electricityapp, "SQL_MAX_STEPS", 1000)
    body = client.post("/api/debug/query", json={"sql":
        "WITH RECURSIVE c(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM c) SELECT COUNT(*) FROM c"})
    assert body.status_code == 400
    assert "too long" in body.get_json()["error"]


def test_the_table_list_says_what_there_is_to_query(client, conn):
    """A console with no schema in front of it is one you use by guessing."""
    conn.execute("INSERT INTO app_state (key, value) VALUES ('a', '1')")
    conn.commit()
    tables = client.get("/api/debug/tables").get_json()["tables"]
    by_name = {t["name"]: t for t in tables}
    assert "app_state" in by_name
    assert by_name["app_state"]["rows"] == 1
    assert "key" in by_name["app_state"]["columns"]
    assert not any(t["name"].startswith("sqlite_") for t in tables)


def test_the_console_needs_the_same_key_as_everything_else(direct_client):
    """No ingress header and no token is no access, the way it is for every
    other route — this one more than most."""
    assert direct_client.post("/api/debug/query", json={"sql": "SELECT 1"}).status_code == 401
    assert direct_client.get("/api/debug/tables").status_code == 401
