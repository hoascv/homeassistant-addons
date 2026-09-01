"""Fermented feed: batches, stirring, and the reminder that stops mould.

The reminder is the point of the feature. An unstirred batch grows mould on top
and gets thrown away, so most of what follows is about the notification firing
when it should and — just as important — staying quiet when it should not. A
reminder that cries wolf is one people mute, and muting it costs you the ones
that mattered.
"""
import datetime

import pytest

import ferment

NOW = datetime.datetime(2026, 9, 1, 8, 0)


def _hours(n):
    return NOW + datetime.timedelta(hours=n)


# --- how much to make ---------------------------------------------------------


def test_the_suggestion_covers_the_flock_for_the_whole_ferment():
    """A rotation only works if a batch lasts until the next is ready, so it is
    birds times days, not birds times one meal."""
    assert ferment.suggested_grams(5, days=3) == 5 * 3 * ferment.GRAMS_PER_BIRD_PER_DAY


def test_five_chickens_get_a_sane_number():
    grams = ferment.suggested_grams(5)
    assert 500 < grams < 900, f"{grams} g is not a plausible three-day batch for five birds"


@pytest.mark.parametrize("birds,days", [(0, 3), (5, 0), (-1, 3)])
def test_a_nonsensical_flock_or_window_suggests_nothing(birds, days):
    assert ferment.suggested_grams(birds, days=days) == 0


# --- the batch lifecycle ------------------------------------------------------


def test_a_new_batch_counts_as_just_stirred(conn):
    """You have this second mixed the grain into the water. Without it the
    first reminder fires an interval after starting rather than an interval
    after anybody last touched it."""
    ferment.start_batch(conn, "Tub 1", now=NOW)
    conn.commit()
    batch = ferment.batches(conn, now=NOW)[0]
    assert batch["hours_since_stir"] == 0
    assert batch["stir_due"] is False


def test_a_batch_needs_a_container(conn):
    with pytest.raises(ValueError, match="container"):
        ferment.start_batch(conn, "   ")


def test_a_batch_becomes_ready_by_the_clock_alone(conn):
    """Nothing happens to make it ready; the date simply passes. A stored state
    would need something to notice, and nothing would."""
    ferment.start_batch(conn, "Tub 1", ferment_days=3, now=NOW)
    conn.commit()
    assert ferment.batches(conn, now=_hours(71))[0]["state"] == "fermenting"
    assert ferment.batches(conn, now=_hours(73))[0]["state"] == "ready"


def test_a_batch_can_be_fed_or_discarded_and_leaves_the_rotation(conn):
    for container, outcome in [("Tub 1", "fed"), ("Tub 2", "discarded")]:
        batch_id = ferment.start_batch(conn, container, now=NOW)
        ferment.close_batch(conn, batch_id, outcome, now=_hours(72))
    conn.commit()

    assert ferment.batches(conn, now=_hours(80)) == []
    history = ferment.batches(conn, include_closed=True, now=_hours(80))
    assert {b["outcome"] for b in history} == {"fed", "discarded"}


def test_thrown_away_is_recorded_separately_from_eaten(conn):
    """A batch lost to mould is a different event from one the birds ate, and
    how often that happens is worth being able to find out."""
    batch_id = ferment.start_batch(conn, "Tub 1", now=NOW)
    ferment.close_batch(conn, batch_id, "discarded", now=_hours(72))
    conn.commit()
    assert ferment.batches(conn, include_closed=True, now=NOW)[0]["state"] == "discarded"


def test_an_unknown_outcome_is_refused(conn):
    batch_id = ferment.start_batch(conn, "Tub 1", now=NOW)
    with pytest.raises(ValueError, match="outcome"):
        ferment.close_batch(conn, batch_id, "composted")


