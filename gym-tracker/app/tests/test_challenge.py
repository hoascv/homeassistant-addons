from datetime import date, timedelta


def test_challenge_items_seeded(client):
    data = client.get("/api/challenge").get_json()
    items = data["items"]
    assert [i["item_type"] for i in items] == ["supplement", "exercise", "exercise"]
    assert [i["name"] for i in items] == ["Creatine", "Push-up", "Squat"]
    assert items[0]["label"] == "Creatine · 5 g"
    assert items[1]["label"] == "Push-up · 40 reps"
    assert data["streak"] == 0
    assert data["complete_today"] is False
    assert len(data["last_7_days"]) == 7


def test_toggle_marks_done(client):
    item_id = client.get("/api/challenge").get_json()["items"][0]["id"]
    res = client.post("/api/challenge/toggle", json={"item_id": item_id}).get_json()
    assert res["done"] is True
    data = client.get("/api/challenge").get_json()
    assert data["items"][0]["done_today"] is True
    # toggling again clears it
    res = client.post("/api/challenge/toggle", json={"item_id": item_id}).get_json()
    assert res["done"] is False


def test_complete_today_when_all_ticked(client):
    for item in client.get("/api/challenge").get_json()["items"]:
        client.post("/api/challenge/toggle", json={"item_id": item["id"]})
    data = client.get("/api/challenge").get_json()
    assert data["complete_today"] is True
    assert data["streak"] == 1


def test_streak_counts_consecutive_full_days(client, conn):
    items = [i["id"] for i in client.get("/api/challenge").get_json()["items"]]
    today = date.today()
    # Complete the 3 days *before* today fully; leave today untouched.
    for off in range(1, 4):
        day = (today - timedelta(days=off)).isoformat()
        for i in items:
            conn.execute("INSERT INTO challenge_completions (item_id, day) VALUES (?, ?)", (i, day))
    conn.commit()
    # An unfinished today should not break the 3-day streak behind it.
    assert client.get("/api/challenge").get_json()["streak"] == 3


def test_streak_breaks_on_partial_day(client, conn):
    items = [i["id"] for i in client.get("/api/challenge").get_json()["items"]]
    today = date.today()
    # yesterday fully complete, day-before only partially -> streak stops at 1
    for i in items:
        conn.execute(
            "INSERT INTO challenge_completions (item_id, day) VALUES (?, ?)",
            (i, (today - timedelta(days=1)).isoformat()),
        )
    conn.execute(
        "INSERT INTO challenge_completions (item_id, day) VALUES (?, ?)",
        (items[0], (today - timedelta(days=2)).isoformat()),
    )
    conn.commit()
    assert client.get("/api/challenge").get_json()["streak"] == 1


def _first_exercise_id(client):
    return client.get("/api/exercises").get_json()[0]["exercises"][0]["id"]


def _first_supplement_id(client):
    return client.get("/api/supplements").get_json()[0]["id"]


def test_add_exercise_challenge_item(client):
    ex_id = _first_exercise_id(client)
    res = client.post(
        "/api/challenge/items",
        json={"item_type": "exercise", "exercise_id": ex_id, "target_reps": 25},
    )
    assert res.status_code == 201
    new_id = res.get_json()["id"]
    item = next(i for i in client.get("/api/challenge/items").get_json() if i["id"] == new_id)
    assert item["item_type"] == "exercise"
    assert item["target_reps"] == 25
    assert item["label"].endswith("· 25 reps")

    # edit the rep target
    assert client.put(f"/api/challenge/items/{new_id}", json={"target_reps": 30}).status_code == 200
    item = next(i for i in client.get("/api/challenge/items").get_json() if i["id"] == new_id)
    assert item["target_reps"] == 30

    # archive
    assert client.delete(f"/api/challenge/items/{new_id}").status_code == 200
    assert new_id not in [i["id"] for i in client.get("/api/challenge/items").get_json()]


