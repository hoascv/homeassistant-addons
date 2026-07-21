import base64
from datetime import datetime, timedelta

import app as coopapp

# A minimal valid 1x1 JPEG, used as a stand-in for a real photo upload.
_TINY_JPEG_BYTES = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAMCAgICAgMCAgIDAwMDBAYEBAQEBAgGBgUGCQgKCgkICQkKDA8MCgsOCwkJDRENDg8QEBEQCgwSExIQEw8QEBD/2wBDAQMDAwQDBAgEBAgQCwkLEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBD/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAj/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCdABmX/9k="
)
_TINY_JPEG_DATA_URI = "data:image/jpeg;base64," + base64.b64encode(_TINY_JPEG_BYTES).decode()


def _hatch(days_ago):
    return (datetime.now() - timedelta(days=days_ago)).date().isoformat()


# --- Breeds ---


def test_breeds_seeded_with_defaults(client):
    body = client.get("/api/breeds").get_json()
    seen = {(row["name"], row["annual_eggs"]) for row in body}
    assert seen == set(coopapp.DEFAULT_BREEDS)


def test_add_breed(client):
    res = client.post("/api/breeds", json={"name": "Rhode Island Red", "annual_eggs": 250})
    assert res.status_code == 201
    body = res.get_json()
    assert body["name"] == "Rhode Island Red"
    assert body["annual_eggs"] == 250


def test_add_breed_rejects_empty_name(client):
    res = client.post("/api/breeds", json={"name": "  ", "annual_eggs": 250})
    assert res.status_code == 400


def test_add_breed_rejects_non_numeric_annual_eggs(client):
    res = client.post("/api/breeds", json={"name": "Orpington", "annual_eggs": "lots"})
    assert res.status_code == 400


def test_add_breed_rejects_non_positive_annual_eggs(client):
    res = client.post("/api/breeds", json={"name": "Orpington", "annual_eggs": 0})
    assert res.status_code == 400


def test_add_breed_rejects_case_insensitive_duplicate(client):
    res = client.post("/api/breeds", json={"name": "isabrown", "annual_eggs": 300})
    assert res.status_code == 400


def test_delete_breed(client):
    created = client.post("/api/breeds", json={"name": "Orpington", "annual_eggs": 180}).get_json()
    res = client.delete(f"/api/breeds/{created['id']}")
    assert res.status_code == 204
    names = [row["name"] for row in client.get("/api/breeds").get_json()]
    assert "Orpington" not in names


# --- Chickens: CRUD ---


def test_chickens_empty_by_default(client):
    assert client.get("/api/chickens").get_json() == []


def test_add_chicken(client):
    res = client.post(
        "/api/chickens",
        json={"name": "Henrietta", "breed": "Isabrown", "hatch_date": _hatch(300)},
    )
    assert res.status_code == 201

    chickens = client.get("/api/chickens").get_json()
    assert len(chickens) == 1
    assert chickens[0]["name"] == "Henrietta"
    assert chickens[0]["breed"] == "Isabrown"
    assert chickens[0]["status"] == "active"


def test_add_chicken_requires_name(client):
    res = client.post("/api/chickens", json={"breed": "Isabrown"})
    assert res.status_code == 400


def test_add_chicken_rejects_invalid_hatch_date(client):
    res = client.post("/api/chickens", json={"name": "Henrietta", "hatch_date": "not-a-date"})
    assert res.status_code == 400


def test_add_chicken_rejects_invalid_status(client):
    res = client.post("/api/chickens", json={"name": "Henrietta", "status": "flying-south"})
    assert res.status_code == 400


def test_add_chicken_without_breed_or_hatch_date(client):
    res = client.post("/api/chickens", json={"name": "Mystery Hen"})
    assert res.status_code == 201
    chickens = client.get("/api/chickens").get_json()
    assert chickens[0]["breed"] is None
    assert chickens[0]["hatch_date"] is None


def test_update_chicken(client):
    created = client.post("/api/chickens", json={"name": "Henrietta", "breed": "Isabrown"}).get_json()
    res = client.put(f"/api/chickens/{created['id']}", json={"status": "lost"})
    assert res.status_code == 200

    chickens = client.get("/api/chickens").get_json()
    assert chickens[0]["status"] == "lost"
    assert chickens[0]["name"] == "Henrietta"  # unspecified fields preserved


def test_update_missing_chicken_returns_404(client):
    res = client.put("/api/chickens/999", json={"status": "lost"})
    assert res.status_code == 404


