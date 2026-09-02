"""Herbal tonics: garlic in the water, oregano in the feed, and the reminder.

Unlike a ferment, nothing goes mouldy if one is missed — which is precisely why
it needs a reminder. It does not fail, it just quietly stops happening, and six
weeks later nobody can say when the birds last had anything.

The module is deliberately modest about what these are. They are supplements,
not medicine, and a schedule of them tidily ticked off would otherwise imply
that the flock's health is handled. Several tests below exist to pin that the
app keeps saying so.
"""
import datetime

import pytest

import tonics

NOW = datetime.datetime(2026, 9, 2, 9, 0)


def _days(n):
    return NOW + datetime.timedelta(days=n)


@pytest.fixture
def routine(conn):
    return tonics.add_routine(conn, "Garlic in the water",
                              dose="1 clove per litre", cadence_days=7, now=NOW)


# --- the schedule -------------------------------------------------------------


def test_a_routine_never_given_is_due_immediately(conn, routine):
    """The one you just added and have not started is exactly the one worth
    being reminded about."""
    [item] = tonics.routines(conn, now=NOW)
    assert item["never_given"] is True
    assert item["due"] is True
    assert item["last_given_at"] is None


def test_giving_it_clears_the_due_state(conn, routine):
    tonics.log_dose(conn, routine, now=NOW)
    [item] = tonics.routines(conn, now=NOW)
    assert item["due"] is False
    assert item["doses"] == 1
    assert item["next_due_at"] == _days(7).isoformat(timespec="seconds")


def test_it_falls_due_again_after_its_cadence(conn, routine):
    tonics.log_dose(conn, routine, now=NOW)
    assert tonics.routines(conn, now=_days(6))[0]["due"] is False
    assert tonics.routines(conn, now=_days(7))[0]["due"] is True


def test_a_few_days_late_is_due_but_not_overdue(conn, routine):
    """A tonic missed by a day is not an event. Colouring it would spend the
    card's one alarm on something that does not need one."""
    tonics.log_dose(conn, routine, now=NOW)
    at_nine = tonics.routines(conn, now=_days(9))[0]
    assert at_nine["due"] is True and at_nine["overdue"] is False
    assert tonics.routines(conn, now=_days(11))[0]["overdue"] is True


def test_the_most_recent_dose_is_what_counts(conn, routine):
    tonics.log_dose(conn, routine, now=NOW)
    tonics.log_dose(conn, routine, now=_days(3))
    [item] = tonics.routines(conn, now=_days(4))
    assert item["last_given_at"] == _days(3).isoformat(timespec="seconds")
    assert item["doses"] == 2


def test_cadence_is_held_to_something_sane(conn):
    """A blank or zero reads as "not specified" and takes the weekly default;
    an absurd number is clamped rather than refused, since the keeper plainly
    meant *something* and a 400 on the way to adding garlic is not help."""
    tonics.add_routine(conn, "Blank", cadence_days=0, now=NOW)
    tonics.add_routine(conn, "Absurd", cadence_days=99999, now=NOW)
    cadences = sorted(r["cadence_days"] for r in tonics.routines(conn, now=NOW))
    assert cadences == [tonics.DEFAULT_CADENCE_DAYS, 365]


def test_a_routine_needs_a_name(conn):
    with pytest.raises(ValueError, match="name"):
        tonics.add_routine(conn, "   ")


def test_giving_or_pausing_something_that_is_not_there(conn):
    with pytest.raises(ValueError, match="no such routine"):
        tonics.log_dose(conn, 999)
    with pytest.raises(ValueError, match="no such routine"):
        tonics.set_active(conn, 999, False)


# --- pausing rather than deleting ---------------------------------------------


