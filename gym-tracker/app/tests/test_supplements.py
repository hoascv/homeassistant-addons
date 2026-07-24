def test_supplements_seeded(client):
    sups = {s["name"]: s for s in client.get("/api/supplements").get_json()}
    creatine = sups["Creatine"]
    assert creatine["dose"] == "5 g"
    assert creatine["dose_amount"] == 5 and creatine["dose_unit"] == "g"
    assert creatine["quantity"] == 1 and creatine["timing"] == "Anytime"
    assert "Protein powder" in sups


def test_add_supplement_with_structured_fields(client):
    res = client.post(
        "/api/supplements",
        json={"name": "Omega-3", "dose_amount": 500, "dose_unit": "mg", "quantity": 2,
              "timing": "With meal", "brand": "Acme"},
    )
    assert res.status_code == 201
    sup = next(s for s in client.get("/api/supplements").get_json() if s["name"] == "Omega-3")
    assert sup["dose_amount"] == 500 and sup["dose_unit"] == "mg"
    assert sup["quantity"] == 2
    assert sup["timing"] == "With meal" and sup["brand"] == "Acme"
    # quantity per serving folds into the display dose
    assert sup["dose"] == "2× 500 mg"


def test_edit_supplement_fields(client):
    sid = client.post("/api/supplements", json={"name": "D3", "dose_amount": 1000, "dose_unit": "IU"}).get_json()["id"]
    assert client.put(
        f"/api/supplements/{sid}",
        json={"name": "Vitamin D3", "dose_amount": 2000, "dose_unit": "IU", "quantity": 1, "timing": "Morning"},
    ).status_code == 200
    sup = next(s for s in client.get("/api/supplements").get_json() if s["id"] == sid)
    assert sup["name"] == "Vitamin D3"
    assert sup["dose_amount"] == 2000 and sup["dose"] == "2000 IU"
    assert sup["timing"] == "Morning"


def test_supplement_dose_optional(client):
    # A supplement can be name-only (no dosage yet).
    sid = client.post("/api/supplements", json={"name": "Greens"}).get_json()["id"]
    sup = next(s for s in client.get("/api/supplements").get_json() if s["id"] == sid)
    assert sup["dose"] is None and sup["dose_amount"] is None


def test_add_supplement_validation(client):
    assert client.post("/api/supplements", json={"dose_amount": 5}).status_code == 400  # no name
    assert client.post("/api/supplements", json={"name": "x", "dose_amount": "lots"}).status_code == 400
    assert client.post("/api/supplements", json={"name": "x", "quantity": -1}).status_code == 400
    assert client.put("/api/supplements/999", json={"name": "x"}).status_code == 404


def test_supplement_timings_endpoint(client):
    timings = client.get("/api/supplement-timings").get_json()
    assert "Morning" in timings and "Post-workout" in timings


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


def test_dose_text_helpers():
    import app as gymapp
    assert gymapp._parse_dose_text("5 g") == (5.0, "g")
    assert gymapp._parse_dose_text("2.5g") == (2.5, "g")
    assert gymapp._parse_dose_text("1 tablet") == (1.0, "tablet")
    assert gymapp._parse_dose_text("") == (None, None)
    assert gymapp._supplement_dose_text(5, "g", 1) == "5 g"
    assert gymapp._supplement_dose_text(500, "mg", 2) == "2× 500 mg"
    assert gymapp._supplement_dose_text(None, "scoop", None) == "scoop"
    assert gymapp._supplement_dose_text(None, None, None) is None
