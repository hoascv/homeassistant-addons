"""The forfeit: what a missed day costs you outside the app.

The charge is derived from the ticks exactly as the score is, so it corrects
itself when the record is corrected. The payments are the only stored part,
because paying is something that happens in the world.
"""
from datetime import date, timedelta


def _challenge(client, **settings):
    body = {"name": "Forfeit challenge", "start_date": date.today().isoformat()}
    body.update(settings)
    res = client.post("/api/challenges", json=body)
    assert res.status_code == 201, res.get_json()
    return res.get_json()["id"]


def _item(client, conn, challenge_id):
    ex = conn.execute("SELECT id FROM exercises WHERE archived = 0 LIMIT 1").fetchone()["id"]
    res = client.post("/api/challenge/items", json={
        "item_type": "exercise", "exercise_id": ex, "target_reps": 10,
        "challenge_id": challenge_id,
    })
    assert res.status_code == 201, res.get_json()
    return res.get_json()["id"]


def _view(client, challenge_id):
    return [c for c in client.get("/api/challenges").get_json() if c["id"] == challenge_id][0]


def _miss_days(conn, challenge_id, days_back):
    """Age the challenge so the days behind it are settled, missed days."""
    start = (date.today() - timedelta(days=days_back)).isoformat()
    conn.execute(
        "UPDATE challenges SET start_date = ?, forfeit_from = ? WHERE id = ?",
        (start, start, challenge_id),
    )
    conn.commit()


def test_a_challenge_owes_nothing_until_a_day_is_missed(client, conn):
    cid = _challenge(client, forfeit_enabled=True, forfeit_amount=10, forfeit_unit="kr")
    _item(client, conn, cid)
    forfeit = _view(client, cid)["forfeit"]
    assert forfeit["enabled"] is True
    assert (forfeit["owed"], forfeit["days_missed"], forfeit["charged"]) == (0, 0, 0)
    # Today is at stake rather than charged: it can still be won.
    assert forfeit["at_stake"] == 10


def test_each_missed_day_puts_the_stake_on_the_tab(client, conn):
    cid = _challenge(client, forfeit_enabled=True, forfeit_amount=10, forfeit_unit="kr")
    _item(client, conn, cid)
    _miss_days(conn, cid, 3)  # three settled days behind today, none ticked
    forfeit = _view(client, cid)["forfeit"]
    assert forfeit["days_missed"] == 3
    assert forfeit["charged"] == 30
    assert forfeit["owed"] == 30
    assert forfeit["unit"] == "kr"


def test_backfilling_a_day_takes_its_charge_back_off(client, conn):
    cid = _challenge(client, forfeit_enabled=True, forfeit_amount=10)
    item_id = _item(client, conn, cid)
    _miss_days(conn, cid, 3)
    assert _view(client, cid)["forfeit"]["owed"] == 30

    # It turns out you did it and forgot to tick.
    client.post(
        "/api/challenge/toggle",
        json={"item_id": item_id, "day": (date.today() - timedelta(days=2)).isoformat()},
    )
    assert _view(client, cid)["forfeit"]["owed"] == 20


def test_paying_up_settles_what_is_owed_now(client, conn):
    cid = _challenge(client, forfeit_enabled=True, forfeit_amount=10)
    _item(client, conn, cid)
    _miss_days(conn, cid, 2)

    res = client.post(f"/api/challenges/{cid}/forfeit/payments", json={}).get_json()
    assert res["forfeit"]["paid"] == 20
    assert res["forfeit"]["owed"] == 0
    # The charge itself is untouched — settled is not the same as never owed.
    assert res["forfeit"]["charged"] == 20
    assert res["forfeit"]["days_missed"] == 2


def test_paying_when_nothing_is_owed_is_refused(client, conn):
    cid = _challenge(client, forfeit_enabled=True)
    _item(client, conn, cid)
    res = client.post(f"/api/challenges/{cid}/forfeit/payments", json={})
    assert res.status_code == 400
    assert res.get_json()["error"] == "nothing to pay"


def test_a_challenge_without_a_forfeit_has_no_tab_to_pay(client):
    cid = _challenge(client)
    res = client.post(f"/api/challenges/{cid}/forfeit/payments", json={})
    assert res.status_code == 400
    assert _view(client, cid)["forfeit"] is None


def test_paying_then_backfilling_leaves_you_in_credit(client, conn):
    """The honest answer, rather than quietly keeping what you no longer owe."""
    cid = _challenge(client, forfeit_enabled=True, forfeit_amount=10)
    item_id = _item(client, conn, cid)
    _miss_days(conn, cid, 2)
    client.post(f"/api/challenges/{cid}/forfeit/payments", json={})

    client.post(
        "/api/challenge/toggle",
        json={"item_id": item_id, "day": (date.today() - timedelta(days=1)).isoformat()},
    )
    forfeit = _view(client, cid)["forfeit"]
    assert forfeit["charged"] == 10 and forfeit["paid"] == 20
    assert forfeit["owed"] == -10


