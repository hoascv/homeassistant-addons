"""Changing a target part-way through a challenge.

Raising the reps is a normal thing to do — the point of a challenge is often to
get harder. So the question is not whether it is allowed but what it does to the
days already behind you, and the answer has to be "nothing".

The design that makes that true: a completion records only `(item_id, day)`, so
a tick is binary and carries no target. Volume comes from `workout_logs`, which
snapshot the sets and reps as they were at the moment of ticking. Targets live
on the item, and only describe what is being asked of you *now*.
"""
from datetime import date, timedelta


def _item(client, kind="exercise"):
    items = client.get("/api/challenge").get_json()["items"]
    return next(i for i in items if i["item_type"] == kind)


def _stats(client):
    """The per-challenge stats block; its per-item breakdown is under `items`."""
    return client.get("/api/challenges/stats").get_json()[0]


def test_raising_the_reps_does_not_undo_completed_days(client, conn):
    """The one that matters. Days already earned were earned against what was
    asked at the time, and a completion holds no target to re-judge."""
    item = _item(client)
    client.put(f"/api/challenge/items/{item['id']}",
               json={"target_sets": 3, "target_reps": 40})

    today = date.today()
    for off in (1, 2, 3):
        for i in [x["id"] for x in client.get("/api/challenge").get_json()["items"]]:
            conn.execute("INSERT INTO challenge_completions (item_id, day) VALUES (?, ?)",
                         (i, (today - timedelta(days=off)).isoformat()))
    conn.commit()
    streak_before = client.get("/api/challenge").get_json()["streak"]

    client.put(f"/api/challenge/items/{item['id']}", json={"target_reps": 50})

    assert client.get("/api/challenge").get_json()["streak"] == streak_before


def test_history_still_shows_those_days_as_done(client, conn):
    item = _item(client)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    conn.execute("INSERT INTO challenge_completions (item_id, day) VALUES (?, ?)",
                 (item["id"], yesterday))
    conn.commit()

    client.put(f"/api/challenge/items/{item['id']}", json={"target_reps": 500})

    history = client.get("/api/challenge/history").get_json()
    days = history["days"] if isinstance(history, dict) else history
    day = next(d for d in days if d.get("day") == yesterday)
    assert day, "yesterday should still appear"


def test_adherence_is_unaffected_by_a_harder_target(client, conn):
    """Per-item adherence counts days ticked over days the item was a member,
    so the target does not enter into it at all."""
    item = _item(client)
    today = date.today()
    for off in (1, 2):
        conn.execute("INSERT INTO challenge_completions (item_id, day) VALUES (?, ?)",
                     (item["id"], (today - timedelta(days=off)).isoformat()))
    conn.commit()

    before = next(p for p in _stats(client)["items"] if p["id"] == item["id"])
    client.put(f"/api/challenge/items/{item['id']}", json={"target_reps": 999})
    after = next(p for p in _stats(client)["items"] if p["id"] == item["id"])

    assert (after["days_done"], after["days_member"]) == (
        before["days_done"], before["days_member"]
    )


def test_volume_keeps_what_was_actually_done(client):
    """The subtle one. Volume reads workout_logs, which record the target as it
    stood when the tick happened — so raising the reps does not retroactively
    inflate what you already did."""
    item = _item(client)
    client.put(f"/api/challenge/items/{item['id']}",
               json={"target_sets": 1, "target_reps": 40})
    client.post("/api/challenge/toggle", json={"item_id": item["id"]})

    logged = next(
        w for w in client.get("/api/workouts").get_json() if w["source"] == "challenge"
    )
    assert (logged["sets"], logged["reps"]) == (1, 40)

    client.put(f"/api/challenge/items/{item['id']}", json={"target_reps": 100})

    still = next(w for w in client.get("/api/workouts").get_json() if w["id"] == logged["id"])
    assert still["reps"] == 40, "an old session was rewritten to the new target"


def test_the_next_tick_uses_the_new_target(client):
    """The other half: the change has to actually take effect going forward."""
    item = _item(client)
    client.put(f"/api/challenge/items/{item['id']}",
               json={"target_sets": 1, "target_reps": 40})
    client.post("/api/challenge/toggle", json={"item_id": item["id"]})
    client.post("/api/challenge/toggle", json={"item_id": item["id"]})  # un-tick

    client.put(f"/api/challenge/items/{item['id']}", json={"target_reps": 60})
    client.post("/api/challenge/toggle", json={"item_id": item["id"]})

    logged = next(
        w for w in client.get("/api/workouts").get_json() if w["source"] == "challenge"
    )
    assert logged["reps"] == 60


def test_a_timed_exercise_behaves_the_same(client):
    item = _item(client)
    client.put(f"/api/challenge/items/{item['id']}",
               json={"target_sets": 1, "target_seconds": 60, "target_reps": ""})
    client.post("/api/challenge/toggle", json={"item_id": item["id"]})

    logged = next(
        w for w in client.get("/api/workouts").get_json() if w["source"] == "challenge"
    )
    assert logged["duration_sec"] == 60

    client.put(f"/api/challenge/items/{item['id']}", json={"target_seconds": 90})
    still = next(w for w in client.get("/api/workouts").get_json() if w["id"] == logged["id"])
    assert still["duration_sec"] == 60


def test_the_label_shows_the_current_target_everywhere(client, conn):
    """The one place a change is visible retrospectively: the item's label is a
    single current value, so a past day is *displayed* with today's target even
    though it was neither judged nor logged against it."""
    item = _item(client)
    client.put(f"/api/challenge/items/{item['id']}",
               json={"target_sets": 3, "target_reps": 40})
    conn.execute("INSERT INTO challenge_completions (item_id, day) VALUES (?, ?)",
                 (item["id"], (date.today() - timedelta(days=1)).isoformat()))
    conn.commit()

    client.put(f"/api/challenge/items/{item['id']}", json={"target_reps": 50})

    label = next(
        i for i in client.get("/api/challenge").get_json()["items"] if i["id"] == item["id"]
    )["label"]
    assert "50" in label and "40" not in label