def test_add_supplement_challenge_item(client):
    sup_id = _first_supplement_id(client)
    res = client.post(
        "/api/challenge/items",
        json={"item_type": "supplement", "supplement_id": sup_id, "dose": "10 g"},
    )
    assert res.status_code == 201
    new_id = res.get_json()["id"]
    item = next(i for i in client.get("/api/challenge/items").get_json() if i["id"] == new_id)
    assert item["item_type"] == "supplement"
    assert item["dose"] == "10 g"


def test_challenge_item_type_validation(client):
    # free text / no type is rejected — items must reference the library
    assert client.post("/api/challenge/items", json={"label": "10 min stretch"}).status_code == 400
    assert client.post("/api/challenge/items", json={"item_type": "exercise"}).status_code == 400
    assert client.post("/api/challenge/items", json={"item_type": "exercise", "exercise_id": 999}).status_code == 400
    assert client.post("/api/challenge/items", json={"item_type": "supplement", "supplement_id": 999}).status_code == 400


def test_ticking_exercise_item_logs_and_unlogs_workout(client):
    ex_id = _first_exercise_id(client)
    item_id = client.post(
        "/api/challenge/items",
        json={"item_type": "exercise", "exercise_id": ex_id, "target_sets": 3, "target_reps": 40},
    ).get_json()["id"]

    # tick it on -> a workout appears, sourced from the challenge
    client.post("/api/challenge/toggle", json={"item_id": item_id, "day": "2026-07-20"})
    workouts = client.get("/api/workouts?date=2026-07-20").get_json()
    assert len(workouts) == 1
    assert workouts[0]["exercise_id"] == ex_id
    assert workouts[0]["sets"] == 3 and workouts[0]["reps"] == 40
    assert workouts[0]["source"] == "challenge"

    # tick it off -> the auto-logged workout is removed again
    client.post("/api/challenge/toggle", json={"item_id": item_id, "day": "2026-07-20"})
    assert client.get("/api/workouts?date=2026-07-20").get_json() == []


def test_ticking_supplement_item_logs_no_workout(client):
    sup_id = _first_supplement_id(client)
    item_id = client.post(
        "/api/challenge/items", json={"item_type": "supplement", "supplement_id": sup_id}
    ).get_json()["id"]
    client.post("/api/challenge/toggle", json={"item_id": item_id, "day": "2026-07-20"})
    assert client.get("/api/workouts?date=2026-07-20").get_json() == []


def test_challenge_history_matrix(client):
    data = client.get("/api/challenge/history?days=10").get_json()
    assert len(data["days"]) == 10
    assert len(data["items"]) == 3
    # newest first
    assert data["days"][0]["day"] > data["days"][1]["day"]
    # backfill a past day via toggle, then it shows in history
    item_id = data["items"][0]["id"]
    client.post("/api/challenge/toggle", json={"item_id": item_id, "day": data["days"][3]["day"]})
    refreshed = client.get("/api/challenge/history?days=10").get_json()
    assert item_id in refreshed["days"][3]["done"]


def test_challenge_history_explicit_range(client):
    data = client.get("/api/challenge/history?from=2026-06-01&to=2026-06-07").get_json()
    assert data["from"] == "2026-06-01" and data["to"] == "2026-06-07"
    days = [d["day"] for d in data["days"]]
    assert len(days) == 7
    assert days[0] == "2026-06-07"  # newest first
    assert days[-1] == "2026-06-01"


def test_challenge_history_backfill_old_day(client):
    # Backfilling a day well outside the default 14-day window — the Excel
    # import case: toggle every item complete on an old date, then read it
    # back through an explicit range.
    items = client.get("/api/challenge/items").get_json()
    for it in items:
        client.post("/api/challenge/toggle", json={"item_id": it["id"], "day": "2026-05-20"})
    data = client.get("/api/challenge/history?from=2026-05-18&to=2026-05-22").get_json()
    may20 = next(d for d in data["days"] if d["day"] == "2026-05-20")
    assert may20["complete"] is True