def test_delete_chicken(client):
    created = client.post("/api/chickens", json={"name": "Henrietta"}).get_json()
    res = client.delete(f"/api/chickens/{created['id']}")
    assert res.status_code == 204
    assert client.get("/api/chickens").get_json() == []


# --- Photos ---


def test_chicken_without_photo_has_no_photo_flag_and_404s(client):
    created = client.post("/api/chickens", json={"name": "Henrietta"}).get_json()
    chickens = client.get("/api/chickens").get_json()
    assert chickens[0]["has_photo"] is False

    res = client.get(f"/api/chickens/{created['id']}/photo")
    assert res.status_code == 404


def test_add_chicken_with_photo(client):
    res = client.post(
        "/api/chickens", json={"name": "Henrietta", "photo": _TINY_JPEG_DATA_URI}
    )
    assert res.status_code == 201
    chicken_id = res.get_json()["id"]

    chickens = client.get("/api/chickens").get_json()
    assert chickens[0]["has_photo"] is True
    assert "photo" not in chickens[0]  # raw blob never included in the list

    photo_res = client.get(f"/api/chickens/{chicken_id}/photo")
    assert photo_res.status_code == 200
    assert photo_res.content_type == "image/jpeg"
    assert photo_res.data == _TINY_JPEG_BYTES


def test_add_chicken_rejects_invalid_photo_data(client):
    res = client.post("/api/chickens", json={"name": "Henrietta", "photo": "not-a-data-uri"})
    assert res.status_code == 400


def test_add_chicken_rejects_oversized_photo(client, monkeypatch):
    monkeypatch.setattr(coopapp, "MAX_PHOTO_BYTES", 10)  # smaller than the tiny test JPEG
    res = client.post(
        "/api/chickens", json={"name": "Henrietta", "photo": _TINY_JPEG_DATA_URI}
    )
    assert res.status_code == 400
    assert "too large" in res.get_json()["error"]


def test_update_chicken_can_add_a_photo(client):
    created = client.post("/api/chickens", json={"name": "Henrietta"}).get_json()
    res = client.put(f"/api/chickens/{created['id']}", json={"photo": _TINY_JPEG_DATA_URI})
    assert res.status_code == 200

    chickens = client.get("/api/chickens").get_json()
    assert chickens[0]["has_photo"] is True


def test_update_chicken_without_photo_field_preserves_existing_photo(client):
    created = client.post(
        "/api/chickens", json={"name": "Henrietta", "photo": _TINY_JPEG_DATA_URI}
    ).get_json()

    client.put(f"/api/chickens/{created['id']}", json={"status": "lost"})

    chickens = client.get("/api/chickens").get_json()
    assert chickens[0]["has_photo"] is True
    assert chickens[0]["status"] == "lost"


def test_update_chicken_can_explicitly_clear_photo(client):
    created = client.post(
        "/api/chickens", json={"name": "Henrietta", "photo": _TINY_JPEG_DATA_URI}
    ).get_json()

    client.put(f"/api/chickens/{created['id']}", json={"photo": None})

    chickens = client.get("/api/chickens").get_json()
    assert chickens[0]["has_photo"] is False
    assert client.get(f"/api/chickens/{created['id']}/photo").status_code == 404


# --- Age-based laying curve ---


def test_age_stage_multiplier_boundaries():
    assert coopapp._age_stage_multiplier(0) == 0.0
    assert coopapp._age_stage_multiplier(coopapp.POINT_OF_LAY_DAYS - 1) == 0.0
    assert coopapp._age_stage_multiplier(coopapp.POINT_OF_LAY_DAYS) == 1.0
    assert coopapp._age_stage_multiplier(coopapp.PRIME_END_DAYS - 1) == 1.0
    assert coopapp._age_stage_multiplier(coopapp.PRIME_END_DAYS) == coopapp.REDUCED_RATE_MULTIPLIER


def test_chicken_daily_rate_zero_before_point_of_lay(client):
    client.post(
        "/api/chickens",
        json={"name": "Chick", "breed": "Isabrown", "hatch_date": _hatch(30)},
    )
    chickens = client.get("/api/chickens").get_json()
    assert chickens[0]["daily_rate"] == 0.0


def test_chicken_daily_rate_full_during_prime(client):
    client.post(
        "/api/chickens",
        json={"name": "Henrietta", "breed": "Isabrown", "hatch_date": _hatch(300)},
    )
    chickens = client.get("/api/chickens").get_json()
    assert chickens[0]["daily_rate"] == round(300 / 365, 2)


