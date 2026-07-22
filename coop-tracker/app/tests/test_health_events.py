import io
import sqlite3

import app as coopapp


def _add_chicken(client, name="Henrietta"):
    return client.post("/api/chickens", json={"name": name}).get_json()["id"]


def test_new_chicken_has_no_health_events(client):
    chicken_id = _add_chicken(client)
    assert client.get(f"/api/chickens/{chicken_id}/health").get_json() == []


def test_health_endpoints_404_for_unknown_chicken(client):
    assert client.get("/api/chickens/999/health").status_code == 404
    res = client.post(
        "/api/chickens/999/health",
        json={"event_type": "vet_visit", "event_date": "2026-07-01"},
    )
    assert res.status_code == 404


def test_add_one_event_of_each_type(client):
    chicken_id = _add_chicken(client)
    for event_type in coopapp.HEALTH_EVENT_TYPES:
        payload = {"event_type": event_type, "event_date": "2026-07-01"}
        if event_type == "weight":
            payload["weight_grams"] = 1900
        res = client.post(f"/api/chickens/{chicken_id}/health", json=payload)
        assert res.status_code == 201, event_type
        assert res.get_json()["event_type"] == event_type

    events = client.get(f"/api/chickens/{chicken_id}/health").get_json()
    assert {e["event_type"] for e in events} == set(coopapp.HEALTH_EVENT_TYPES)


def test_add_event_rejects_unknown_type(client):
    chicken_id = _add_chicken(client)
    res = client.post(
        f"/api/chickens/{chicken_id}/health",
        json={"event_type": "abduction", "event_date": "2026-07-01"},
    )
    assert res.status_code == 400


def test_add_event_requires_valid_date(client):
    chicken_id = _add_chicken(client)
    missing = client.post(
        f"/api/chickens/{chicken_id}/health", json={"event_type": "vet_visit"}
    )
    assert missing.status_code == 400
    invalid = client.post(
        f"/api/chickens/{chicken_id}/health",
        json={"event_type": "vet_visit", "event_date": "not-a-date"},
    )
    assert invalid.status_code == 400
    assert invalid.get_json()["error"] == "invalid event_date"


def test_weight_event_requires_positive_numeric_weight(client):
    chicken_id = _add_chicken(client)
    for weight_grams in (None, "heavy", 0, -100):
        payload = {"event_type": "weight", "event_date": "2026-07-01"}
        if weight_grams is not None:
            payload["weight_grams"] = weight_grams
        res = client.post(f"/api/chickens/{chicken_id}/health", json=payload)
        assert res.status_code == 400, weight_grams


def test_weight_is_optional_on_other_event_types(client):
    chicken_id = _add_chicken(client)
    res = client.post(
        f"/api/chickens/{chicken_id}/health",
        json={"event_type": "observation", "event_date": "2026-07-01", "weight_grams": 1850},
    )
    assert res.status_code == 201
    assert res.get_json()["weight_grams"] == 1850


def test_notes_and_created_at_persisted(client):
    chicken_id = _add_chicken(client)
    res = client.post(
        f"/api/chickens/{chicken_id}/health",
        json={"event_type": "vet_visit", "event_date": "2026-07-01", "notes": "annual checkup"},
    )
    body = res.get_json()
    assert body["notes"] == "annual checkup"
    assert body["created_at"]

    events = client.get(f"/api/chickens/{chicken_id}/health").get_json()
    assert events[0]["notes"] == "annual checkup"


def test_events_listed_newest_event_date_first(client):
    chicken_id = _add_chicken(client)
    for event_date in ("2026-05-01", "2026-07-01", "2026-06-01"):
        client.post(
            f"/api/chickens/{chicken_id}/health",
            json={"event_type": "observation", "event_date": event_date},
        )
    events = client.get(f"/api/chickens/{chicken_id}/health").get_json()
    assert [e["event_date"] for e in events] == ["2026-07-01", "2026-06-01", "2026-05-01"]


def test_events_scoped_to_their_chicken(client):
    henrietta = _add_chicken(client, "Henrietta")
    betty = _add_chicken(client, "Betty")
    client.post(
        f"/api/chickens/{henrietta}/health",
        json={"event_type": "vet_visit", "event_date": "2026-07-01"},
    )
    assert client.get(f"/api/chickens/{betty}/health").get_json() == []


def test_delete_event(client):
    chicken_id = _add_chicken(client)
    event = client.post(
        f"/api/chickens/{chicken_id}/health",
        json={"event_type": "vet_visit", "event_date": "2026-07-01"},
    ).get_json()

    res = client.delete(f"/api/health-events/{event['id']}")
    assert res.status_code == 204
    assert client.get(f"/api/chickens/{chicken_id}/health").get_json() == []


def test_deleting_chicken_removes_its_events(client, conn):
    chicken_id = _add_chicken(client)
    client.post(
        f"/api/chickens/{chicken_id}/health",
        json={"event_type": "vet_visit", "event_date": "2026-07-01"},
    )

    client.delete(f"/api/chickens/{chicken_id}")

    remaining = conn.execute(
        "SELECT COUNT(*) AS n FROM health_events WHERE chicken_id = ?", (chicken_id,)
    ).fetchone()["n"]
    assert remaining == 0


def test_restoring_pre_health_events_backup_recreates_the_table(client, tmp_path):
    # A backup taken before health_events existed: restoring it must still
    # leave a working health-events feature, because api_restore re-runs
    # init_db() after the swap.
    old_backup = tmp_path / "old.db"
    conn = sqlite3.connect(old_backup)
    conn.execute(
        """
        CREATE TABLE logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            ts TEXT NOT NULL,
            count INTEGER,
            food_type TEXT,
            amount TEXT,
            notes TEXT
        )
        """
    )
    conn.commit()
    conn.close()

    res = client.post(
        "/api/restore",
        data={"file": (io.BytesIO(old_backup.read_bytes()), "backup.db")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200

    chicken_id = _add_chicken(client)
    res = client.post(
        f"/api/chickens/{chicken_id}/health",
        json={"event_type": "vet_visit", "event_date": "2026-07-01"},
    )
    assert res.status_code == 201