def test_challenge_history_span_is_capped(client):
    # An over-wide range is clamped rather than returning thousands of rows.
    data = client.get("/api/challenge/history?from=2000-01-01&to=2026-07-24").get_json()
    assert len(data["days"]) <= 370
    assert data["to"] == "2026-07-24"


def test_archived_item_not_required_for_streak(client, conn):
    """Archiving an item removes it from the 'all items done' bar, so a day
    where the remaining items are done still counts."""
    data = client.get("/api/challenge").get_json()
    items = data["items"]
    # Complete only the first two items today, then archive the third.
    for item in items[:2]:
        client.post("/api/challenge/toggle", json={"item_id": item["id"]})
    assert client.get("/api/challenge").get_json()["complete_today"] is False
    client.delete(f"/api/challenge/items/{items[2]['id']}")
    assert client.get("/api/challenge").get_json()["complete_today"] is True


def test_challenge_validation(client):
    assert client.post("/api/challenge/toggle", json={}).status_code == 400
    assert client.post("/api/challenge/toggle", json={"item_id": 999}).status_code == 404
    assert client.post("/api/challenge/items", json={"label": "  "}).status_code == 400
    assert client.put("/api/challenge/items/999", json={"label": "x"}).status_code == 404
    assert client.delete("/api/challenge/items/999").status_code == 404


# --- Multiple challenges, and time-boxed ones -------------------------------


def _new_challenge(client, name, start, end=None):
    body = {"name": name, "start_date": start}
    if end:
        body["end_date"] = end
    r = client.post("/api/challenges", json=body)
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _add_exercise_item(client, conn, challenge_id, name):
    ex = conn.execute("SELECT id FROM exercises WHERE archived = 0 LIMIT 1").fetchone()["id"]
    r = client.post("/api/challenge/items", json={
        "item_type": "exercise", "exercise_id": ex, "target_reps": 10,
        "challenge_id": challenge_id,
    })
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def test_existing_items_are_migrated_into_a_default_challenge(client, conn):
    challenges = client.get("/api/challenges").get_json()
    assert len(challenges) == 1
    assert challenges[0]["name"] == "Daily challenge"
    assert challenges[0]["end_date"] is None  # open-ended
    assert challenges[0]["items"]  # the seeded items came with it
    orphans = conn.execute(
        "SELECT COUNT(*) FROM challenge_items WHERE challenge_id IS NULL AND archived = 0"
    ).fetchone()[0]
    assert orphans == 0


def test_challenges_are_independent(client, conn):
    second = _new_challenge(client, "30-day squats", "2026-07-25", "2026-08-23")
    item = _add_exercise_item(client, conn, second, "squat")

    client.post("/api/challenge/toggle", json={"item_id": item})
    views = {c["name"]: c for c in client.get("/api/challenges").get_json()}

    assert views["30-day squats"]["complete_today"] is True
    # Ticking one challenge says nothing about the other.
    assert views["Daily challenge"]["complete_today"] is False
    assert len(views["30-day squats"]["items"]) == 1


def test_a_time_boxed_challenge_reports_its_day_number(client):
    from datetime import date, timedelta

    start = (date.today() - timedelta(days=3)).isoformat()
    end = (date.today() + timedelta(days=26)).isoformat()
    cid = _new_challenge(client, "30-day squats", start, end)

    view = next(c for c in client.get("/api/challenges").get_json() if c["id"] == cid)
    assert view["total_days"] == 30
    assert view["day_number"] == 4
    assert view["finished"] is False


def test_a_challenge_past_its_end_date_is_finished(client):
    cid = _new_challenge(client, "June push-ups", "2026-06-01", "2026-06-30")
    view = next(c for c in client.get("/api/challenges").get_json() if c["id"] == cid)
    assert view["finished"] is True