def test_a_paused_routine_drops_off_the_card_but_keeps_its_history(conn, routine):
    """Paused over winter, back in spring with its record intact. Deleting and
    re-adding would lose that."""
    tonics.log_dose(conn, routine, now=NOW)
    tonics.set_active(conn, routine, False)

    assert tonics.routines(conn, now=NOW) == []
    [item] = tonics.routines(conn, now=NOW, include_inactive=True)
    assert item["doses"] == 1

    tonics.set_active(conn, routine, True)
    assert tonics.routines(conn, now=NOW)[0]["doses"] == 1


def test_a_paused_routine_never_notifies(conn, routine):
    tonics.set_active(conn, routine, False)
    assert tonics.due(conn, now=_days(30)) == []


def test_deleting_takes_the_doses_with_it(conn, routine):
    """The REFERENCES clause is documentation here: this app does not enable
    PRAGMA foreign_keys (ARCHITECTURE.md §18), so the cascade is done by hand.
    Without it, deleting a routine orphans rows nothing can ever reach again —
    which is what this caught."""
    tonics.log_dose(conn, routine, now=NOW)
    tonics.delete_routine(conn, routine)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) AS n FROM tonic_doses").fetchone()["n"] == 0


# --- what ships ---------------------------------------------------------------


def test_the_shipped_routines_carry_a_dose_and_a_caution(conn):
    """The dose is the useful part and the caution is the important one. A
    routine with a name and nothing else would be a to-do item."""
    tonics.seed_if_empty(conn, now=NOW)
    seeded = tonics.routines(conn, now=NOW)
    assert len(seeded) == len(tonics.SEEDS)
    assert all(r["dose"] and r["notes"] for r in seeded)


def test_the_cider_vinegar_routine_warns_about_metal_drinkers(conn):
    """The one genuinely dangerous mistake in this list: acid on a galvanised
    drinker leaches zinc, and that poisons birds. The gut-health claims are
    thin; this risk is not, so it is stated in capitals on the card."""
    tonics.seed_if_empty(conn, now=NOW)
    [vinegar] = [r for r in tonics.routines(conn, now=NOW) if "vinegar" in r["name"].lower()]
    assert "NEVER" in vinegar["notes"]
    assert "galvanised" in vinegar["notes"]


def test_the_garlic_routine_says_more_is_not_better(conn):
    """Garlic is an allium, and alliums in quantity cause anaemia in birds. A
    keeper reading "good for immunity" and doubling it is the failure this
    sentence exists to prevent."""
    tonics.seed_if_empty(conn, now=NOW)
    [garlic] = [r for r in tonics.routines(conn, now=NOW) if "garlic" in r["name"].lower()]
    assert "More is not better" in garlic["notes"]


def test_seeding_happens_once_and_never_undoes_a_deletion(conn):
    """Re-seeding on every start would put back the ones a keeper deliberately
    removed, which reads as the app fighting them."""
    tonics.seed_if_empty(conn, now=NOW)
    routines = tonics.routines(conn, now=NOW)
    tonics.delete_routine(conn, routines[0]["id"])

    assert tonics.seed_if_empty(conn, now=NOW) == 0
    assert len(tonics.routines(conn, now=NOW)) == len(tonics.SEEDS) - 1


# --- the reminder -------------------------------------------------------------


def test_nothing_due_means_no_message():
    assert tonics.reminder_message([]) is None


def test_one_due_routine_is_named(conn, routine):
    """"The chickens need something" is a reminder you learn to dismiss."""
    assert tonics.reminder_message(tonics.due(conn, now=NOW)) == \
        "Time for the flock's garlic in the water."


def test_two_are_both_named(conn):
    tonics.add_routine(conn, "Garlic", now=NOW)
    tonics.add_routine(conn, "Oregano", now=NOW)
    message = tonics.reminder_message(tonics.due(conn, now=NOW))
    assert "garlic" in message and "oregano" in message


def test_several_are_counted_rather_than_listed(conn):
    """A list of five in a notification is a wall, and the card is one tap
    away for the rest."""
    for name in ("Garlic", "Oregano", "Vinegar", "Greens"):
        tonics.add_routine(conn, name, now=NOW)
    message = tonics.reminder_message(tonics.due(conn, now=NOW))
    assert message.startswith("4 tonics are due")


