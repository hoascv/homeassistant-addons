from datetime import datetime


def test_feeding_stats_empty_food_type_returns_zeroed_result(client):
    body = client.get("/api/feeding-stats?food_type=").get_json()
    assert body["empty_count"] == 0
    assert body["avg_days_between_empty"] is None
    assert body["days_since_last_empty"] is None


def test_feeding_stats_no_history_for_food_type(client):
    body = client.get("/api/feeding-stats?food_type=pellets").get_json()
    assert body["food_type"] == "pellets"
    assert body["empty_count"] == 0
    assert body["last_empty"] is None


def test_feeding_log_without_container_empty_does_not_count(client):
    client.post(
        "/api/log",
        json={"type": "feeding", "food_type": "pellets", "container_empty": False},
    )
    body = client.get("/api/feeding-stats?food_type=pellets").get_json()
    assert body["empty_count"] == 0


def test_feeding_stats_single_empty_event_has_no_average_yet(client):
    client.post(
        "/api/log",
        json={
            "type": "feeding",
            "food_type": "pellets",
            "container_empty": True,
            "ts": "2026-06-20T10:00:00",
        },
    )
    body = client.get("/api/feeding-stats?food_type=pellets").get_json()
    assert body["empty_count"] == 1
    assert body["avg_days_between_empty"] is None
    assert body["last_empty"] == "2026-06-20T10:00:00"
    assert body["days_since_last_empty"] > 0


def test_feeding_stats_averages_intervals_between_empty_events(client):
    client.post(
        "/api/log",
        json={
            "type": "feeding",
            "food_type": "pellets",
            "container_empty": True,
            "ts": "2026-06-01T10:00:00",
        },
    )
    client.post(
        "/api/log",
        json={
            "type": "feeding",
            "food_type": "pellets",
            "container_empty": True,
            "ts": "2026-06-20T10:00:00",  # 19 days later
        },
    )
    client.post(
        "/api/log",
        json={
            "type": "feeding",
            "food_type": "pellets",
            "container_empty": True,
            "ts": "2026-07-09T10:00:00",  # 19 days after that
        },
    )
    body = client.get("/api/feeding-stats?food_type=pellets").get_json()
    assert body["empty_count"] == 3
    assert body["avg_days_between_empty"] == 19.0


def test_feeding_stats_food_type_matching_is_case_and_whitespace_insensitive(client):
    client.post(
        "/api/log",
        json={
            "type": "feeding",
            "food_type": "  Pellets ",
            "container_empty": True,
            "ts": "2026-06-01T10:00:00",
        },
    )
    body = client.get("/api/feeding-stats?food_type=pellets").get_json()
    assert body["empty_count"] == 1


def test_feeding_stats_only_matches_the_requested_food_type(client):
    client.post(
        "/api/log",
        json={
            "type": "feeding",
            "food_type": "pellets",
            "container_empty": True,
            "ts": "2026-06-01T10:00:00",
        },
    )
    client.post(
        "/api/log",
        json={
            "type": "feeding",
            "food_type": "scratch grains",
            "container_empty": True,
            "ts": "2026-06-10T10:00:00",
        },
    )
    body = client.get("/api/feeding-stats?food_type=pellets").get_json()
    assert body["empty_count"] == 1


def test_container_empty_round_trips_through_update(client):
    created = client.post(
        "/api/log",
        json={"type": "feeding", "food_type": "pellets", "container_empty": False},
    ).get_json()

    entries = client.get("/api/entries?type=feeding").get_json()
    assert entries[0]["container_empty"] == 0

    client.put(f"/api/entries/{created['id']}", json={"container_empty": True})
    entries = client.get("/api/entries?type=feeding").get_json()
    assert entries[0]["container_empty"] == 1

    stats = client.get("/api/feeding-stats?food_type=pellets").get_json()
    assert stats["empty_count"] == 1


def test_container_empty_is_null_for_non_feeding_entries(client):
    client.post("/api/log", json={"type": "egg", "count": 1})
    entries = client.get("/api/entries?type=egg").get_json()
    assert entries[0]["container_empty"] is None


def test_update_without_container_empty_field_preserves_existing_value(client):
    created = client.post(
        "/api/log",
        json={"type": "feeding", "food_type": "pellets", "container_empty": True},
    ).get_json()

    client.put(f"/api/entries/{created['id']}", json={"notes": "topped up"})

    entries = client.get("/api/entries?type=feeding").get_json()
    assert entries[0]["container_empty"] == 1
    assert entries[0]["notes"] == "topped up"


def test_feeding_stats_total_feedings_counts_regardless_of_container_empty(client):
    client.post("/api/log", json={"type": "feeding", "food_type": "pellets", "container_empty": False})
    client.post("/api/log", json={"type": "feeding", "food_type": "pellets", "container_empty": True})
    client.post("/api/log", json={"type": "feeding", "food_type": "pellets", "container_empty": False})

    body = client.get("/api/feeding-stats?food_type=pellets").get_json()
    assert body["total_feedings"] == 3
    assert body["empty_count"] == 1


def test_feeding_stats_all_empty_when_no_feedings_logged(client):
    body = client.get("/api/feeding-stats-all").get_json()
    assert body == []


def test_feeding_stats_all_covers_every_logged_food_type(client):
    client.post(
        "/api/log",
        json={
            "type": "feeding",
            "food_type": "Pellets",
            "container_empty": True,
            "ts": "2026-06-01T10:00:00",
        },
    )
    client.post(
        "/api/log",
        json={
            "type": "feeding",
            "food_type": "Pellets",
            "container_empty": True,
            "ts": "2026-06-19T10:00:00",
        },
    )
    client.post(
        "/api/log",
        json={
            "type": "feeding",
            "food_type": "Scratch grains",
            "container_empty": False,
            "ts": "2026-07-01T10:00:00",
        },
    )

    body = client.get("/api/feeding-stats-all").get_json()
    by_type = {row["food_type"]: row for row in body}

    assert set(by_type) == {"Pellets", "Scratch grains"}
    assert by_type["Pellets"]["total_feedings"] == 2
    assert by_type["Pellets"]["avg_days_between_empty"] == 18.0
    assert by_type["Scratch grains"]["total_feedings"] == 1
    assert by_type["Scratch grains"]["empty_count"] == 0
    assert by_type["Scratch grains"]["avg_days_between_empty"] is None


def test_feeding_stats_all_sorted_alphabetically_case_insensitive(client):
    for food_type in ("scratch grains", "Layer feed", "pellets"):
        client.post("/api/log", json={"type": "feeding", "food_type": food_type})

    body = client.get("/api/feeding-stats-all").get_json()
    names = [row["food_type"] for row in body]
    assert names == ["Layer feed", "pellets", "scratch grains"]


def test_feeding_stats_all_survives_food_type_removed_from_management_list(client):
    created = client.post("/api/food-types", json={"name": "Temporary feed"}).get_json()
    client.post("/api/log", json={"type": "feeding", "food_type": "Temporary feed"})
    client.delete(f"/api/food-types/{created['id']}")

    body = client.get("/api/feeding-stats-all").get_json()
    names = [row["food_type"] for row in body]
    assert "Temporary feed" in names