def test_closing_a_batch_twice_does_not_move_the_first_outcome(conn):
    batch_id = ferment.start_batch(conn, "Tub 1", now=NOW)
    ferment.close_batch(conn, batch_id, "fed", now=_hours(72))
    ferment.close_batch(conn, batch_id, "discarded", now=_hours(80))
    conn.commit()
    assert ferment.batches(conn, include_closed=True, now=NOW)[0]["outcome"] == "fed"


# --- stirring -----------------------------------------------------------------


def test_a_batch_falls_due_once_the_interval_has_passed(conn):
    ferment.start_batch(conn, "Tub 1", now=NOW)
    conn.commit()
    assert ferment.due_for_stir(conn, now=_hours(11), stir_hours=12) == []
    assert len(ferment.due_for_stir(conn, now=_hours(12), stir_hours=12)) == 1


def test_stirring_clears_it(conn):
    batch_id = ferment.start_batch(conn, "Tub 1", now=NOW)
    conn.commit()
    assert ferment.due_for_stir(conn, now=_hours(13)) 
    ferment.log_stir(conn, batch_id, now=_hours(13))
    conn.commit()
    assert ferment.due_for_stir(conn, now=_hours(14)) == []


def test_a_finished_batch_never_needs_stirring(conn):
    """A reminder naming a tub you emptied yesterday is how people stop reading
    the reminders."""
    batch_id = ferment.start_batch(conn, "Tub 1", now=NOW)
    ferment.close_batch(conn, batch_id, "fed", now=_hours(1))
    conn.commit()
    assert ferment.due_for_stir(conn, now=_hours(48)) == []


def test_stirring_a_finished_batch_is_refused(conn):
    batch_id = ferment.start_batch(conn, "Tub 1", now=NOW)
    ferment.close_batch(conn, batch_id, "fed", now=_hours(1))
    conn.commit()
    with pytest.raises(ValueError, match="finished"):
        ferment.log_stir(conn, batch_id)


def test_stirring_an_unknown_batch_is_refused(conn):
    with pytest.raises(ValueError, match="no such batch"):
        ferment.log_stir(conn, 999)


def test_stirs_are_counted(conn):
    batch_id = ferment.start_batch(conn, "Tub 1", now=NOW)
    ferment.log_stir(conn, batch_id, now=_hours(12))
    ferment.log_stir(conn, batch_id, now=_hours(24))
    conn.commit()
    assert ferment.batches(conn, now=_hours(25))[0]["stirs"] == 3  # including the start


# --- what the notification says -----------------------------------------------


def test_nothing_due_means_no_message(conn):
    assert ferment.stir_message([]) is None


def test_one_container_is_named_with_how_long_it_has_been(conn):
    ferment.start_batch(conn, "Tub 1", now=NOW)
    conn.commit()
    message = ferment.stir_message(ferment.due_for_stir(conn, now=_hours(14)))
    assert "Tub 1" in message
    assert "14h" in message


def test_several_containers_are_all_named(conn):
    """A keeper with three tubs going needs to know which. "Stir something" is
    a message you learn to dismiss."""
    for container in ("Tub 1", "Tub 2", "Tub 3"):
        ferment.start_batch(conn, container, now=NOW)
    conn.commit()
    message = ferment.stir_message(ferment.due_for_stir(conn, now=_hours(13)))
    for container in ("Tub 1", "Tub 2", "Tub 3"):
        assert container in message
    assert "3 containers" in message


# --- the summary --------------------------------------------------------------


def test_the_summary_counts_what_needs_attention(conn):
    ferment.start_batch(conn, "Tub 1", ferment_days=3, now=NOW)          # will be ready
    ferment.start_batch(conn, "Tub 2", ferment_days=9, now=_hours(1))    # still going
    conn.commit()

    body = ferment.summary(conn, birds=5, now=_hours(80))
    assert body["open"] == 2
    assert body["ready"] == 1
    assert body["stir_due"] == 2
    assert body["birds"] == 5
    assert body["suggested_grams"] == ferment.suggested_grams(5)


