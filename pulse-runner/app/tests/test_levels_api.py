"""Level CRUD: listing, ownership, and official-level immutability."""
import json


def _valid_payload(**overrides):
    payload = {
        "name": "My Level",
        "start_mode": "cube",
        "scroll_speed": 8,
        "background": "grid-blue",
        "length_units": 30,
        "objects": [
            {"type": "block", "x": 0, "y": 0, "w": 15, "h": 1},
            {"type": "spike", "x": 6, "y": 1, "w": 1, "h": 1},
        ],
    }
    payload.update(overrides)
    return payload


def test_list_levels_includes_seeded_official_levels(client):
    res = client.get("/api/levels")
    assert res.status_code == 200
    names = [lvl["name"] for lvl in res.get_json()]
    assert "Warm-up" in names
    assert "Spike Run" in names
    assert all(lvl["is_official"] for lvl in res.get_json() if lvl["name"] in ("Warm-up", "Spike Run"))


def test_official_levels_sort_before_user_levels(client):
    client.post("/api/levels", json=_valid_payload(name="Zzz User Level"))
    levels = client.get("/api/levels").get_json()
    official_idx = [i for i, lvl in enumerate(levels) if lvl["is_official"]]
    user_idx = [i for i, lvl in enumerate(levels) if not lvl["is_official"]]
    assert max(official_idx) < min(user_idx)


def test_get_level_detail_includes_objects(client):
    levels = client.get("/api/levels").get_json()
    warmup_id = next(lvl["id"] for lvl in levels if lvl["name"] == "Warm-up")
    detail = client.get(f"/api/levels/{warmup_id}").get_json()
    assert detail["length_units"] == 64
    assert any(obj["type"] == "spike" for obj in detail["objects"])
    assert any(obj["type"] == "block" for obj in detail["objects"])


def test_get_level_not_found(client):
    res = client.get("/api/levels/999999")
    assert res.status_code == 404


def test_create_level(client):
    res = client.post("/api/levels", json=_valid_payload())
    assert res.status_code == 201
    body = res.get_json()
    assert body["name"] == "My Level"
    assert body["is_official"] is False
    assert body["created_by"] == "test-ingress-user"
    assert len(body["objects"]) == 2


def test_create_level_rejects_missing_name(client):
    payload = _valid_payload()
    del payload["name"]
    res = client.post("/api/levels", json=payload)
    assert res.status_code == 400
    assert "name" in res.get_json()["error"]


def test_create_level_rejects_unknown_object_type(client):
    payload = _valid_payload(objects=[{"type": "laser", "x": 0, "y": 0}])
    res = client.post("/api/levels", json=payload)
    assert res.status_code == 400
    assert "unknown type" in res.get_json()["error"]


def test_create_level_rejects_block_missing_dimensions(client):
    payload = _valid_payload(objects=[{"type": "block", "x": 0, "y": 0}])
    res = client.post("/api/levels", json=payload)
    assert res.status_code == 400
    assert "'w'" in res.get_json()["error"]


def test_create_level_rejects_bad_start_mode(client):
    payload = _valid_payload(start_mode="robot")
    res = client.post("/api/levels", json=payload)
    assert res.status_code == 400


def test_update_own_level(client, level):
    payload = _valid_payload(name="Renamed")
    res = client.put(f"/api/levels/{level['id']}", json=payload)
    assert res.status_code == 200
    assert res.get_json()["name"] == "Renamed"


def test_update_someone_elses_level_is_403(client, conn):
    cur = conn.execute(
        "INSERT INTO levels (name, created_by, objects_json, is_official, sort_order) "
        "VALUES ('Other', 'someone-else', '[]', 0, 0)"
    )
    conn.commit()
    res = client.put(f"/api/levels/{cur.lastrowid}", json=_valid_payload())
    assert res.status_code == 403


def test_update_official_level_is_403(client):
    levels = client.get("/api/levels").get_json()
    warmup_id = next(lvl["id"] for lvl in levels if lvl["name"] == "Warm-up")
    res = client.put(f"/api/levels/{warmup_id}", json=_valid_payload())
    assert res.status_code == 403


def test_delete_own_level(client, level):
    res = client.delete(f"/api/levels/{level['id']}")
    assert res.status_code == 200
    assert client.get(f"/api/levels/{level['id']}").status_code == 404


def test_delete_official_level_is_403(client):
    levels = client.get("/api/levels").get_json()
    warmup_id = next(lvl["id"] for lvl in levels if lvl["name"] == "Warm-up")
    res = client.delete(f"/api/levels/{warmup_id}")
    assert res.status_code == 403
    assert client.get(f"/api/levels/{warmup_id}").status_code == 200