def test_a_payment_can_be_taken_back(client, conn):
    cid = _challenge(client, forfeit_enabled=True, forfeit_amount=10)
    _item(client, conn, cid)
    _miss_days(conn, cid, 2)
    paid = client.post(f"/api/challenges/{cid}/forfeit/payments", json={}).get_json()

    res = client.delete(
        f"/api/challenges/{cid}/forfeit/payments/{paid['payment_id']}"
    ).get_json()
    assert res["forfeit"]["paid"] == 0
    assert res["forfeit"]["owed"] == 20


def test_partial_payments_add_up(client, conn):
    cid = _challenge(client, forfeit_enabled=True, forfeit_amount=10)
    _item(client, conn, cid)
    _miss_days(conn, cid, 3)
    client.post(f"/api/challenges/{cid}/forfeit/payments", json={"amount": 5})
    client.post(f"/api/challenges/{cid}/forfeit/payments", json={"amount": 15})
    forfeit = _view(client, cid)["forfeit"]
    assert forfeit["paid"] == 20 and forfeit["owed"] == 10
    assert forfeit["payment_count"] == 2
    assert client.get(f"/api/challenges/{cid}/forfeit/payments").get_json()["payments"][0][
        "amount"
    ] == 15  # newest first


def test_rest_days_are_never_charged(client, conn):
    cid = _challenge(
        client, forfeit_enabled=True, forfeit_amount=10,
        schedule_kind="weekdays", schedule_weekdays=str(date.today().weekday()),
    )
    _item(client, conn, cid)
    _miss_days(conn, cid, 6)  # a week back, but only one weekday is due
    forfeit = _view(client, cid)["forfeit"]
    # The only due day in the window is today, which is still at stake.
    assert forfeit["days_missed"] == 0
    assert forfeit["at_stake"] == 10


def test_switching_the_forfeit_on_does_not_backdate_a_debt(client, conn):
    cid = _challenge(client)
    _item(client, conn, cid)
    conn.execute(
        "UPDATE challenges SET start_date = ? WHERE id = ?",
        ((date.today() - timedelta(days=30)).isoformat(), cid),
    )
    conn.commit()

    client.put(f"/api/challenges/{cid}", json={"forfeit_enabled": True, "forfeit_amount": 10})
    forfeit = _view(client, cid)["forfeit"]
    assert forfeit["since"] == date.today().isoformat()
    assert forfeit["owed"] == 0, "a month of misses nobody was charging for is not a bill"


def test_switching_it_off_remembers_where_the_tab_opened(client):
    cid = _challenge(client, forfeit_enabled=True)
    client.put(f"/api/challenges/{cid}", json={"forfeit_enabled": False})
    client.put(f"/api/challenges/{cid}", json={"forfeit_enabled": True})
    assert _view(client, cid)["forfeit"]["since"] == date.today().isoformat()


def test_a_repeat_starts_a_clean_tab(client, conn):
    cid = _challenge(client, forfeit_enabled=True, forfeit_amount=10, forfeit_unit="kr")
    _item(client, conn, cid)
    _miss_days(conn, cid, 2)
    assert _view(client, cid)["forfeit"]["owed"] == 20

    new_id = client.post(f"/api/challenges/{cid}/repeat", json={}).get_json()["id"]
    fresh = _view(client, new_id)["forfeit"]
    assert fresh["enabled"] is True and fresh["amount"] == 10 and fresh["unit"] == "kr"
    assert fresh["owed"] == 0, "what you owe on one run stays on that run"
    # And the original still owes what it owed.
    assert _view(client, cid)["forfeit"]["owed"] == 20


def test_the_stake_and_the_unit_are_validated(client):
    cid = _challenge(client)
    bad = client.put(f"/api/challenges/{cid}", json={"forfeit_enabled": True, "forfeit_amount": "x"})
    assert bad.status_code == 400
    huge = client.put(
        f"/api/challenges/{cid}", json={"forfeit_enabled": True, "forfeit_amount": 999999}
    )
    assert huge.status_code == 400
    # A blank unit falls back rather than rendering "30 " on the card.
    client.put(f"/api/challenges/{cid}", json={"forfeit_enabled": True, "forfeit_unit": "   "})
    assert _view(client, cid)["forfeit"]["unit"] == "kr"


def test_payments_are_in_the_change_feed(client, conn):
    """A pipeline reading the feed can see the jar filling up."""
    cid = _challenge(client, forfeit_enabled=True, forfeit_amount=10)
    _item(client, conn, cid)
    _miss_days(conn, cid, 1)
    client.post(f"/api/challenges/{cid}/forfeit/payments", json={})
    tables = {
        e["table"] for e in client.get("/api/changes?since=0").get_json()["changes"]
    }
    assert "challenge_forfeit_payments" in tables