# --- the reminder ------------------------------------------------------------
#
# Separate from the daily egg reminder on purpose: stirring is a twice-daily
# job and a reminder that can only arrive at 18:00 is no use for the morning
# one. These cover the firing rules, which are where a reminder becomes either
# useful or noise.


@pytest.fixture
def notified(monkeypatch):
    """Capture notifications instead of sending them."""
    import app as coop
    sent = []
    monkeypatch.setattr(coop, "send_notification", lambda msg, title=None: sent.append(msg))
    monkeypatch.setattr(coop, "_stir_last_notified", None)
    return sent


def _configure(set_options, **overrides):
    set_options(**{
        "ferment_enabled": True, "notify_service": "mobile_app_x",
        "ferment_stir_times": "08:00, 20:00", "ferment_stir_hours": 12,
        **overrides,
    })


def test_a_due_batch_in_an_open_window_notifies(conn, set_options, notified):
    import app as coop
    _configure(set_options)
    ferment.start_batch(conn, "Tub 1", now=NOW)
    conn.commit()
    coop._ferment_stir_tick(datetime.datetime(2026, 9, 1, 20, 5), conn)
    assert notified and "Tub 1" in notified[0]


def test_nothing_due_means_no_notification(conn, set_options, notified):
    import app as coop
    _configure(set_options)
    ferment.start_batch(conn, "Tub 1", now=datetime.datetime(2026, 9, 1, 19, 0))
    conn.commit()
    coop._ferment_stir_tick(datetime.datetime(2026, 9, 1, 20, 5), conn)
    assert notified == []


def test_before_the_first_window_nothing_fires(conn, set_options, notified):
    """A batch can be overdue at 5am and still not worth waking anyone for."""
    import app as coop
    _configure(set_options)
    ferment.start_batch(conn, "Tub 1", now=datetime.datetime(2026, 8, 31, 12, 0))
    conn.commit()
    coop._ferment_stir_tick(datetime.datetime(2026, 9, 1, 5, 0), conn)
    assert notified == []


def test_one_notification_per_window_not_one_per_tick(conn, set_options, notified):
    """The loop ticks every few seconds. Without the guard this is a phone
    buzzing continuously from 20:00 until somebody stirs."""
    import app as coop
    _configure(set_options)
    ferment.start_batch(conn, "Tub 1", now=NOW)
    conn.commit()
    for minute in (5, 6, 30):
        coop._ferment_stir_tick(datetime.datetime(2026, 9, 1, 20, minute), conn)
    assert len(notified) == 1


def test_the_next_window_notifies_again(conn, set_options, notified):
    import app as coop
    _configure(set_options)
    ferment.start_batch(conn, "Tub 1", now=NOW)
    conn.commit()
    coop._ferment_stir_tick(datetime.datetime(2026, 9, 1, 20, 5), conn)
    coop._ferment_stir_tick(datetime.datetime(2026, 9, 2, 8, 5), conn)
    assert len(notified) == 2


def test_a_window_that_had_nothing_due_can_still_fire_later(conn, set_options, notified):
    """A batch falling due at 20:30 should be reminded at 20:30, not skipped
    because the window was already checked at 20:00 and found quiet."""
    import app as coop
    _configure(set_options)
    ferment.start_batch(conn, "Tub 1", now=datetime.datetime(2026, 9, 1, 8, 30))
    conn.commit()
    coop._ferment_stir_tick(datetime.datetime(2026, 9, 1, 20, 0), conn)
    assert notified == []
    coop._ferment_stir_tick(datetime.datetime(2026, 9, 1, 20, 45), conn)
    assert len(notified) == 1


def test_the_guard_survives_a_restart(conn, set_options, notified, monkeypatch):
    """Restarting the add-on an hour after a reminder must not resend it."""
    import app as coop
    _configure(set_options)
    ferment.start_batch(conn, "Tub 1", now=NOW)
    conn.commit()
    coop._ferment_stir_tick(datetime.datetime(2026, 9, 1, 20, 5), conn)

    monkeypatch.setattr(coop, "_stir_last_notified", None)  # as if just started
    coop._ferment_stir_tick(datetime.datetime(2026, 9, 1, 21, 0), conn)
    assert len(notified) == 1


