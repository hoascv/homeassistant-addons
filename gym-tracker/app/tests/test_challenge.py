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


# --- Bad input answers, rather than crashing --------------------------------


def test_junk_challenge_id_is_a_bad_request(client):
    for url in ("/api/challenge/items?challenge_id=abc", "/api/challenge/history?challenge_id=abc"):
        r = client.get(url)
        assert r.status_code == 400, url
        assert "challenge_id" in r.get_json()["error"]


def test_an_absurd_name_is_trimmed_not_stored_whole(client):
    cid = client.post("/api/challenges", json={
        "name": "x" * 5000, "start_date": "2026-07-01",
    }).get_json()["id"]
    name = next(c for c in client.get("/api/challenges").get_json() if c["id"] == cid)["name"]
    assert len(name) == 80


def test_stats_volume_stops_at_the_end_date(client, conn):
    from datetime import date, timedelta

    today = date.today()
    end = (today - timedelta(days=2)).isoformat()
    cid = _new_challenge(client, "Finished", (today - timedelta(days=6)).isoformat(), end)
    item = _add_exercise_item(client, conn, cid, "squat")
    # One session inside the period, one logged after it ended.
    for day, sets in (((today - timedelta(days=4)).isoformat(), 3), (today.isoformat(), 9)):
        conn.execute(
            "INSERT INTO workout_logs (ts, exercise_id, sets, reps, source, challenge_item_id, ts_exact) "
            "SELECT ?, exercise_id, ?, 10, 'challenge', ?, 1 FROM challenge_items WHERE id = ?",
            (f"{day}T09:00:00", sets, item, item),
        )
    conn.commit()

    stats = next(s for s in client.get("/api/challenges/stats").get_json() if s["id"] == cid)
    assert stats["volume"]["sessions"] == 1  # the later one is outside the challenge
    assert stats["volume"]["reps"] == 30


def test_a_future_challenge_has_not_started(client):
    from datetime import date, timedelta

    start = (date.today() + timedelta(days=3)).isoformat()
    cid = _new_challenge(client, "Next week", start)
    view = next(c for c in client.get("/api/challenges").get_json() if c["id"] == cid)
    assert view["not_started"] is True
    assert view["streak"] == 0

    stats = next(s for s in client.get("/api/challenges/stats").get_json() if s["id"] == cid)
    assert stats["days_elapsed"] == 0
    assert stats["completion_pct"] is None  # no days yet, so no rate to report


def test_a_finished_challenge_keeps_the_streak_it_ended_on(client, conn):
    from datetime import date, timedelta

    today = date.today()
    start = (today - timedelta(days=9)).isoformat()
    end = (today - timedelta(days=3)).isoformat()
    cid = _new_challenge(client, "Done", start, end)
    item = _add_exercise_item(client, conn, cid, "squat")
    _backdate_setup(conn, cid, start)
    for off in range(3, 10):  # every day it ran, perfectly
        client.post("/api/challenge/toggle", json={
            "item_id": item, "day": (today - timedelta(days=off)).isoformat(),
        })

    stats = next(s for s in client.get("/api/challenges/stats").get_json() if s["id"] == cid)
    assert stats["completion_pct"] == 100.0
    # Counted to its end date, not to today: a perfect run doesn't become a
    # streak of zero just because the challenge is over.
    assert stats["current_streak"] == 7
    assert stats["longest_streak"] == 7


# --- Moving an item between challenges --------------------------------------


