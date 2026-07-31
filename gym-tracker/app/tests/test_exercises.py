def test_presets_seeded_and_grouped(client):
    groups = client.get("/api/exercises").get_json()
    equipment = [g["equipment"] for g in groups]
    assert equipment[:3] == ["Bodyweight", "Pull-up bar", "Dumbbells"]
    names = {ex["name"] for g in groups for ex in g["exercises"]}
    assert {"Push-up", "Pull-up", "Dumbbell curl"} <= names


def test_add_custom_exercise(client):
    res = client.post("/api/exercises", json={"name": "Kettlebell swing", "equipment": "Kettlebell"})
    assert res.status_code == 201
    groups = {g["equipment"]: g for g in client.get("/api/exercises").get_json()}
    assert "Kettlebell" in groups
    assert groups["Kettlebell"]["exercises"][0]["name"] == "Kettlebell swing"


def test_add_exercise_requires_name(client):
    assert client.post("/api/exercises", json={"name": "  "}).status_code == 400


def test_update_exercise(client):
    ex_id = client.post("/api/exercises", json={"name": "Band pull-apart", "equipment": "Bands"}).get_json()["id"]
    assert client.put(
        f"/api/exercises/{ex_id}", json={"name": "Band row", "equipment": "Bands"}
    ).status_code == 200
    names = {ex["name"] for g in client.get("/api/exercises").get_json() for ex in g["exercises"]}
    assert "Band row" in names and "Band pull-apart" not in names
    assert client.put("/api/exercises/999", json={"name": "x"}).status_code == 404


def test_unused_exercise_hard_deleted(client):
    ex_id = client.post("/api/exercises", json={"name": "Temp"}).get_json()["id"]
    assert client.delete(f"/api/exercises/{ex_id}").get_json()["status"] == "deleted"


def test_used_exercise_archived_not_deleted(client):
    groups = client.get("/api/exercises").get_json()
    ex_id = groups[0]["exercises"][0]["id"]
    client.post("/api/workouts", json={"exercise_id": ex_id, "sets": 3, "reps": 40})
    assert client.delete(f"/api/exercises/{ex_id}").get_json()["status"] == "archived"
    # gone from the active library, but its workout history remains
    active_ids = [ex["id"] for g in client.get("/api/exercises").get_json() for ex in g["exercises"]]
    assert ex_id not in active_ids
    history = client.get("/api/workouts").get_json()
    assert any(w["exercise_id"] == ex_id for w in history)


def test_workout_crud(client):
    groups = client.get("/api/exercises").get_json()
    ex_id = groups[0]["exercises"][0]["id"]
    res = client.post(
        "/api/workouts",
        json={"exercise_id": ex_id, "sets": 3, "reps": 12, "weight_kg": 10, "date": "2026-07-20"},
    )
    assert res.status_code == 201
    wid = res.get_json()["id"]

    rows = client.get("/api/workouts").get_json()
    assert rows[0]["id"] == wid
    assert rows[0]["exercise_name"]
    assert rows[0]["sets"] == 3 and rows[0]["reps"] == 12 and rows[0]["weight_kg"] == 10

    # filter by exercise + date
    assert len(client.get(f"/api/workouts?exercise_id={ex_id}").get_json()) == 1
    assert len(client.get("/api/workouts?date=2026-07-20").get_json()) == 1
    assert len(client.get("/api/workouts?date=2026-07-19").get_json()) == 0

    assert client.delete(f"/api/workouts/{wid}").status_code == 204
    assert client.get("/api/workouts").get_json() == []