def test_nothing_fires_while_the_feature_is_off(conn, set_options, notified):
    import app as coop
    _configure(set_options, ferment_enabled=False)
    ferment.start_batch(conn, "Tub 1", now=NOW)
    conn.commit()
    coop._ferment_stir_tick(datetime.datetime(2026, 9, 1, 20, 5), conn)
    assert notified == []


def test_nothing_fires_without_a_notify_service(conn, set_options, notified):
    import app as coop
    _configure(set_options, notify_service="")
    ferment.start_batch(conn, "Tub 1", now=NOW)
    conn.commit()
    coop._ferment_stir_tick(datetime.datetime(2026, 9, 1, 20, 5), conn)
    assert notified == []


# --- the HTTP surface ---------------------------------------------------------


def test_the_batch_lifecycle_through_the_routes(client, set_options):
    set_options(ferment_enabled=True, flock_isabrown_count=3, flock_sussex_count=2)

    started = client.post("/api/ferment/batches",
                          json={"container": "Tub 1", "grams": 675}).get_json()
    assert started["open"] == 1
    batch_id = started["batches"][0]["id"]

    stirred = client.post(f"/api/ferment/batches/{batch_id}/stir").get_json()
    assert stirred["batches"][0]["stirs"] == 2

    closed = client.post(f"/api/ferment/batches/{batch_id}/close",
                         json={"outcome": "fed"}).get_json()
    assert closed["open"] == 0


def test_the_suggestion_follows_the_configured_flock(client, set_options):
    """Five birds is what this household has, and the number should say so
    without being typed in twice."""
    set_options(flock_isabrown_count=3, flock_sussex_count=2)
    body = client.get("/api/ferment").get_json()
    assert body["birds"] == 5
    assert body["suggested_grams"] == ferment.suggested_grams(5)


def test_a_batch_without_a_container_is_a_400(client):
    assert client.post("/api/ferment/batches", json={}).status_code == 400


def test_a_bad_outcome_is_a_400(client):
    started = client.post("/api/ferment/batches", json={"container": "Tub 1"}).get_json()
    batch_id = started["batches"][0]["id"]
    assert client.post(f"/api/ferment/batches/{batch_id}/close",
                       json={"outcome": "composted"}).status_code == 400


def test_history_includes_finished_batches(client):
    started = client.post("/api/ferment/batches", json={"container": "Tub 1"}).get_json()
    batch_id = started["batches"][0]["id"]
    client.post(f"/api/ferment/batches/{batch_id}/close", json={"outcome": "fed"})
    assert len(client.get("/api/ferment/history").get_json()) == 1
    assert client.get("/api/ferment").get_json()["open"] == 0


def test_ferment_tables_are_in_the_change_feed(client):
    """Every other table the app owns is; one outside it is invisible to the
    lakehouse and missing from the export."""
    import app as coop
    assert "ferment_batches" in coop.TRACKED_TABLES
    assert "ferment_stirs" in coop.TRACKED_TABLES


# --- the card -----------------------------------------------------------------


def _static(name):
    import os
    sub = "templates" if name.endswith(".html") else "static"
    path = os.path.join(os.path.dirname(__file__), "..", sub, name)
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def test_the_card_is_wired():
    html, js = _static("index.html"), _static("app.js")
    for element_id in ("ferment-card", "ferment-hint", "ferment-batches", "ferment-new"):
        assert element_id in html, f"{element_id} missing from index.html"
        assert element_id in js, f"{element_id} never used by app.js"