# --- the tick -----------------------------------------------------------------


@pytest.fixture
def notified(monkeypatch):
    import app as coop

    class _Sent(list):
        services = None

    sent = _Sent()
    sent.services = []
    monkeypatch.setattr(coop, "send_notification",
                        lambda msg, title=None, service=None: (
                            sent.append(msg), sent.services.append(service)))
    monkeypatch.setattr(coop, "_tonic_last_notified", None)
    return sent


def _configure(set_options, **overrides):
    set_options(**{"tonic_enabled": True, "notify_service": "mobile_app_x",
                   "tonic_times": "09:00", **overrides})


def test_a_due_tonic_notifies(conn, set_options, notified):
    import app as coop
    _configure(set_options)
    tonics.add_routine(conn, "Garlic in the water", now=NOW)
    conn.commit()
    coop._tonic_tick(datetime.datetime(2026, 9, 2, 9, 5), conn)
    assert len(notified) == 1 and "garlic" in notified[0]


def test_nothing_due_stays_quiet(conn, set_options, notified):
    import app as coop
    _configure(set_options)
    routine_id = tonics.add_routine(conn, "Garlic", now=NOW)
    tonics.log_dose(conn, routine_id, now=NOW)
    conn.commit()
    coop._tonic_tick(datetime.datetime(2026, 9, 2, 9, 5), conn)
    assert notified == []


def test_one_notification_per_day_not_one_per_tick(conn, set_options, notified):
    import app as coop
    _configure(set_options)
    tonics.add_routine(conn, "Garlic", now=NOW)
    conn.commit()
    for minute in (5, 10, 30):
        coop._tonic_tick(datetime.datetime(2026, 9, 2, 9, minute), conn)
    assert len(notified) == 1


def test_the_next_day_notifies_again(conn, set_options, notified):
    import app as coop
    _configure(set_options)
    tonics.add_routine(conn, "Garlic", now=NOW)
    conn.commit()
    coop._tonic_tick(datetime.datetime(2026, 9, 2, 9, 5), conn)
    coop._tonic_tick(datetime.datetime(2026, 9, 3, 9, 5), conn)
    assert len(notified) == 2


def test_before_the_window_nothing_fires(conn, set_options, notified):
    import app as coop
    _configure(set_options)
    tonics.add_routine(conn, "Garlic", now=NOW)
    conn.commit()
    coop._tonic_tick(datetime.datetime(2026, 9, 2, 7, 0), conn)
    assert notified == []


def test_tonics_can_have_their_own_notify_service(conn, set_options, notified):
    import app as coop
    _configure(set_options, notify_service="mobile_app_eggs",
               tonic_notify_service="mobile_app_herbs")
    tonics.add_routine(conn, "Garlic", now=NOW)
    conn.commit()
    coop._tonic_tick(datetime.datetime(2026, 9, 2, 9, 5), conn)
    assert notified.services == ["mobile_app_herbs"]


def test_nothing_fires_while_the_feature_is_off(conn, set_options, notified):
    import app as coop
    _configure(set_options, tonic_enabled=False)
    tonics.add_routine(conn, "Garlic", now=NOW)
    conn.commit()
    coop._tonic_tick(datetime.datetime(2026, 9, 2, 9, 5), conn)
    assert notified == []


# --- the HTTP surface ---------------------------------------------------------


def test_the_routines_seed_on_first_read_only_when_enabled(client, set_options):
    """A keeper who never turns this on should not find four rows they did not
    ask for sitting in their database."""
    set_options(tonic_enabled=False)
    assert client.get("/api/tonics").get_json()["routines"] == []

    set_options(tonic_enabled=True)
    assert len(client.get("/api/tonics").get_json()["routines"]) == len(tonics.SEEDS)


