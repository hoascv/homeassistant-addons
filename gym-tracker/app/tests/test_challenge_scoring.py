"""Scoring: a due day kept earns points, a due day missed deducts them.

The score is never stored. Every test here leans on that: it asserts what the
ledger says *after* the completion record changed, because the whole point of
deriving it is that fixing a day fixes the score with it.
"""
from datetime import date, timedelta


def _new_challenge(client, name, start, end=None, **extra):
    body = {"name": name, "start_date": start, **extra}
    if end:
        body["end_date"] = end
    r = client.post("/api/challenges", json=body)
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _add_item(client, conn, challenge_id):
    ex = conn.execute("SELECT id FROM exercises WHERE archived = 0 LIMIT 1").fetchone()["id"]
    r = client.post("/api/challenge/items", json={
        "item_type": "exercise", "exercise_id": ex, "target_reps": 10,
        "challenge_id": challenge_id,
    })
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _view(client, cid):
    return [c for c in client.get("/api/challenges").get_json() if c["id"] == cid][0]


def _stats(client, cid):
    return [s for s in client.get("/api/challenges/stats").get_json() if s["id"] == cid][0]


def _tick(client, item, day):
    r = client.post("/api/challenge/toggle", json={"item_id": item, "day": day})
    assert r.status_code == 200, r.get_json()


def _scored_run(client, conn, days_back, kept_offsets, **extra):
    """A challenge running `days_back` days, scored from its first day, with
    the given day-offsets ticked complete."""
    today = date.today()
    start = (today - timedelta(days=days_back)).isoformat()
    cid = _new_challenge(
        client, "Scored", start, scoring_enabled=True, scoring_from=start, **extra
    )
    item = _add_item(client, conn, cid)
    for off in kept_offsets:
        _tick(client, item, (today - timedelta(days=off)).isoformat())
    return cid, item


# --- off by default --------------------------------------------------------


def test_a_challenge_is_not_scored_unless_asked(client):
    """The seeded challenge predates scoring and must be untouched by it."""
    ch = client.get("/api/challenges").get_json()[0]
    assert ch["scoring"]["enabled"] is False
    assert ch["score"] is None


def test_an_unscored_challenge_reports_no_ledger_in_stats(client, conn):
    cid = _new_challenge(client, "Plain", date.today().isoformat())
    _add_item(client, conn, cid)
    assert _stats(client, cid)["score"] is None


# --- the arithmetic --------------------------------------------------------


def test_a_kept_day_earns_and_a_missed_day_deducts(client, conn):
    # Four closed days: two kept, two missed. Today is left open.
    cid, _ = _scored_run(client, conn, days_back=4, kept_offsets=(4, 3))
    score = _view(client, cid)["score"]
    assert score["days_kept"] == 2
    assert score["days_missed"] == 2
    assert score["earned"] == 20
    assert score["lost"] == 20
    assert score["score"] == 0


def test_the_weights_are_configurable(client, conn):
    cid, _ = _scored_run(
        client, conn, days_back=3, kept_offsets=(3,), points_per_day=5, penalty_per_miss=25
    )
    score = _view(client, cid)["score"]
    # One kept at 5, two missed at 25.
    assert (score["earned"], score["lost"]) == (5, 50)
    assert score["score"] == -45


def test_a_score_is_allowed_to_go_negative(client, conn):
    """A deficit is the honest number. Flooring it at zero would make a bad
    fortnight and a bad quarter read exactly alike."""
    cid, _ = _scored_run(client, conn, days_back=5, kept_offsets=())
    assert _view(client, cid)["score"]["score"] == -50


def test_a_penalty_of_zero_makes_it_a_pure_reward_tally(client, conn):
    cid, _ = _scored_run(client, conn, days_back=3, kept_offsets=(3, 2), penalty_per_miss=0)
    score = _view(client, cid)["score"]
    assert score["days_missed"] == 1  # still counted as missed
    assert score["lost"] == 0
    assert score["score"] == 20


# --- when a miss is charged ------------------------------------------------


def test_an_unfinished_today_is_at_stake_not_yet_charged(client, conn):
    cid, _ = _scored_run(client, conn, days_back=1, kept_offsets=(1,))
    score = _view(client, cid)["score"]
    assert score["score"] == 10          # yesterday only
    assert score["days_missed"] == 0
    assert score["at_stake"] == {"gain": 10, "risk": 10}


def test_finishing_today_earns_it_immediately(client, conn):
    cid, item = _scored_run(client, conn, days_back=1, kept_offsets=(1,))
    _tick(client, item, date.today().isoformat())
    score = _view(client, cid)["score"]
    assert score["score"] == 20
    assert score["at_stake"] is None     # nothing left to lose today