def test_workout_edit(client):
    groups = client.get("/api/exercises").get_json()
    ex_id = groups[0]["exercises"][0]["id"]
    wid = client.post(
        "/api/workouts", json={"exercise_id": ex_id, "sets": 3, "reps": 10, "date": "2026-07-20"}
    ).get_json()["id"]

    res = client.put(
        f"/api/workouts/{wid}",
        json={"sets": 4, "reps": 12, "weight_kg": 12.5, "date": "2026-07-21", "notes": "felt strong"},
    )
    assert res.status_code == 200
    w = client.get("/api/workouts").get_json()[0]
    assert w["sets"] == 4 and w["reps"] == 12 and w["weight_kg"] == 12.5
    assert w["ts"].startswith("2026-07-21")
    assert w["notes"] == "felt strong"
    assert client.put("/api/workouts/999", json={"sets": 1}).status_code == 404


def test_exercise_referenced_by_challenge_is_archived(client):
    groups = client.get("/api/exercises").get_json()
    ex_id = groups[0]["exercises"][0]["id"]
    client.post("/api/challenge/items", json={"item_type": "exercise", "exercise_id": ex_id, "target_reps": 20})
    # even with no workout history, a challenge reference protects it from hard delete
    assert client.delete(f"/api/exercises/{ex_id}").get_json()["status"] == "archived"


def test_workout_validation(client):
    assert client.post("/api/workouts", json={}).status_code == 400
    assert client.post("/api/workouts", json={"exercise_id": 999}).status_code == 400
    groups = client.get("/api/exercises").get_json()
    ex_id = groups[0]["exercises"][0]["id"]
    assert client.post("/api/workouts", json={"exercise_id": ex_id, "reps": "lots"}).status_code == 400
    assert client.delete("/api/workouts/999").status_code == 404


# --- Provenance: when definitions were created and last changed -------------


def test_created_and_updated_are_stamped(client, conn):
    r = client.post("/api/exercises", json={"name": "Nordic curl", "equipment": "Bodyweight"})
    eid = r.get_json()["id"]
    row = conn.execute("SELECT created_at, updated_at FROM exercises WHERE id = ?", (eid,)).fetchone()
    assert row["created_at"] and row["updated_at"] == row["created_at"]

    client.put(f"/api/exercises/{eid}", json={"name": "Nordic hamstring curl"})
    after = conn.execute("SELECT created_at, updated_at FROM exercises WHERE id = ?", (eid,)).fetchone()
    assert after["created_at"] == row["created_at"]  # creation never moves
    assert after["updated_at"] >= row["updated_at"]


def test_archiving_counts_as_a_change(client, conn):
    eid = client.post("/api/exercises", json={"name": "Sled push"}).get_json()["id"]
    conn.execute("UPDATE exercises SET updated_at = '2020-01-01T00:00:00' WHERE id = ?", (eid,))
    conn.execute(
        "INSERT INTO workout_logs (ts, exercise_id, source, ts_exact) "
        "VALUES ('2026-07-30T10:00:00', ?, 'manual', 1)",
        (eid,),
    )
    conn.commit()

    client.delete(f"/api/exercises/{eid}")  # referenced, so archived rather than deleted
    row = conn.execute("SELECT archived, updated_at FROM exercises WHERE id = ?", (eid,)).fetchone()
    assert row["archived"] == 1
    assert row["updated_at"] > "2020-01-01T00:00:00"


# --- Exercise pictures ------------------------------------------------------

import io

# Smallest valid files of each type, so the tests exercise the real sniffing.
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)
JPEG_HEAD = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP_HEAD = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 32


def _upload(client, exercise_id, data, filename="pic.png"):
    return client.post(
        f"/api/exercises/{exercise_id}/image",
        data={"file": (io.BytesIO(data), filename)},
        content_type="multipart/form-data",
    )


def _first_exercise(client):
    return client.get("/api/exercises").get_json()[0]["exercises"][0]["id"]