def test_the_card_is_hidden_when_the_feature_is_off():
    """A card about tubs of soaking grain is noise to somebody not fermenting."""
    js = _static("app.js")
    fn = js[js.index("async function loadFerment("):js.index("function renderFerment(")]
    assert "data.enabled" in fn and "card.hidden = true" in fn


def test_only_the_overdue_state_is_coloured():
    """It is the one thing on the card worth reacting to today; colouring the
    rest would bury it."""
    css = _static("style.css")
    assert ".stir-due" in css
    assert "stir it" in css


def test_binning_a_batch_asks_first():
    """Feeding is reversible in the sense that nothing was lost. Throwing a
    batch away is the one button that destroys three days of waiting."""
    js = _static("app.js")
    handler = js[js.index('document.getElementById("ferment-batches").addEventListener'):]
    assert "confirm(" in handler[:900]
    assert handler.index("confirm(") < handler.index("api/ferment/batches/${close.dataset.close}/close")


def test_the_new_batch_prompt_offers_the_flock_sized_suggestion():
    """The number you need before you can start a batch, rather than making
    somebody work out five birds times three days."""
    js = _static("app.js")
    fn = js[js.index('document.getElementById("ferment-new")'):]
    assert "suggested_grams" in fn and "current.birds" in fn


# --- backslopping: carrying the culture forward -------------------------------
#
# The liquid, not the grain. That distinction is the whole safety argument for
# doing this: old wet grain is the substrate spoilage organisms have had three
# days to establish on, and the drained brine is the culture without it. Several
# of the tests below exist only to pin that the code cannot be talked into
# carrying grain-adjacent risk forward — in particular, out of a binned batch.


def test_feeding_a_batch_can_keep_the_liquid(conn):
    ferment.start_batch(conn, "Tub 1", now=NOW)
    batch_id = ferment.batches(conn, now=NOW)[0]["id"]
    ferment.close_batch(conn, batch_id, ferment.FED, now=NOW, save_liquid=True)
    conn.commit()

    jar = ferment.current_starter(conn, now=NOW)
    assert jar is not None
    assert jar["from_batch_id"] == batch_id
    assert jar["generation"] == 0


def test_feeding_without_keeping_the_liquid_leaves_no_jar(conn):
    """The default. Saving it is a thing you chose to do, not the consequence
    of feeding your chickens."""
    ferment.start_batch(conn, "Tub 1", now=NOW)
    batch_id = ferment.batches(conn, now=NOW)[0]["id"]
    ferment.close_batch(conn, batch_id, ferment.FED, now=NOW)
    conn.commit()
    assert ferment.current_starter(conn, now=NOW) is None


def test_the_liquid_from_a_binned_batch_is_refused(conn):
    """A batch is binned because something went wrong in it. That is exactly
    the culture you must not carry into the next bucket, and the moment someone
    would be most tempted to — three days of waiting are otherwise wasted."""
    ferment.start_batch(conn, "Tub 1", now=NOW)
    batch_id = ferment.batches(conn, now=NOW)[0]["id"]
    with pytest.raises(ValueError, match="birds ate"):
        ferment.close_batch(conn, batch_id, ferment.DISCARDED, now=NOW, save_liquid=True)
    conn.commit()
    assert ferment.current_starter(conn, now=NOW) is None
    # And the batch is still open: a refused close changes nothing at all.
    assert ferment.batches(conn, now=NOW) != []


def test_a_seeded_batch_is_ready_sooner(conn):
    ferment.start_batch(conn, "Tub 1", now=NOW)
    first = ferment.batches(conn, now=NOW)[0]["id"]
    ferment.close_batch(conn, first, ferment.FED, now=NOW, save_liquid=True)

    ferment.start_batch(conn, "Tub 1", ferment_days=3, now=NOW, use_starter=True)
    conn.commit()
    batch = ferment.batches(conn, now=NOW)[0]
    assert batch["ferment_days"] == ferment.SEEDED_FERMENT_DAYS
    assert batch["state"] == ferment.ACTIVE
    assert ferment.batches(conn, now=_hours(48))[0]["state"] == ferment.READY