def test_yesterday_is_charged_once_the_day_is_over(client, conn):
    """Forgiving today must not become forgiving yesterday."""
    cid, _ = _scored_run(client, conn, days_back=2, kept_offsets=(2,))
    score = _view(client, cid)["score"]
    assert score["days_missed"] == 1
    assert score["last_change"] == {
        "day": (date.today() - timedelta(days=1)).isoformat(), "delta": -10
    }


def test_a_rest_day_is_worth_nothing_either_way(client, conn):
    """A schedule that does not ask for a day cannot punish you for it."""
    today = date.today()
    start = (today - timedelta(days=6)).isoformat()
    # Due only on the weekday the run started on: one due day a week.
    weekday = (today - timedelta(days=6)).weekday()
    cid = _new_challenge(
        client, "Weekly", start, scoring_enabled=True, scoring_from=start,
        schedule_kind="weekdays", schedule_weekdays=str(weekday),
    )
    _add_item(client, conn, cid)
    score = _view(client, cid)["score"]
    # Six rest days and one missed due day — not seven misses.
    assert score["days_missed"] == 1
    assert score["score"] == -10


# --- the ledger corrects itself -------------------------------------------


def test_backfilling_a_missed_day_repays_its_penalty(client, conn):
    """The reason the score is derived rather than stored: History is allowed
    to be the final word on what actually happened."""
    cid, item = _scored_run(client, conn, days_back=3, kept_offsets=(3,))
    before = _view(client, cid)["score"]
    assert before["score"] == -10  # +10, then two missed

    _tick(client, item, (date.today() - timedelta(days=2)).isoformat())
    after = _view(client, cid)["score"]
    # The -10 is repaid and a +10 earned in its place.
    assert after["score"] == 10
    assert (after["days_kept"], after["days_missed"]) == (2, 1)


def test_unticking_a_day_charges_it_back(client, conn):
    cid, item = _scored_run(client, conn, days_back=2, kept_offsets=(2, 1))
    assert _view(client, cid)["score"]["score"] == 20
    _tick(client, item, (date.today() - timedelta(days=1)).isoformat())  # un-ticks
    assert _view(client, cid)["score"]["score"] == 0  # +10 kept, -10 charged


# --- when the ledger opens -------------------------------------------------


def test_switching_scoring_on_does_not_backdate_a_deficit(client, conn):
    """The whole reason `scoring_from` exists. Nobody was keeping score during
    those days, so nobody gets charged for them."""
    today = date.today()
    start = (today - timedelta(days=30)).isoformat()
    cid = _new_challenge(client, "Old habit", start)
    _add_item(client, conn, cid)

    r = client.put(f"/api/challenges/{cid}", json={"scoring_enabled": True})
    assert r.status_code == 200, r.get_json()

    view = _view(client, cid)
    assert view["scoring"]["since"] == today.isoformat()
    assert view["score"]["score"] == 0
    assert view["score"]["days_missed"] == 0


def test_a_challenge_created_scored_counts_from_its_start(client, conn):
    """A run that begins in the future opens its ledger on its first day, not
    on the day it was set up."""
    start = (date.today() + timedelta(days=3)).isoformat()
    cid = _new_challenge(client, "Next week", start, scoring_enabled=True)
    assert _view(client, cid)["scoring"]["since"] == start


def test_switching_scoring_off_keeps_where_the_ledger_opened(client, conn):
    """An accidental toggle must not throw the ledger away — there would be no
    way back to it."""
    cid, _ = _scored_run(client, conn, days_back=3, kept_offsets=(3, 2, 1))
    opened = _view(client, cid)["scoring"]["since"]

    client.put(f"/api/challenges/{cid}", json={"scoring_enabled": False})
    off = _view(client, cid)
    assert off["scoring"]["enabled"] is False
    assert off["score"] is None
    assert off["scoring"]["since"] == opened  # remembered, not erased

    client.put(f"/api/challenges/{cid}", json={"scoring_enabled": True})
    back = _view(client, cid)
    assert back["scoring"]["since"] == opened
    assert back["score"]["score"] == 30  # the same three days, still there


def test_editing_a_challenge_leaves_its_scoring_alone(client, conn):
    """A rename must not silently reset how it is scored."""
    cid, _ = _scored_run(client, conn, days_back=2, kept_offsets=(2, 1), points_per_day=7)
    client.put(f"/api/challenges/{cid}", json={"name": "Renamed"})
    scoring = _view(client, cid)["scoring"]
    assert scoring["enabled"] is True
    assert scoring["points_per_day"] == 7


# --- repeating -------------------------------------------------------------