def test_a_picture_can_be_added_fetched_and_removed(client):
    eid = _first_exercise(client)
    assert client.get(f"/api/exercises/{eid}/image").status_code == 404

    assert _upload(client, eid, PNG_1PX).status_code == 200

    served = client.get(f"/api/exercises/{eid}/image")
    assert served.status_code == 200
    assert served.mimetype == "image/png"
    assert served.data == PNG_1PX

    # The listing advertises it, with a version to bust the cache on.
    listed = next(
        e for g in client.get("/api/exercises").get_json() for e in g["exercises"] if e["id"] == eid
    )
    assert listed["image_v"]

    assert client.delete(f"/api/exercises/{eid}/image").status_code == 200
    assert client.get(f"/api/exercises/{eid}/image").status_code == 404


def test_replacing_a_picture_changes_its_version(client):
    eid = _first_exercise(client)
    _upload(client, eid, PNG_1PX)
    first = next(
        e for g in client.get("/api/exercises").get_json() for e in g["exercises"] if e["id"] == eid
    )["image_v"]

    _upload(client, eid, JPEG_HEAD, filename="pic.jpg")
    second = next(
        e for g in client.get("/api/exercises").get_json() for e in g["exercises"] if e["id"] == eid
    )["image_v"]

    assert client.get(f"/api/exercises/{eid}/image").mimetype == "image/jpeg"
    assert second >= first  # a cached thumbnail can't survive the swap


def test_the_bytes_decide_the_type_not_the_filename(client):
    eid = _first_exercise(client)
    # A script wearing a .png name is still not an image.
    r = _upload(client, eid, b"<?php echo 'nope'; ?>", filename="evil.png")
    assert r.status_code == 400
    assert "JPEG" in r.get_json()["error"]
    assert client.get(f"/api/exercises/{eid}/image").status_code == 404

    # ...and a real WebP is accepted whatever it's called.
    assert _upload(client, eid, WEBP_HEAD, filename="whatever.txt").status_code == 200
    assert client.get(f"/api/exercises/{eid}/image").mimetype == "image/webp"


def test_an_oversized_upload_is_refused(client):
    import app as gymapp

    eid = _first_exercise(client)
    huge = PNG_1PX + b"\x00" * (gymapp.MAX_IMAGE_BYTES + 1)
    r = _upload(client, eid, huge)
    assert r.status_code == 400
    assert "too large" in r.get_json()["error"]


def test_uploading_to_a_missing_exercise_is_a_404(client):
    assert _upload(client, 9999, PNG_1PX).status_code == 404
    assert client.delete("/api/exercises/9999/image").status_code == 404


def test_pictures_survive_backup_and_restore(client, db_path, tmp_path):
    """Images live in the database precisely so a backup carries them."""
    eid = _first_exercise(client)
    _upload(client, eid, PNG_1PX)

    backup = client.get("/api/backup").data
    client.delete(f"/api/exercises/{eid}/image")
    assert client.get(f"/api/exercises/{eid}/image").status_code == 404

    restored = client.post(
        "/api/restore",
        data={"file": (io.BytesIO(backup), "backup.db")},
        content_type="multipart/form-data",
    )
    assert restored.status_code == 200
    assert client.get(f"/api/exercises/{eid}/image").data == PNG_1PX


def test_challenge_items_advertise_their_exercise_picture(client, conn):
    eid = _first_exercise(client)
    _upload(client, eid, PNG_1PX)
    item = client.post("/api/challenge/items", json={
        "item_type": "exercise", "exercise_id": eid, "target_reps": 10,
    }).get_json()["id"]

    items = client.get("/api/challenge/items").get_json()
    entry = next(i for i in items if i["id"] == item)
    assert entry["image_v"]
    # A supplement item has no exercise, so nothing to show.
    sup = conn.execute("SELECT id FROM supplements WHERE archived = 0 LIMIT 1").fetchone()["id"]
    sup_item = client.post("/api/challenge/items", json={
        "item_type": "supplement", "supplement_id": sup, "dose": "5 g",
    }).get_json()["id"]
    assert next(i for i in client.get("/api/challenge/items").get_json()
                if i["id"] == sup_item)["image_v"] is None