def test_a_cold_room_still_wins_over_the_shortcut(conn):
    """Seeding shortens the wait; it does not overrule a keeper who set four
    days because the utility room is 12°C in February. min(), not assignment."""
    ferment.start_batch(conn, "Tub 1", now=NOW)
    first = ferment.batches(conn, now=NOW)[0]["id"]
    ferment.close_batch(conn, first, ferment.FED, now=NOW, save_liquid=True)

    ferment.start_batch(conn, "Tub 1", ferment_days=1, now=NOW, use_starter=True)
    conn.commit()
    assert ferment.batches(conn, now=NOW)[0]["ferment_days"] == 1


def test_using_the_jar_empties_it(conn):
    """One jar, used once. Seeding two buckets from the same 1-2 cups is not a
    thing the fridge can do, so it is not a thing the model should allow."""
    ferment.start_batch(conn, "Tub 1", now=NOW)
    first = ferment.batches(conn, now=NOW)[0]["id"]
    ferment.close_batch(conn, first, ferment.FED, now=NOW, save_liquid=True)

    ferment.start_batch(conn, "Tub 2", now=NOW, use_starter=True)
    conn.commit()
    assert ferment.current_starter(conn, now=NOW) is None
    with pytest.raises(ValueError, match="no saved liquid"):
        ferment.start_batch(conn, "Tub 3", now=NOW, use_starter=True)


def test_generations_count_up_through_the_line(conn):
    """Each pass is one more remove from the wild culture you started with.
    Nothing refuses on it, but it is the only way to notice drift."""
    ferment.start_batch(conn, "Tub 1", now=NOW)
    previous = ferment.batches(conn, now=NOW)[0]["id"]
    ferment.close_batch(conn, previous, ferment.FED, now=NOW, save_liquid=True)

    for expected in (1, 2, 3):
        ferment.start_batch(conn, "Tub 1", now=NOW, use_starter=True)
        batch = ferment.batches(conn, now=NOW)[0]
        assert batch["generation"] == expected
        ferment.close_batch(conn, batch["id"], ferment.FED, now=NOW, save_liquid=True)
    conn.commit()
    assert ferment.current_starter(conn, now=NOW)["generation"] == 3


def test_a_fresh_batch_after_a_seeded_line_starts_at_zero(conn):
    """Declining the jar is starting over, and the generation count should say
    so rather than remembering a lineage this batch is not part of."""
    ferment.start_batch(conn, "Tub 1", now=NOW)
    first = ferment.batches(conn, now=NOW)[0]["id"]
    ferment.close_batch(conn, first, ferment.FED, now=NOW, save_liquid=True)

    ferment.start_batch(conn, "Tub 2", now=NOW)
    conn.commit()
    assert ferment.batches(conn, now=NOW)[0]["generation"] == 0


def test_saving_again_replaces_the_jar_rather_than_stacking(conn):
    """There is one jar in the fridge. A list of five would be a model of
    something that does not exist, and the keeper would have to guess which."""
    for _ in range(3):
        ferment.start_batch(conn, "Tub 1", now=NOW)
        batch_id = ferment.batches(conn, now=NOW)[0]["id"]
        ferment.close_batch(conn, batch_id, ferment.FED, now=NOW, save_liquid=True)
    conn.commit()
    unused = conn.execute(
        "SELECT COUNT(*) AS n FROM ferment_starter WHERE used_at IS NULL").fetchone()["n"]
    assert unused == 1