def test_a_repeat_inherits_the_settings_but_opens_a_fresh_ledger(client, conn):
    cid, _ = _scored_run(client, conn, days_back=5, kept_offsets=(), penalty_per_miss=20)
    assert _view(client, cid)["score"]["score"] == -100

    r = client.post(f"/api/challenges/{cid}/repeat", json={})
    assert r.status_code == 201, r.get_json()
    new_id = r.get_json()["id"]

    fresh = _view(client, new_id)
    assert fresh["scoring"]["enabled"] is True
    assert fresh["scoring"]["penalty_per_miss"] == 20
    assert fresh["scoring"]["since"] == date.today().isoformat()
    assert fresh["score"]["score"] == 0  # the old run's deficit stays with it


def test_repeating_an_unscored_challenge_leaves_the_ledger_shut(client, conn):
    """So that switching scoring on later still opens it on that day, rather
    than retroactively at the repeat's start."""
    today = date.today()
    cid = _new_challenge(client, "Plain", (today - timedelta(days=5)).isoformat())
    _add_item(client, conn, cid)
    new_id = client.post(f"/api/challenges/{cid}/repeat", json={}).get_json()["id"]
    assert _view(client, new_id)["scoring"]["since"] is None


# --- stats -----------------------------------------------------------------


def test_stats_report_the_ledger_and_its_two_halves(client, conn):
    cid, _ = _scored_run(client, conn, days_back=4, kept_offsets=(4, 3, 2))
    score = _stats(client, cid)["score"]
    assert (score["days_kept"], score["days_missed"]) == (3, 1)
    assert (score["earned"], score["lost"]) == (30, 10)
    assert score["score"] == 20
    # The running total, one entry per settled due day, in order.
    assert [p["score"] for p in score["series"]] == [10, 20, 30, 20]


def test_the_card_series_is_capped_but_the_total_is_not(client, conn):
    """The sparkline carries a window; the score carries the whole run."""
    cid, _ = _scored_run(client, conn, days_back=40, kept_offsets=range(1, 41))
    view = _view(client, cid)["score"]
    assert len(view["series"]) == 30
    assert view["score"] == 400          # all 40 days, not just the drawn 30
    assert view["series"][-1]["score"] == 400


# --- validation ------------------------------------------------------------


def test_scoring_weights_must_be_numbers(client):
    r = client.post("/api/challenges", json={
        "name": "Bad", "start_date": date.today().isoformat(),
        "scoring_enabled": True, "points_per_day": "lots",
    })
    assert r.status_code == 400
    assert "points_per_day" in r.get_json()["error"]


def test_scoring_weights_are_bounded(client):
    r = client.post("/api/challenges", json={
        "name": "Bad", "start_date": date.today().isoformat(),
        "scoring_enabled": True, "penalty_per_miss": 99999,
    })
    assert r.status_code == 400
    assert "penalty_per_miss" in r.get_json()["error"]


def test_a_negative_weight_is_refused(client):
    """A negative penalty would pay you for missing."""
    r = client.post("/api/challenges", json={
        "name": "Bad", "start_date": date.today().isoformat(),
        "scoring_enabled": True, "penalty_per_miss": -10,
    })
    assert r.status_code == 400


def test_a_junk_scoring_start_is_refused(client):
    r = client.post("/api/challenges", json={
        "name": "Bad", "start_date": date.today().isoformat(),
        "scoring_enabled": True, "scoring_from": "last tuesday",
    })
    assert r.status_code == 400
    assert "scoring_from" in r.get_json()["error"]


def test_scoring_enabled_accepts_the_string_a_form_sends(client, conn):
    cid = _new_challenge(
        client, "Formy", date.today().isoformat(), scoring_enabled="true"
    )
    assert _view(client, cid)["scoring"]["enabled"] is True


# --- upgrading an older database -------------------------------------------


def test_a_database_from_before_scoring_upgrades_unscored(client, conn):
    """Restoring an older backup runs this branch, so it has to leave every
    existing challenge exactly as it was — not scored, and not in deficit."""
    import app as gymapp

    today = date.today()
    cid = _new_challenge(client, "Old habit", (today - timedelta(days=10)).isoformat())
    _add_item(client, conn, cid)

    # Genuinely the pre-feature schema: dropping the columns is what makes
    # init_db take the migration branch, which is the thing under test.
    for trigger in ("challenges_ai", "challenges_au", "challenges_ad"):
        conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for column in ("scoring_enabled", "points_per_day", "penalty_per_miss", "scoring_from"):
        conn.execute(f"ALTER TABLE challenges DROP COLUMN {column}")
    conn.commit()
    gymapp.init_db()

    view = _view(client, cid)
    assert view["scoring"] == {
        "enabled": False, "points_per_day": 10, "penalty_per_miss": 10, "since": None,
    }
    assert view["score"] is None

    # And it can still be switched on afterwards, opening the ledger today.
    client.put(f"/api/challenges/{cid}", json={"scoring_enabled": True})
    upgraded = _view(client, cid)
    assert upgraded["score"]["score"] == 0
    assert upgraded["scoring"]["since"] == today.isoformat()
