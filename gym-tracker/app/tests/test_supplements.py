def test_supplements_seeded(client):
    sups = client.get("/api/supplements").get_json()
    names = {s["name"]: s["dose"] for s in sups}
    assert names.get("Creatine") == "5 g"
    assert "Protein powder" in names


def test_add_edit_supplement(client):
    res = client.post("/api/supplements", json={"name": "Omega-3", "dose": "2 g"})
    assert res.status_code == 201
    sid = res.get_json()["id"]
    assert any(s["name"] == "Omega-3" for s in client.get("/api/supplements").get_json())

    assert client.put(f"/api/supplements/{sid}", json={"name": "Fish oil", "dose": "3 g"}).status_code == 200
    sup = next(s for s in client.get("/api/supplements").get_json() if s["id"] == sid)
    assert sup["name"] == "Fish oil" and sup["dose"] == "3 g"


def test_add_supplement_requires_name(client):
    assert client.post("/api/supplements", json={"dose": "5 g"}).status_code == 400
    assert client.put("/api/supplements/999", json={"name": "x"}).status_code == 404


def test_unused_supplement_hard_deleted(client):
    sid = client.post("/api/supplements", json={"name": "Temp"}).get_json()["id"]
    assert client.delete(f"/api/supplements/{sid}").get_json()["status"] == "deleted"


def test_referenced_supplement_archived(client):
    sid = client.post("/api/supplements", json={"name": "BCAA", "dose": "5 g"}).get_json()["id"]
    client.post("/api/challenge/items", json={"item_type": "supplement", "supplement_id": sid})
    assert client.delete(f"/api/supplements/{sid}").get_json()["status"] == "archived"
    assert sid not in [s["id"] for s in client.get("/api/supplements").get_json()]


def test_delete_supplement_404(client):
    assert client.delete("/api/supplements/999").status_code == 404
