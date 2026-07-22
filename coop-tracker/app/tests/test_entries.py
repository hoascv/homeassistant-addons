def test_log_egg_creates_entry(client):
    res = client.post("/api/log", json={"type": "egg", "count": 3})
    assert res.status_code == 201
    body = res.get_json()
    assert body["type"] == "egg"
    assert "id" in body

    entries = client.get("/api/entries").get_json()
    assert len(entries) == 1
    assert entries[0]["count"] == 3


def test_log_invalid_type_rejected(client):
    res = client.post("/api/log", json={"type": "dinosaur"})
    assert res.status_code == 400
    assert res.get_json()["error"] == "invalid type"


def test_log_cleaning_without_count(client):
    res = client.post("/api/log", json={"type": "cleaning", "notes": "full bedding change"})
    assert res.status_code == 201
    entries = client.get("/api/entries?type=cleaning").get_json()
    assert entries[0]["count"] is None
    assert entries[0]["notes"] == "full bedding change"


def test_log_custom_timestamp(client):
    res = client.post("/api/log", json={"type": "egg", "count": 1, "ts": "2026-01-05T10:00:00"})
    assert res.get_json()["ts"] == "2026-01-05T10:00:00"


def test_log_invalid_timestamp_rejected(client):
    res = client.post("/api/log", json={"type": "egg", "count": 1, "ts": "not-a-date"})
    assert res.status_code == 400
    assert res.get_json()["error"] == "invalid ts"


def test_log_sale_records_price(client):
    client.post("/api/log", json={"type": "sale", "count": 2, "price": 6.5})
    entries = client.get("/api/entries?type=sale").get_json()
    assert entries[0]["price"] == 6.5


def test_log_expense_records_category_and_cost(client):
    client.post("/api/log", json={"type": "expense", "category": "Food", "cost": 24.99})
    entries = client.get("/api/entries?type=expense").get_json()
    assert entries[0]["category"] == "Food"
    assert entries[0]["cost"] == 24.99


def test_entries_filtered_by_type(client):
    client.post("/api/log", json={"type": "egg", "count": 2})
    client.post("/api/log", json={"type": "cleaning"})
    eggs = client.get("/api/entries?type=egg").get_json()
    assert len(eggs) == 1
    assert eggs[0]["type"] == "egg"


def test_entries_limit_and_order(client):
    for i in range(5):
        client.post(
            "/api/log",
            json={"type": "egg", "count": i, "ts": f"2026-01-0{i + 1}T10:00:00"},
        )
    entries = client.get("/api/entries?limit=3").get_json()
    assert [e["count"] for e in entries] == [4, 3, 2]  # most recent first, capped at 3


def test_update_entry(client):
    created = client.post("/api/log", json={"type": "egg", "count": 1}).get_json()
    res = client.put(f"/api/entries/{created['id']}", json={"count": 5, "notes": "corrected"})
    assert res.status_code == 200
    entries = client.get("/api/entries").get_json()
    assert entries[0]["count"] == 5
    assert entries[0]["notes"] == "corrected"


def test_update_entry_leaves_unspecified_fields_untouched(client):
    created = client.post(
        "/api/log", json={"type": "egg", "count": 1, "notes": "original"}
    ).get_json()
    client.put(f"/api/entries/{created['id']}", json={"count": 2})
    entries = client.get("/api/entries").get_json()
    assert entries[0]["count"] == 2
    assert entries[0]["notes"] == "original"


def test_update_missing_entry_returns_404(client):
    res = client.put("/api/entries/9999", json={"count": 1})
    assert res.status_code == 404


def test_delete_entry(client):
    created = client.post("/api/log", json={"type": "egg", "count": 1}).get_json()
    res = client.delete(f"/api/entries/{created['id']}")
    assert res.status_code == 204
    assert client.get("/api/entries").get_json() == []


def test_log_used_egg_given_away(client):
    client.post("/api/log", json={"type": "used", "count": 2, "given_away": True})
    entries = client.get("/api/entries?type=used").get_json()
    assert entries[0]["given_away"] == 1


def test_log_used_egg_without_given_away_is_null(client):
    client.post("/api/log", json={"type": "used", "count": 2})
    entries = client.get("/api/entries?type=used").get_json()
    assert entries[0]["given_away"] is None


def test_given_away_is_null_for_non_used_entries(client):
    client.post("/api/log", json={"type": "egg", "count": 1})
    entries = client.get("/api/entries?type=egg").get_json()
    assert entries[0]["given_away"] is None


def test_given_away_round_trips_through_update(client):
    created = client.post(
        "/api/log", json={"type": "used", "count": 1, "given_away": False}
    ).get_json()

    entries = client.get("/api/entries?type=used").get_json()
    assert entries[0]["given_away"] == 0

    client.put(f"/api/entries/{created['id']}", json={"given_away": True})
    entries = client.get("/api/entries?type=used").get_json()
    assert entries[0]["given_away"] == 1


def test_update_without_given_away_field_preserves_existing_value(client):
    created = client.post(
        "/api/log", json={"type": "used", "count": 1, "given_away": True}
    ).get_json()

    client.put(f"/api/entries/{created['id']}", json={"notes": "for a neighbor"})

    entries = client.get("/api/entries?type=used").get_json()
    assert entries[0]["given_away"] == 1
    assert entries[0]["notes"] == "for a neighbor"