def test_moving_an_item_leaves_its_earned_days_behind(client, conn):
    from datetime import date, timedelta

    today = date.today()
    start = (today - timedelta(days=5)).isoformat()
    origin = _new_challenge(client, "Morning", start)
    item = _add_exercise_item(client, conn, origin, "squat")
    _backdate_setup(conn, origin, start)
    for off in range(1, 6):
        client.post("/api/challenge/toggle", json={
            "item_id": item, "day": (today - timedelta(days=off)).isoformat(),
        })
    target = _new_challenge(client, "Evening", today.isoformat())

    r = client.post(f"/api/challenge/items/{item}/move", json={"challenge_id": target})
    assert r.status_code == 200
    new_id = r.get_json()["id"]

    stats = {s["id"]: s for s in client.get("/api/challenges/stats").get_json()}
    # The days were earned under the old challenge and stay there.
    assert stats[origin]["days_complete"] == 5
    old_item = next(i for i in stats[origin]["items"] if i["id"] == item)
    assert old_item["archived"] is True and old_item["days_done"] == 5
    # The new challenge starts clean rather than inheriting them.
    assert stats[target]["days_complete"] == 0
    moved = next(i for i in stats[target]["items"] if i["id"] == new_id)
    assert moved["days_done"] == 0

    # The item is gone from the old challenge's live list, present in the new.
    assert item not in [i["id"] for i in client.get(f"/api/challenge/items?challenge_id={origin}").get_json()]
    assert new_id in [i["id"] for i in client.get(f"/api/challenge/items?challenge_id={target}").get_json()]


def test_a_moved_item_keeps_its_target_and_records_its_origin(client, conn):
    from datetime import date

    origin = _new_challenge(client, "Morning", date.today().isoformat())
    ex = conn.execute("SELECT id FROM exercises WHERE archived = 0 LIMIT 1").fetchone()["id"]
    item = client.post("/api/challenge/items", json={
        "item_type": "exercise", "exercise_id": ex, "target_sets": 4, "target_reps": 12,
        "challenge_id": origin,
    }).get_json()["id"]
    target = _new_challenge(client, "Evening", date.today().isoformat())

    new_id = client.post(f"/api/challenge/items/{item}/move", json={
        "challenge_id": target,
    }).get_json()["id"]

    row = conn.execute("SELECT * FROM challenge_items WHERE id = ?", (new_id,)).fetchone()
    assert row["target_sets"] == 4 and row["target_reps"] == 12
    assert row["moved_from"] == item  # the lineage is recorded
    assert row["challenge_id"] == target


def test_moving_rejects_a_bad_destination(client, conn):
    from datetime import date

    origin = _new_challenge(client, "Morning", date.today().isoformat())
    item = _add_exercise_item(client, conn, origin, "squat")

    assert client.post(f"/api/challenge/items/{item}/move", json={"challenge_id": origin}).status_code == 400
    assert client.post(f"/api/challenge/items/{item}/move", json={"challenge_id": 9999}).status_code == 400
    assert client.post(f"/api/challenge/items/{item}/move", json={}).status_code == 400
    assert client.post("/api/challenge/items/9999/move", json={"challenge_id": origin}).status_code == 404


# --- Weight alongside adherence ---------------------------------------------


def test_stats_include_weigh_ins_from_the_challenge_period(client, conn):
    from datetime import date, timedelta

    today = date.today()
    start = (today - timedelta(days=6)).isoformat()
    cid = _new_challenge(client, "Cut", start)
    _add_exercise_item(client, conn, cid, "squat")
    # One before the challenge began, two inside it.
    for day, kg, bf in ((today - timedelta(days=20), 103.0, 22.0),
                        (today - timedelta(days=5), 102.0, 21.0),
                        (today - timedelta(days=1), 101.2, 20.4)):
        conn.execute(
            "INSERT INTO weight_logs (ts, weight_kg, body_fat_pct, ts_exact) VALUES (?, ?, ?, 0)",
            (f"{day.isoformat()}T12:00:00", kg, bf),
        )
    conn.commit()

    stats = next(s for s in client.get("/api/challenges/stats").get_json() if s["id"] == cid)
    weight = stats["weight"]
    assert [p["day"] for p in weight["points"]] == [
        (today - timedelta(days=5)).isoformat(), (today - timedelta(days=1)).isoformat(),
    ]
    assert weight["start_kg"] == 102.0 and weight["end_kg"] == 101.2
    assert weight["delta_kg"] == -0.8
    assert weight["delta_bf"] == -0.6