def test_an_old_jar_is_flagged_but_never_refused(conn):
    """A quiet culture gives you the wait you were avoiding plus a false sense
    that you were not waiting. Worth saying; not worth blocking on — the keeper
    can smell it and we cannot."""
    ferment.start_batch(conn, "Tub 1", now=NOW)
    batch_id = ferment.batches(conn, now=NOW)[0]["id"]
    ferment.close_batch(conn, batch_id, ferment.FED, now=NOW, save_liquid=True)
    conn.commit()

    fresh = ferment.current_starter(conn, now=_hours(24))
    assert fresh["stale"] is False and fresh["age_days"] == 1.0

    old = _hours(24 * (ferment.STARTER_GOOD_FOR_DAYS + 1))
    assert ferment.current_starter(conn, now=old)["stale"] is True
    ferment.start_batch(conn, "Tub 2", now=old, use_starter=True)
    assert ferment.batches(conn, now=old)[0]["generation"] == 1


def test_a_long_line_suggests_starting_clean(conn):
    conn.execute(
        "INSERT INTO ferment_starter (saved_at, generation) VALUES (?, ?)",
        (NOW.isoformat(timespec="seconds"), ferment.STARTER_GENERATION_HINT - 2))
    conn.commit()
    assert ferment.current_starter(conn, now=NOW)["many_generations"] is False

    ferment.discard_starter(conn)
    conn.execute(
        "INSERT INTO ferment_starter (saved_at, generation) VALUES (?, ?)",
        (NOW.isoformat(timespec="seconds"), ferment.STARTER_GENERATION_HINT - 1))
    conn.commit()
    assert ferment.current_starter(conn, now=NOW)["many_generations"] is True


def test_discarding_the_jar_is_the_way_back_to_a_clean_start(conn):
    ferment.start_batch(conn, "Tub 1", now=NOW)
    batch_id = ferment.batches(conn, now=NOW)[0]["id"]
    ferment.close_batch(conn, batch_id, ferment.FED, now=NOW, save_liquid=True)
    ferment.discard_starter(conn)
    conn.commit()
    assert ferment.current_starter(conn, now=NOW) is None


def test_the_jar_survives_the_batch_it_came_from_being_deleted(conn):
    """Deliberately no foreign key. Pruning old history should not silently
    take the culture in the fridge with it."""
    ferment.start_batch(conn, "Tub 1", now=NOW)
    batch_id = ferment.batches(conn, now=NOW)[0]["id"]
    ferment.close_batch(conn, batch_id, ferment.FED, now=NOW, save_liquid=True)
    conn.execute("DELETE FROM ferment_batches WHERE id = ?", (batch_id,))
    conn.commit()
    assert ferment.current_starter(conn, now=NOW) is not None


def test_the_summary_carries_the_jar(conn):
    assert ferment.summary(conn, 5, now=NOW)["starter"] is None
    ferment.start_batch(conn, "Tub 1", now=NOW)
    batch_id = ferment.batches(conn, now=NOW)[0]["id"]
    ferment.close_batch(conn, batch_id, ferment.FED, now=NOW, save_liquid=True)
    conn.commit()
    assert ferment.summary(conn, 5, now=NOW)["starter"]["generation"] == 0


def test_seeding_an_unknown_batch_is_refused(conn):
    with pytest.raises(ValueError, match="no such batch"):
        ferment.save_starter(conn, 999)


# --- backslopping over HTTP ---------------------------------------------------


def test_the_full_cycle_through_the_routes(client, set_options):
    """Feed, keep the liquid, seed the next one — the loop the keeper actually
    runs, start to finish."""
    set_options(ferment_enabled=True, flock_isabrown_count=5)

    first = client.post("/api/ferment/batches", json={"container": "Tub 1"}).get_json()
    batch_id = first["batches"][0]["id"]
    assert first["starter"] is None

    fed = client.post(f"/api/ferment/batches/{batch_id}/close",
                      json={"outcome": "fed", "save_liquid": True}).get_json()
    assert fed["starter"]["generation"] == 0
    assert fed["seeded_days"] == ferment.SEEDED_FERMENT_DAYS

    seeded = client.post("/api/ferment/batches",
                         json={"container": "Tub 1", "use_starter": True}).get_json()
    assert seeded["batches"][0]["generation"] == 1
    assert seeded["batches"][0]["ferment_days"] == ferment.SEEDED_FERMENT_DAYS
    assert seeded["starter"] is None