def test_the_lifecycle_through_the_routes(client, set_options):
    set_options(tonic_enabled=True)
    added = client.post("/api/tonics", json={
        "name": "Nettle tea", "dose": "a handful, wilted", "cadence_days": 5}).get_json()
    routine = [r for r in added["routines"] if r["name"] == "Nettle tea"][0]
    assert routine["due"] is True

    given = client.post(f"/api/tonics/{routine['id']}/given").get_json()
    assert [r for r in given["routines"] if r["id"] == routine["id"]][0]["due"] is False

    paused = client.post(f"/api/tonics/{routine['id']}/active",
                         json={"active": False}).get_json()
    assert routine["id"] not in [r["id"] for r in paused["routines"]]

    assert client.delete(f"/api/tonics/{routine['id']}").status_code == 200


def test_a_routine_without_a_name_is_a_400(client, set_options):
    set_options(tonic_enabled=True)
    assert client.post("/api/tonics", json={"name": " "}).status_code == 400


def test_giving_an_unknown_routine_is_a_404(client, set_options):
    set_options(tonic_enabled=True)
    assert client.post("/api/tonics/999/given").status_code == 404


def test_the_history_lists_what_was_given(client, set_options):
    set_options(tonic_enabled=True)
    routine = client.get("/api/tonics").get_json()["routines"][0]
    client.post(f"/api/tonics/{routine['id']}/given")
    [entry] = client.get("/api/tonics/history").get_json()
    assert entry["name"] == routine["name"]


def test_the_tonic_tables_are_in_the_change_feed(client):
    import app as coop
    assert "tonic_routines" in coop.TRACKED_TABLES
    assert "tonic_doses" in coop.TRACKED_TABLES


# --- the card -----------------------------------------------------------------


def _static(name):
    import os
    sub = "templates" if name.endswith(".html") else "static"
    with open(os.path.join(os.path.dirname(__file__), "..", sub, name),
              encoding="utf-8") as handle:
        return handle.read()


def test_the_card_says_these_are_not_medicine():
    """On the card, not in the docs. A tidy schedule of tonics ticked off
    quietly implies the flock's health is handled, and it is the implication
    rather than any single claim that misleads."""
    html = _static("index.html")
    assert "Supplements, not medicine" in html
    assert "needs a vet" in html


def test_only_a_well_overdue_tonic_is_coloured():
    """"Due" on a weekly rhythm is not an emergency, and spending the card's
    one alarm on it would bury the ferment row that is."""
    css = _static("style.css")
    assert ".tonic-overdue" in css
    js = _static("app.js")
    # To the end of the function, not to the next getElementById — that one
    # appears *inside* renderTonics, and cutting there reads as a missing line.
    start = js.index("function renderTonics(")
    fn = js[start:js.index("\n}\n", start)]
    assert 'r.overdue ? " tonic-overdue" : ""' in fn
    assert "r.due ?" in fn and "tonic-due" not in css


def test_the_dose_is_shown_on_the_row():
    """"Garlic" is a reminder to do something you then have to go and look up."""
    js = _static("app.js")
    assert "tonic-dose" in js and "tonic-dose" in _static("style.css")


def test_removing_a_routine_asks_first():
    js = _static("app.js")
    handler = js[js.index('document.getElementById("tonic-list").addEventListener'):]
    assert "confirm(" in handler[:800]


def test_an_unreadable_dose_timestamp_does_not_break_the_card(conn, routine):
    """Nothing writes a bad value today, but the card reads every routine to
    draw one row, so a single unparsable date would take the whole card down
    rather than one line. It reads as never given — which is the safe answer,
    since that is the state that asks you to go and look."""
    tonics.log_dose(conn, routine, now=NOW)
    conn.execute("UPDATE tonic_doses SET given_at = 'not a date'")
    conn.commit()
    [item] = tonics.routines(conn, now=_days(30))
    assert item["never_given"] is True
    assert item["due"] is True
    assert item["next_due_at"] is None
