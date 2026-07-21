import app as coopapp


def test_food_types_seeded_with_defaults(client):
    body = client.get("/api/food-types").get_json()
    names = [row["name"] for row in body]
    assert names == coopapp.DEFAULT_FOOD_TYPES


def test_add_food_type(client):
    res = client.post("/api/food-types", json={"name": "Homemade mash mix"})
    assert res.status_code == 201
    assert res.get_json()["name"] == "Homemade mash mix"

    names = [row["name"] for row in client.get("/api/food-types").get_json()]
    assert "Homemade mash mix" in names


def test_add_food_type_trims_whitespace(client):
    client.post("/api/food-types", json={"name": "  Sprouted grain  "})
    names = [row["name"] for row in client.get("/api/food-types").get_json()]
    assert "Sprouted grain" in names


def test_add_food_type_rejects_empty_name(client):
    res = client.post("/api/food-types", json={"name": "   "})
    assert res.status_code == 400
    assert res.get_json()["error"] == "name is required"


def test_add_food_type_rejects_case_insensitive_duplicate(client):
    res = client.post("/api/food-types", json={"name": "pellets"})
    assert res.status_code == 400
    assert "already" in res.get_json()["error"]

    names = [row["name"] for row in client.get("/api/food-types").get_json()]
    assert names.count("Pellets") == 1  # the original, no duplicate added


def test_delete_food_type(client):
    created = client.post("/api/food-types", json={"name": "Temporary feed"}).get_json()
    res = client.delete(f"/api/food-types/{created['id']}")
    assert res.status_code == 204

    names = [row["name"] for row in client.get("/api/food-types").get_json()]
    assert "Temporary feed" not in names


def test_deleting_a_food_type_does_not_touch_existing_log_entries(client):
    created = client.post("/api/food-types", json={"name": "Temporary feed"}).get_json()
    client.post("/api/log", json={"type": "feeding", "food_type": "Temporary feed"})
    client.delete(f"/api/food-types/{created['id']}")

    entries = client.get("/api/entries?type=feeding").get_json()
    assert entries[0]["food_type"] == "Temporary feed"


def test_new_food_types_are_appended_in_order(client):
    client.post("/api/food-types", json={"name": "Zzz feed"})
    client.post("/api/food-types", json={"name": "Aaa feed"})
    names = [row["name"] for row in client.get("/api/food-types").get_json()]
    assert names[-2:] == ["Zzz feed", "Aaa feed"]  # insertion order, not alphabetical