def test_seeding_with_an_empty_fridge_is_a_400(client, set_options):
    set_options(ferment_enabled=True)
    response = client.post("/api/ferment/batches",
                           json={"container": "Tub 1", "use_starter": True})
    assert response.status_code == 400
    assert "saved liquid" in response.get_json()["error"]


def test_keeping_the_liquid_from_a_binned_batch_is_a_400(client, set_options):
    set_options(ferment_enabled=True)
    started = client.post("/api/ferment/batches", json={"container": "Tub 1"}).get_json()
    batch_id = started["batches"][0]["id"]
    response = client.post(f"/api/ferment/batches/{batch_id}/close",
                           json={"outcome": "discarded", "save_liquid": True})
    assert response.status_code == 400
    assert client.get("/api/ferment").get_json()["starter"] is None


def test_the_jar_can_be_thrown_out_over_http(client, set_options):
    set_options(ferment_enabled=True)
    started = client.post("/api/ferment/batches", json={"container": "Tub 1"}).get_json()
    client.post(f"/api/ferment/batches/{started['batches'][0]['id']}/close",
                json={"outcome": "fed", "save_liquid": True})
    assert client.get("/api/ferment").get_json()["starter"] is not None
    assert client.delete("/api/ferment/starter").get_json()["starter"] is None


def test_the_starter_table_is_in_the_change_feed(client):
    """The lineage of the culture is data about the flock's feed, so a
    downstream pipeline should see it like everything else."""
    import app as coop
    assert "ferment_starter" in coop.TRACKED_TABLES


# --- the jar on the card ------------------------------------------------------


def test_the_jar_is_shown_on_the_card():
    html, js = _static("index.html"), _static("app.js")
    assert "ferment-starter" in html
    assert "renderStarter(" in js


def test_keeping_the_liquid_is_only_offered_on_a_fed_batch():
    """Never on a binned one. The server refuses it either way, but a UI that
    asks the question at all invites somebody to answer yes."""
    js = _static("app.js")
    handler = js[js.index('document.getElementById("ferment-batches").addEventListener'):]
    handler = handler[:handler.index("});")]
    assert 'close.dataset.outcome === "fed" && confirm(' in handler
    assert "save_liquid: saveLiquid" in handler


def test_an_empty_fridge_offers_no_seeding():
    """The prompt is conditional on there being a jar, so declining is the
    default rather than a dialog you dismiss every time."""
    js = _static("app.js")
    fn = js[js.index('document.getElementById("ferment-new")'):]
    assert "!!current.starter && confirm(" in fn
    assert "use_starter: useStarter" in fn


def test_a_stale_jar_is_the_only_starter_state_that_is_coloured():
    css = _static("style.css")
    assert ".starter-stale" in css and ".ferment-warn" in css


def test_an_existing_install_gains_the_generation_column():
    """1.45.0 shipped ferment_batches without it, and CREATE TABLE IF NOT
    EXISTS will not add a column to a table that is already there. Without the
    migration every read of a batch would fail on an add-on that had been
    fermenting perfectly happily the day before."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE ferment_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT, container TEXT NOT NULL,
            started_at TEXT NOT NULL, ferment_days INTEGER NOT NULL DEFAULT 3,
            grams REAL, notes TEXT, closed_at TEXT, outcome TEXT)
    """)
    conn.execute("INSERT INTO ferment_batches (container, started_at) VALUES (?, ?)",
                 ("Tub 1", NOW.isoformat(timespec="seconds")))

    ferment.create_schema(conn)
    conn.commit()

    # The batch that predates the feature reads as unseeded, which it was.
    assert ferment.batches(conn, now=NOW)[0]["generation"] == 0
    ferment.create_schema(conn)  # and running it twice is not an error
