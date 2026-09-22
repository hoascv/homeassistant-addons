"""Putting a challenge's items in the order you actually do them.

Until 1.57.0 the only order available was the order things were added in, which
made a warm-up added to an existing session sit underneath the working sets —
and the only way to lift it was to delete and re-add everything above it, which
takes each item's tick history with it.
"""
from datetime import date


def _challenge_with_items(client, count=3):
    """A challenge of `count` exercise items, in the order they were added."""
    r = client.post("/api/challenges", json={
        "name": "Session A", "start_date": date.today().isoformat(),
    })
    assert r.status_code == 201, r.get_json()
    cid = r.get_json()["id"]

    groups = client.get("/api/exercises").get_json()
    library = [ex["id"] for group in groups for ex in group["exercises"] if not ex["is_routine"]]
    assert len(library) >= count, "the seeded library is smaller than this test needs"

    ids = []
    for exercise_id in library[:count]:
        added = client.post("/api/challenge/items", json={
            "item_type": "exercise", "exercise_id": exercise_id,
            "target_reps": 10, "challenge_id": cid,
        })
        assert added.status_code == 201, added.get_json()
        ids.append(added.get_json()["id"])
    return cid, ids


def _order(client, challenge_id):
    items = client.get(f"/api/challenge/items?challenge_id={challenge_id}").get_json()
    return [it["id"] for it in items]


def test_items_start_in_the_order_they_were_added(client):
    """The behaviour being extended, stated so a regression names itself."""
    cid, ids = _challenge_with_items(client)
    assert _order(client, cid) == ids


def test_the_whole_order_is_what_gets_sent(client):
    cid, ids = _challenge_with_items(client)
    wanted = [ids[2], ids[0], ids[1]]

    res = client.post(f"/api/challenges/{cid}/items/order", json={"item_ids": wanted})

    assert res.status_code == 200, res.get_json()
    assert [it["id"] for it in res.get_json()] == wanted
    assert _order(client, cid) == wanted


def test_the_card_reads_in_the_new_order(client):
    """The order the endpoint reports is worth little if the card disagrees."""
    cid, ids = _challenge_with_items(client)
    client.post(f"/api/challenges/{cid}/items/order", json={"item_ids": list(reversed(ids))})

    card = next(c for c in client.get("/api/challenges").get_json() if c["id"] == cid)

    assert [it["id"] for it in card["items"]] == list(reversed(ids))


def test_moving_one_item_up_leaves_the_rest_alone(client):
    """What the ↑ arrow sends: two entries swapped, everything else in place."""
    cid, ids = _challenge_with_items(client, count=4)
    swapped = [ids[0], ids[2], ids[1], ids[3]]

    client.post(f"/api/challenges/{cid}/items/order", json={"item_ids": swapped})

    assert _order(client, cid) == swapped


def test_reordering_keeps_the_ticks(client):
    """The whole point of reordering over delete-and-re-add: an item keeps its
    id, so the days already ticked against it stay ticked."""
    cid, ids = _challenge_with_items(client)
    today = date.today().isoformat()
    ticked = client.post("/api/challenge/toggle",
                         json={"item_id": ids[2], "date": today, "done": True})
    assert ticked.status_code in (200, 201), ticked.get_json()

    client.post(f"/api/challenges/{cid}/items/order", json={"item_ids": list(reversed(ids))})

    items = client.get(f"/api/challenge/items?challenge_id={cid}").get_json()
    assert [it["id"] for it in items][0] == ids[2]
    card = next(c for c in client.get("/api/challenges").get_json() if c["id"] == cid)
    assert next(it for it in card["items"] if it["id"] == ids[2])["done_today"] is True


def test_a_list_missing_an_item_is_refused(client):
    """A partial list would have to invent a position for what it left out."""
    cid, ids = _challenge_with_items(client)

    res = client.post(f"/api/challenges/{cid}/items/order", json={"item_ids": ids[:2]})

    assert res.status_code == 400
    assert "exactly once" in res.get_json()["error"]
    assert _order(client, cid) == ids  # and nothing moved


def test_an_item_named_twice_is_refused(client):
    cid, ids = _challenge_with_items(client)

    res = client.post(f"/api/challenges/{cid}/items/order",
                      json={"item_ids": [ids[0], ids[0], ids[1]]})

    assert res.status_code == 400
    assert _order(client, cid) == ids


def test_another_challenge_s_item_is_refused(client):
    """Reordering is not a way to move an item between challenges — /move is,
    and it is deliberate about the history it leaves behind."""
    cid, ids = _challenge_with_items(client)
    other_cid, other_ids = _challenge_with_items(client)

    res = client.post(f"/api/challenges/{cid}/items/order",
                      json={"item_ids": ids[:2] + [other_ids[0]]})

    assert res.status_code == 400
    assert _order(client, cid) == ids
    assert _order(client, other_cid) == other_ids


def test_an_archived_item_is_not_part_of_the_order(client):
    """Removing an item archives it; the order is over what is still on the card."""
    cid, ids = _challenge_with_items(client)
    assert client.delete(f"/api/challenge/items/{ids[1]}").status_code == 200

    remaining = [ids[2], ids[0]]
    res = client.post(f"/api/challenges/{cid}/items/order", json={"item_ids": remaining})

    assert res.status_code == 200, res.get_json()
    assert _order(client, cid) == remaining


def test_the_list_must_be_a_list(client):
    cid, _ = _challenge_with_items(client)

    res = client.post(f"/api/challenges/{cid}/items/order", json={"item_ids": "1,2,3"})

    assert res.status_code == 400
    assert "must be a list" in res.get_json()["error"]


def test_ids_that_are_not_numbers_are_refused(client):
    cid, _ = _challenge_with_items(client)

    res = client.post(f"/api/challenges/{cid}/items/order", json={"item_ids": ["first"]})

    assert res.status_code == 400


def test_no_such_challenge(client):
    res = client.post("/api/challenges/9999/items/order", json={"item_ids": []})

    assert res.status_code == 404


def test_reordering_is_recorded_in_the_change_feed(client, conn):
    """The lakehouse merges challenge_items from the feed, so an order it never
    heard about would leave the mirror reading in the old one."""
    cid, ids = _challenge_with_items(client)
    before = conn.execute("SELECT COALESCE(MAX(seq), 0) AS n FROM change_log").fetchone()["n"]

    client.post(f"/api/challenges/{cid}/items/order", json={"item_ids": list(reversed(ids))})

    rows = conn.execute(
        "SELECT DISTINCT row_id FROM change_log "
        "WHERE table_name = 'challenge_items' AND op = 'U' AND seq > ?",
        (before,),
    ).fetchall()
    assert {int(r["row_id"]) for r in rows} == set(ids)
