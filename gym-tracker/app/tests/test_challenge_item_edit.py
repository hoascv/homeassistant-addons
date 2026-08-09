"""Editing a challenge item's target.

The inline fields in Edit items each send a single key, so anything the handler
reads as "absent means clear it" is data loss on an unrelated edit. That is what
happened to sets until 1.32.2.
"""


def _exercise_item(client):
    items = client.get("/api/challenge").get_json()["items"]
    return next(i for i in items if i["item_type"] == "exercise")


def _reload(client, item_id):
    return next(
        i for i in client.get("/api/challenge").get_json()["items"] if i["id"] == item_id
    )


def test_editing_reps_keeps_the_sets(client):
    """The reported shape: a 3 x 40 item edited to 50 reps became "50 reps",
    with the sets silently gone."""
    item = _exercise_item(client)
    client.put(f"/api/challenge/items/{item['id']}",
               json={"target_sets": 3, "target_reps": 40})

    client.put(f"/api/challenge/items/{item['id']}", json={"target_reps": 50})

    after = _reload(client, item["id"])
    assert after["target_sets"] == 3
    assert after["target_reps"] == 50


def test_the_label_still_shows_both(client):
    """The label is what the user actually reads on the card."""
    item = _exercise_item(client)
    client.put(f"/api/challenge/items/{item['id']}",
               json={"target_sets": 3, "target_reps": 40})
    client.put(f"/api/challenge/items/{item['id']}", json={"target_reps": 50})

    assert "3" in _reload(client, item["id"])["label"]


def test_editing_sets_keeps_the_reps(client):
    item = _exercise_item(client)
    client.put(f"/api/challenge/items/{item['id']}",
               json={"target_sets": 3, "target_reps": 40})

    client.put(f"/api/challenge/items/{item['id']}", json={"target_sets": 5})

    after = _reload(client, item["id"])
    assert after["target_sets"] == 5
    assert after["target_reps"] == 40


def test_editing_the_duration_keeps_the_sets(client):
    """Same field, the timed half of it."""
    item = _exercise_item(client)
    client.put(f"/api/challenge/items/{item['id']}",
               json={"target_sets": 4, "target_seconds": 60})

    client.put(f"/api/challenge/items/{item['id']}", json={"target_seconds": 90})

    after = _reload(client, item["id"])
    assert after["target_sets"] == 4
    assert after["target_seconds"] == 90


def test_a_value_can_still_be_cleared_deliberately(client):
    """Preserving what was not sent must not make a field unclearable — an
    empty string is an explicit "no target", which is different from silence."""
    item = _exercise_item(client)
    client.put(f"/api/challenge/items/{item['id']}",
               json={"target_sets": 3, "target_reps": 40})

    client.put(f"/api/challenge/items/{item['id']}", json={"target_sets": ""})

    assert _reload(client, item["id"])["target_sets"] is None
    assert _reload(client, item["id"])["target_reps"] == 40


def test_setting_the_joined_date_does_not_touch_the_target(client):
    """The other inline control in the same list, sending only joined_on."""
    item = _exercise_item(client)
    client.put(f"/api/challenge/items/{item['id']}",
               json={"target_sets": 3, "target_reps": 40})

    client.put(f"/api/challenge/items/{item['id']}", json={"joined_on": "2026-01-01"})

    after = _reload(client, item["id"])
    assert (after["target_sets"], after["target_reps"]) == (3, 40)


def test_a_bad_number_is_still_refused(client):
    item = _exercise_item(client)
    res = client.put(f"/api/challenge/items/{item['id']}", json={"target_reps": "loads"})
    assert res.status_code == 400
    assert "target_reps" in res.get_json()["error"]


def test_a_supplement_dose_edit_is_unaffected(client):
    items = client.get("/api/challenge").get_json()["items"]
    item = next(i for i in items if i["item_type"] == "supplement")

    client.put(f"/api/challenge/items/{item['id']}", json={"dose": "10 g"})

    assert "10 g" in _reload(client, item["id"])["label"]