def test_end_date_must_not_precede_the_start(client):
    r = client.post("/api/challenges", json={
        "name": "Backwards", "start_date": "2026-07-10", "end_date": "2026-07-01",
    })
    assert r.status_code == 400
    assert "end_date" in r.get_json()["error"]


def test_a_challenge_is_archived_not_deleted(client, conn):
    cid = _new_challenge(client, "Temp", "2026-07-01")
    assert client.delete(f"/api/challenges/{cid}").status_code == 200
    assert [c for c in client.get("/api/challenges").get_json() if c["id"] == cid] == []
    # The record survives, so its completions stay meaningful.
    assert conn.execute("SELECT archived FROM challenges WHERE id = ?", (cid,)).fetchone()[0] == 1


# --- Statistics -------------------------------------------------------------


def test_stats_report_completion_streaks_and_items(client, conn):
    from datetime import date, timedelta

    start = (date.today() - timedelta(days=4)).isoformat()
    cid = _new_challenge(client, "Squats", start)
    item = _add_exercise_item(client, conn, cid, "squat")

    # Done on 3 of the 5 days, the last two consecutive.
    for offset in (4, 1, 0):
        day = (date.today() - timedelta(days=offset)).isoformat()
        client.post("/api/challenge/toggle", json={"item_id": item, "day": day})

    stats = next(s for s in client.get("/api/challenges/stats").get_json() if s["id"] == cid)
    assert stats["days_elapsed"] == 5
    assert stats["days_complete"] == 3
    assert stats["completion_pct"] == 60.0
    assert stats["current_streak"] == 2
    assert stats["longest_streak"] == 2
    assert len(stats["days"]) == 5
    assert stats["items"][0]["days_done"] == 3
    assert stats["items"][0]["rate_pct"] == 60.0


def test_stats_only_count_days_the_challenge_was_running(client, conn):
    from datetime import date, timedelta

    # Ends in the past: elapsed days stop at the end date, not today.
    start = (date.today() - timedelta(days=10)).isoformat()
    end = (date.today() - timedelta(days=6)).isoformat()
    cid = _new_challenge(client, "Short run", start, end)
    _add_exercise_item(client, conn, cid, "squat")

    stats = next(s for s in client.get("/api/challenges/stats").get_json() if s["id"] == cid)
    assert stats["days_elapsed"] == 5
    assert stats["finished"] is True


def test_stats_include_volume_from_the_challenge(client, conn):
    from datetime import date

    cid = _new_challenge(client, "Squats", date.today().isoformat())
    item = _add_exercise_item(client, conn, cid, "squat")
    client.post("/api/challenge/toggle", json={"item_id": item})
    conn.execute(
        "UPDATE workout_logs SET sets = 3, reps = 10, hr_avg = 130, hr_max = 150 "
        "WHERE challenge_item_id = ?",
        (item,),
    )
    conn.commit()

    stats = next(s for s in client.get("/api/challenges/stats").get_json() if s["id"] == cid)
    assert stats["volume"]["sessions"] == 1
    assert stats["volume"]["reps"] == 30
    assert stats["volume"]["hr_avg"] == 130 and stats["volume"]["hr_max"] == 150


# --- Item membership windows ------------------------------------------------


def _backdate_setup(conn, challenge_id, day):
    """Make a challenge (and the items it already has) look as though it was
    set up on `day`, so later additions are genuinely later."""
    conn.execute("UPDATE challenges SET created_at = ? WHERE id = ?", (f"{day}T08:00:00", challenge_id))
    conn.execute(
        "UPDATE challenge_items SET created_at = ? WHERE challenge_id = ?",
        (f"{day}T08:05:00", challenge_id),
    )
    conn.commit()