def test_stats_weight_is_empty_without_weigh_ins_in_the_period(client, conn):
    from datetime import date, timedelta

    cid = _new_challenge(client, "Cut", (date.today() + timedelta(days=2)).isoformat())
    stats = next(s for s in client.get("/api/challenges/stats").get_json() if s["id"] == cid)
    assert stats["weight"]["points"] == []
    assert stats["weight"]["delta_kg"] is None


# --- Repeating a challenge --------------------------------------------------


def test_repeating_clones_the_items_with_fresh_dates(client, conn):
    from datetime import date, timedelta

    cid = _new_challenge(client, "30-day squats", "2026-06-01", "2026-06-30")
    _add_exercise_item(client, conn, cid, "squat")

    r = client.post(f"/api/challenges/{cid}/repeat", json={})
    assert r.status_code == 201
    new_id = r.get_json()["id"]

    fresh = next(c for c in client.get("/api/challenges").get_json() if c["id"] == new_id)
    assert fresh["name"] == "30-day squats"
    assert fresh["start_date"] == date.today().isoformat()
    # 30 days again, counted from today rather than reusing the old dates.
    assert fresh["end_date"] == (date.today() + timedelta(days=29)).isoformat()
    assert fresh["total_days"] == 30
    assert len(fresh["items"]) == 1

    row = conn.execute("SELECT repeat_of FROM challenges WHERE id = ?", (new_id,)).fetchone()
    assert row["repeat_of"] == cid


def test_repeating_leaves_the_original_alone(client, conn):
    from datetime import date, timedelta

    today = date.today()
    start = (today - timedelta(days=4)).isoformat()
    cid = _new_challenge(client, "Squats", start)
    item = _add_exercise_item(client, conn, cid, "squat")
    _backdate_setup(conn, cid, start)
    for off in range(1, 5):
        client.post("/api/challenge/toggle", json={
            "item_id": item, "day": (today - timedelta(days=off)).isoformat(),
        })

    new_id = client.post(f"/api/challenges/{cid}/repeat", json={}).get_json()["id"]
    stats = {s["id"]: s for s in client.get("/api/challenges/stats").get_json()}

    assert stats[cid]["days_complete"] == 4  # untouched
    assert stats[new_id]["days_complete"] == 0  # a fresh run, not a reset
    # The clone is its own item, so ticking it can't affect the original.
    assert {i["id"] for i in stats[cid]["items"]}.isdisjoint({i["id"] for i in stats[new_id]["items"]})


def test_repeating_accepts_explicit_dates_and_a_name(client, conn):
    cid = _new_challenge(client, "30-day squats", "2026-06-01", "2026-06-30")
    _add_exercise_item(client, conn, cid, "squat")

    new_id = client.post(f"/api/challenges/{cid}/repeat", json={
        "name": "60-day squats", "start_date": "2026-09-01", "end_date": "2026-10-30",
    }).get_json()["id"]

    fresh = next(c for c in client.get("/api/challenges").get_json() if c["id"] == new_id)
    assert fresh["name"] == "60-day squats"
    assert fresh["start_date"] == "2026-09-01" and fresh["end_date"] == "2026-10-30"


def test_repeating_a_missing_challenge_is_a_404(client):
    assert client.post("/api/challenges/9999/repeat", json={}).status_code == 404


# --- Schedules --------------------------------------------------------------


def _scheduled_challenge(client, name, start, **schedule):
    body = {"name": name, "start_date": start, **schedule}
    r = client.post("/api/challenges", json=body)
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def test_predicate_every_n_days_is_anchored_on_the_start(client):
    from datetime import date, timedelta

    import app as gymapp

    ch = {"schedule_kind": "interval", "schedule_interval": 2, "start_date": "2026-08-03"}
    start = date(2026, 8, 3)
    due = [gymapp._challenge_scheduled_on(ch, start + timedelta(days=i)) for i in range(5)]
    assert due == [True, False, True, False, True]