def test_chicken_daily_rate_reduced_after_prime(client):
    client.post(
        "/api/chickens",
        json={"name": "Old Betty", "breed": "Sussex", "hatch_date": _hatch(700)},
    )
    chickens = client.get("/api/chickens").get_json()
    assert chickens[0]["daily_rate"] == round((260 / 365) * coopapp.REDUCED_RATE_MULTIPLIER, 2)


def test_chicken_daily_rate_unknown_age_defaults_to_prime(client):
    client.post("/api/chickens", json={"name": "Mystery Hen", "breed": "Sussex"})
    chickens = client.get("/api/chickens").get_json()
    assert chickens[0]["daily_rate"] == round(260 / 365, 2)


def test_chicken_daily_rate_zero_for_unknown_breed(client):
    client.post("/api/chickens", json={"name": "No Breed Hen", "breed": "Silkie"})
    chickens = client.get("/api/chickens").get_json()
    assert chickens[0]["daily_rate"] == 0.0


# --- Forecast basis switching ---


def test_forecast_uses_flat_counts_when_no_chickens(client):
    body = client.get("/api/trends?months=1").get_json()
    assert body["forecast_flock_basis"] == "flat_counts"


def test_forecast_switches_to_individual_once_a_chicken_exists(client):
    client.post(
        "/api/chickens",
        json={"name": "Henrietta", "breed": "Isabrown", "hatch_date": _hatch(300)},
    )
    body = client.get("/api/trends?months=1").get_json()
    assert body["forecast_flock_basis"] == "individual"
    assert body["forecast_daily_rate"] == round(300 / 365, 2)


def test_forecast_sums_multiple_active_chickens(client):
    client.post(
        "/api/chickens",
        json={"name": "Henrietta", "breed": "Isabrown", "hatch_date": _hatch(300)},
    )
    client.post(
        "/api/chickens",
        json={"name": "Old Betty", "breed": "Sussex", "hatch_date": _hatch(700)},
    )
    body = client.get("/api/trends?months=1").get_json()
    expected = (300 / 365) + (260 / 365) * coopapp.REDUCED_RATE_MULTIPLIER
    assert body["forecast_daily_rate"] == round(expected, 2)


def test_forecast_excludes_lost_chickens(client):
    created = client.post(
        "/api/chickens",
        json={"name": "Henrietta", "breed": "Isabrown", "hatch_date": _hatch(300)},
    ).get_json()
    client.put(f"/api/chickens/{created['id']}", json={"status": "lost"})

    body = client.get("/api/trends?months=1").get_json()
    # no active chickens left -> falls back to flat counts, not zero
    assert body["forecast_flock_basis"] == "flat_counts"


def test_forecast_zero_rate_chickens_still_use_individual_basis(client):
    # A chicken with an unknown/removed breed contributes 0, but its mere
    # presence (active) still means "individual" is the basis, not a
    # silent fallback to flat counts.
    client.post("/api/chickens", json={"name": "No Breed Hen", "breed": "Silkie"})
    body = client.get("/api/trends?months=1").get_json()
    assert body["forecast_flock_basis"] == "individual"
    assert body["forecast_daily_rate"] == 0.0


def test_deleting_a_chickens_breed_zeroes_its_rate_without_erroring(client):
    breed = client.post("/api/breeds", json={"name": "Orpington", "annual_eggs": 180}).get_json()
    client.post(
        "/api/chickens",
        json={"name": "Henrietta", "breed": "Orpington", "hatch_date": _hatch(300)},
    )
    client.delete(f"/api/breeds/{breed['id']}")

    chickens = client.get("/api/chickens").get_json()
    assert chickens[0]["daily_rate"] == 0.0
    assert chickens[0]["breed"] == "Orpington"  # data preserved, not corrupted

    body = client.get("/api/trends?months=1").get_json()
    assert body["forecast_flock_basis"] == "individual"
    assert body["forecast_daily_rate"] == 0.0


def test_backtest_uses_each_chickens_age_as_of_the_historical_month(client):
    # A chicken hatched ~10 months ago: too young to have been laying a
    # year ago, in prime now. The backtest for a year-ago month should
    # reflect its age back then, not its current age.
    client.post(
        "/api/chickens",
        json={"name": "Henrietta", "breed": "Isabrown", "hatch_date": _hatch(300)},
    )
    body = client.get("/api/trends?months=12").get_json()
    # the oldest month in a 12-month window is ~11 months back — the bird
    # (300 days old now) would have been too young to lay back then
    assert body["forecast_backtest"][0] == 0