def test_adding_an_item_does_not_invalidate_earlier_days(client, conn):
    from datetime import date, timedelta

    today = date.today()
    start = (today - timedelta(days=9)).isoformat()
    cid = _new_challenge(client, "Squats", start)
    first = _add_exercise_item(client, conn, cid, "squat")
    _backdate_setup(conn, cid, start)
    for off in range(10):
        client.post("/api/challenge/toggle", json={
            "item_id": first, "day": (today - timedelta(days=off)).isoformat(),
        })

    before = next(s for s in client.get("/api/challenges/stats").get_json() if s["id"] == cid)
    assert before["completion_pct"] == 100.0 and before["current_streak"] == 10

    _add_exercise_item(client, conn, cid, "push-up")  # added today
    after = next(s for s in client.get("/api/challenges/stats").get_json() if s["id"] == cid)

    # Only today is affected: the nine days before the item existed still count.
    assert after["days_complete"] == 9
    assert after["completion_pct"] == 90.0
    assert after["current_streak"] == 9
    # And the newcomer is scored over its one day, not the whole run.
    newcomer = next(i for i in after["items"] if i["days_member"] == 1)
    assert newcomer["days_done"] == 0 and newcomer["rate_pct"] == 0.0


def test_backfilling_an_item_pulls_its_membership_back(client, conn):
    from datetime import date, timedelta

    today = date.today()
    start = (today - timedelta(days=5)).isoformat()
    cid = _new_challenge(client, "Squats", start)
    item = _add_exercise_item(client, conn, cid, "squat")
    _backdate_setup(conn, cid, start)
    later = _add_exercise_item(client, conn, cid, "push-up")  # added today

    # Ticking the newcomer on an earlier day says it applied then, so that day
    # starts requiring it.
    day = (today - timedelta(days=3)).isoformat()
    client.post("/api/challenge/toggle", json={"item_id": later, "day": day})
    client.post("/api/challenge/toggle", json={"item_id": item, "day": day})

    stats = next(s for s in client.get("/api/challenges/stats").get_json() if s["id"] == cid)
    newcomer = next(i for i in stats["items"] if i["id"] == later)
    assert newcomer["days_member"] == 4  # from that backfilled day to today
    assert newcomer["days_done"] == 1
    assert any(d["day"] == day and d["complete"] for d in stats["days"])


def test_an_archived_item_keeps_the_days_it_was_part_of(client, conn):
    from datetime import date, timedelta

    today = date.today()
    start = (today - timedelta(days=4)).isoformat()
    cid = _new_challenge(client, "Squats", start)
    item = _add_exercise_item(client, conn, cid, "squat")
    _backdate_setup(conn, cid, start)
    for off in range(5):
        client.post("/api/challenge/toggle", json={
            "item_id": item, "day": (today - timedelta(days=off)).isoformat(),
        })

    client.delete(f"/api/challenge/items/{item}")
    stats = next(s for s in client.get("/api/challenges/stats").get_json() if s["id"] == cid)

    # Removing it doesn't erase the record of the days it was done.
    archived = next(i for i in stats["items"] if i["id"] == item)
    assert archived["archived"] is True
    assert archived["days_done"] == 5
    assert stats["days_complete"] == 5
    assert stats["item_count"] == 0  # nothing active left


def test_items_archived_before_archive_times_were_recorded_are_left_out(client, conn):
    from datetime import date, timedelta

    today = date.today()
    cid = _new_challenge(client, "Squats", (today - timedelta(days=2)).isoformat())
    item = _add_exercise_item(client, conn, cid, "squat")
    _backdate_setup(conn, cid, (today - timedelta(days=2)).isoformat())
    # An upgrade leaves old archived rows with no archived_at.
    conn.execute(
        "UPDATE challenge_items SET archived = 1, archived_at = NULL WHERE id = ?", (item,)
    )
    conn.commit()

    stats = next(s for s in client.get("/api/challenges/stats").get_json() if s["id"] == cid)
    # It can't be placed in time, so it is excluded rather than allowed to
    # rewrite days as incomplete.
    assert stats["items"] == []
    assert stats["days_complete"] == 0