def test_predicate_weekdays(client):
    from datetime import date

    import app as gymapp

    ch = {"schedule_kind": "weekdays", "schedule_weekdays": "0,2,4", "start_date": "2026-08-03"}
    # Mon 3 Aug 2026 through Sun 9 Aug.
    week = [gymapp._challenge_scheduled_on(ch, date(2026, 8, 3 + i)) for i in range(7)]
    assert week == [True, False, True, False, True, False, False]


def test_a_broken_schedule_falls_back_to_daily(client):
    from datetime import date

    import app as gymapp

    for ch in (
        {"schedule_kind": "interval", "schedule_interval": None, "start_date": "2026-08-03"},
        {"schedule_kind": "weekdays", "schedule_weekdays": "", "start_date": "2026-08-03"},
        {"schedule_kind": "nonsense", "start_date": "2026-08-03"},
    ):
        assert gymapp._challenge_scheduled_on(ch, date(2026, 8, 4)) is True


def test_a_weekday_challenge_kept_perfectly_is_a_hundred_percent(client, conn):
    from datetime import date, timedelta

    # Four weeks back, so every weekday appears several times.
    today = date.today()
    start = (today - timedelta(days=27)).isoformat()
    weekdays = ",".join(str(d) for d in (today.weekday(), (today.weekday() + 2) % 7))
    cid = _scheduled_challenge(
        client, "Gym days", start, schedule_kind="weekdays", schedule_weekdays=weekdays,
    )
    item = _add_exercise_item(client, conn, cid, "squat")
    _backdate_setup(conn, cid, start)

    due = [
        (today - timedelta(days=off))
        for off in range(28)
        if str((today - timedelta(days=off)).weekday()) in weekdays.split(",")
    ]
    for day in due:
        client.post("/api/challenge/toggle", json={"item_id": item, "day": day.isoformat()})

    stats = next(s for s in client.get("/api/challenges/stats").get_json() if s["id"] == cid)
    assert stats["days_elapsed"] == len(due)  # not 28
    assert stats["completion_pct"] == 100.0
    assert stats["schedule"]["kind"] == "weekdays"


def test_a_rest_day_does_not_break_the_streak(client, conn):
    from datetime import date, timedelta

    today = date.today()
    start = (today - timedelta(days=8)).isoformat()
    cid = _scheduled_challenge(
        client, "Every other day", start, schedule_kind="interval", schedule_interval=2,
    )
    item = _add_exercise_item(client, conn, cid, "squat")
    _backdate_setup(conn, cid, start)

    anchor = date.fromisoformat(start)
    due = [anchor + timedelta(days=i) for i in range(0, 9, 2)]
    for day in due:
        client.post("/api/challenge/toggle", json={"item_id": item, "day": day.isoformat()})

    stats = next(s for s in client.get("/api/challenges/stats").get_json() if s["id"] == cid)
    # Five due days in a row, with rest days in between that don't count.
    assert stats["days_elapsed"] == len(due)
    assert stats["current_streak"] == len(due)
    assert stats["longest_streak"] == len(due)


def test_a_missed_due_day_still_breaks_the_streak(client, conn):
    from datetime import date, timedelta

    today = date.today()
    start = (today - timedelta(days=8)).isoformat()
    cid = _scheduled_challenge(
        client, "Every other day", start, schedule_kind="interval", schedule_interval=2,
    )
    item = _add_exercise_item(client, conn, cid, "squat")
    _backdate_setup(conn, cid, start)

    anchor = date.fromisoformat(start)
    due = [anchor + timedelta(days=i) for i in range(0, 9, 2)]
    for day in due[:-2] + due[-1:]:  # skip the second-to-last due day
        client.post("/api/challenge/toggle", json={"item_id": item, "day": day.isoformat()})

    stats = next(s for s in client.get("/api/challenges/stats").get_json() if s["id"] == cid)
    assert stats["current_streak"] == 1
    assert stats["days_complete"] == len(due) - 1


def test_a_tick_on_a_rest_day_changes_nothing(client, conn):
    from datetime import date, timedelta

    today = date.today()
    start = (today - timedelta(days=6)).isoformat()
    cid = _scheduled_challenge(
        client, "Every other day", start, schedule_kind="interval", schedule_interval=2,
    )
    item = _add_exercise_item(client, conn, cid, "squat")
    _backdate_setup(conn, cid, start)
    anchor = date.fromisoformat(start)
    for i in range(0, 7, 2):
        client.post("/api/challenge/toggle", json={"item_id": item, "day": (anchor + timedelta(days=i)).isoformat()})
    before = next(s for s in client.get("/api/challenges/stats").get_json() if s["id"] == cid)

    # A bonus session on a rest day: recorded, but it can't move the numbers.
    client.post("/api/challenge/toggle", json={
        "item_id": item, "day": (anchor + timedelta(days=1)).isoformat(),
    })
    after = next(s for s in client.get("/api/challenges/stats").get_json() if s["id"] == cid)

    assert after["completion_pct"] == before["completion_pct"]
    assert after["days_elapsed"] == before["days_elapsed"]
    assert after["current_streak"] == before["current_streak"]


def test_the_view_says_whether_it_is_due_today(client, conn):
    from datetime import date, timedelta

    today = date.today()
    tomorrow_only = str((today.weekday() + 1) % 7)
    cid = _scheduled_challenge(
        client, "Tomorrow only", today.isoformat(),
        schedule_kind="weekdays", schedule_weekdays=tomorrow_only,
    )
    _add_exercise_item(client, conn, cid, "squat")

    view = next(c for c in client.get("/api/challenges").get_json() if c["id"] == cid)
    assert view["due_today"] is False
    assert view["next_due"] == (today + timedelta(days=1)).isoformat()
    assert [d["scheduled"] for d in view["last_7_days"]].count(True) == 1


def test_repeat_carries_the_schedule(client, conn):
    cid = _scheduled_challenge(
        client, "Gym days", "2026-06-01", schedule_kind="weekdays", schedule_weekdays="0,2,4",
    )
    _add_exercise_item(client, conn, cid, "squat")

    new_id = client.post(f"/api/challenges/{cid}/repeat", json={}).get_json()["id"]
    fresh = next(c for c in client.get("/api/challenges").get_json() if c["id"] == new_id)
    assert fresh["schedule"] == {"kind": "weekdays", "interval": None, "weekdays": [0, 2, 4]}


def test_schedule_validation(client):
    bad = [
        {"schedule_kind": "interval", "schedule_interval": 1},
        {"schedule_kind": "interval", "schedule_interval": 0},
        {"schedule_kind": "interval", "schedule_interval": "abc"},
        {"schedule_kind": "interval", "schedule_interval": 400},
        {"schedule_kind": "weekdays", "schedule_weekdays": ""},
        {"schedule_kind": "weekdays", "schedule_weekdays": "9"},
        {"schedule_kind": "sometimes"},
    ]
    for schedule in bad:
        r = client.post("/api/challenges", json={
            "name": "Bad", "start_date": "2026-08-01", **schedule,
        })
        assert r.status_code == 400, schedule


def test_existing_challenges_are_daily(client):
    view = client.get("/api/challenges").get_json()[0]
    assert view["schedule"] == {"kind": "daily", "interval": None, "weekdays": []}
    assert view["due_today"] is True


# --- An explicit join date --------------------------------------------------


def test_setting_a_join_date_fixes_days_before_the_item_existed(client, conn):
    from datetime import date, timedelta

    today = date.today()
    start = (today - timedelta(days=8)).isoformat()
    cid = _new_challenge(client, "Morning", start)
    first = _add_exercise_item(client, conn, cid, "push-up")
    second = _add_exercise_item(client, conn, cid, "squat")
    # Both look like setup items, as they do on a database predating
    # created_at: every day requires both.
    _backdate_setup(conn, cid, start)

    anchor = date.fromisoformat(start)
    for i in range(9):
        client.post("/api/challenge/toggle", json={
            "item_id": first, "day": (anchor + timedelta(days=i)).isoformat(),
        })
    for i in range(4, 9):  # the second item only from day 4
        client.post("/api/challenge/toggle", json={
            "item_id": second, "day": (anchor + timedelta(days=i)).isoformat(),
        })

    before = next(s for s in client.get("/api/challenges/stats").get_json() if s["id"] == cid)
    assert before["days_complete"] == 5  # the first four days can't complete

    joined = (anchor + timedelta(days=4)).isoformat()
    assert client.put(f"/api/challenge/items/{second}", json={"joined_on": joined}).status_code == 200

    after = next(s for s in client.get("/api/challenges/stats").get_json() if s["id"] == cid)
    assert after["days_complete"] == 9  # every day now counts
    assert after["completion_pct"] == 100.0
    # And the newcomer is scored over its own membership, not the whole run.
    item = next(i for i in after["items"] if i["id"] == second)
    assert item["days_member"] == 5 and item["rate_pct"] == 100.0


def test_the_join_date_can_be_cleared_back_to_inferred(client, conn):
    from datetime import date, timedelta

    today = date.today()
    start = (today - timedelta(days=5)).isoformat()
    cid = _new_challenge(client, "Morning", start)
    item = _add_exercise_item(client, conn, cid, "squat")
    _backdate_setup(conn, cid, start)

    client.put(f"/api/challenge/items/{item}", json={"joined_on": today.isoformat()})
    listed = next(i for i in client.get(f"/api/challenge/items?challenge_id={cid}").get_json()
                  if i["id"] == item)
    assert listed["joined_on"] == today.isoformat()
    assert listed["joined_effective"] == today.isoformat()

    client.put(f"/api/challenge/items/{item}", json={"joined_on": ""})
    listed = next(i for i in client.get(f"/api/challenge/items?challenge_id={cid}").get_json()
                  if i["id"] == item)
    assert listed["joined_on"] is None
    assert listed["joined_effective"] is None  # back to "there from the start"


def test_a_join_date_must_be_a_date(client, conn):
    from datetime import date

    cid = _new_challenge(client, "Morning", date.today().isoformat())
    item = _add_exercise_item(client, conn, cid, "squat")
    r = client.put(f"/api/challenge/items/{item}", json={"joined_on": "07/07/2026"})
    assert r.status_code == 400
    assert "joined_on" in r.get_json()["error"]


def test_an_explicit_join_date_outranks_an_earlier_tick(client, conn):
    from datetime import date, timedelta

    today = date.today()
    start = (today - timedelta(days=6)).isoformat()
    cid = _new_challenge(client, "Morning", start)
    item = _add_exercise_item(client, conn, cid, "squat")
    _backdate_setup(conn, cid, start)
    anchor = date.fromisoformat(start)
    client.post("/api/challenge/toggle", json={"item_id": item, "day": anchor.isoformat()})

    # Normally that tick would pull membership back; an explicit date wins.
    client.put(f"/api/challenge/items/{item}", json={
        "joined_on": (anchor + timedelta(days=3)).isoformat(),
    })
    stats = next(s for s in client.get("/api/challenges/stats").get_json() if s["id"] == cid)
    entry = next(i for i in stats["items"] if i["id"] == item)
    assert entry["days_member"] == 4  # from the stated day, not the early tick
