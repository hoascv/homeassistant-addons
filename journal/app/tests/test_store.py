"""Entries, goals, the section template, and what the file on disk gives away."""
from datetime import date, timedelta

import pytest

import crypto
import store
from conftest import PASSWORD, an_entry


# --- The vault ---


def test_a_new_vault_can_be_unlocked_with_its_password(conn):
    created = store.create_vault(conn, PASSWORD)
    assert store.unlock_key(conn, PASSWORD) == created


def test_the_wrong_password_is_refused(conn, key):
    with pytest.raises(crypto.WrongPassword):
        store.unlock_key(conn, PASSWORD + "!")


def test_a_short_password_is_refused_before_anything_is_written(conn):
    with pytest.raises(ValueError):
        store.create_vault(conn, "short")
    assert not store.vault_exists(conn)


def test_a_second_vault_cannot_overwrite_the_first(conn, key):
    """Otherwise a stray POST would replace the salt and verifier, and every
    entry in the database would become undecryptable in one move."""
    with pytest.raises(ValueError):
        store.create_vault(conn, "another password entirely")


def test_the_password_is_nowhere_in_the_database(conn, key, db_path):
    raw = open(db_path, "rb").read()
    assert PASSWORD.encode() not in raw


def test_a_new_vault_starts_with_the_default_sections(conn, key):
    titles = [section["title"] for section in store.get_sections(conn, key)]
    assert titles == [section["title"] for section in store.DEFAULT_SECTIONS]


# --- What the disk gives away ---


def test_nothing_written_reaches_the_disk_in_the_clear(conn, key, db_path):
    """The one test this add-on exists to pass.

    Every kind of authored text — entry prose, a tag, a goal title, a goal
    note, a section heading someone renamed — goes in, and then the raw
    database file is searched for all of it.
    """
    store.save_sections(conn, key, [{"key": "did", "title": "Confessions", "hint": "unmistakable-hint"}])
    goal_id = store.create_goal(conn, key, "Learn Portuguese", why="unmistakable-why")
    store.save_entry(
        conn,
        key,
        "2026-08-29",
        {
            "sections": [{"key": "did", "title": "Confessions", "text": "unmistakable-prose"}],
            "mood": 4,
            "tags": ["unmistakable-tag"],
            "goals": [{"id": goal_id, "note": "unmistakable-note", "moved": True}],
        },
    )
    conn.commit()

    raw = open(db_path, "rb").read()
    for secret in (
        b"unmistakable-prose",
        b"unmistakable-tag",
        b"unmistakable-note",
        b"unmistakable-why",
        b"unmistakable-hint",
        b"Confessions",
        b"Learn Portuguese",
    ):
        assert secret not in raw, f"{secret!r} is sitting in the database in the clear"


def test_the_skeleton_is_deliberately_readable(conn, key, db_path):
    """The other half of the promise, stated as a test: dates and status stay
    in the clear on purpose, so a locked add-on can still count a streak. If
    this ever fails, the streak sensor and the reminder have gone with it."""
    store.save_entry(conn, key, "2026-08-29", an_entry())
    conn.commit()
    assert b"2026-08-29" in open(db_path, "rb").read()


def test_the_entries_table_holds_only_a_blob_and_dates(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(entries)")}
    assert columns == {"day", "blob", "created_at", "updated_at"}


def test_the_goals_table_never_gets_a_title_column(conn):
    """A convenience column for sorting by title would quietly undo the
    encryption for every goal in one migration."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(goals)")}
    assert columns == {"id", "blob", "status", "position", "created_at", "updated_at", "closed_at"}


# --- Changing the password ---


def test_changing_the_password_keeps_every_entry_readable(conn, key):
    store.save_entry(conn, key, "2026-08-28", an_entry("first day"))
    store.save_entry(conn, key, "2026-08-29", an_entry("second day"))
    goal_id = store.create_goal(conn, key, "Swim a mile")

    new_key = store.change_password(conn, PASSWORD, "a brand new password")

    assert store.get_entry(conn, new_key, "2026-08-28")["sections"][0]["text"] == "first day"
    assert store.get_entry(conn, new_key, "2026-08-29")["sections"][0]["text"] == "second day"
    assert store.list_goals(conn, new_key)[0]["title"] == "Swim a mile"
    assert store.get_sections(conn, new_key)[0]["title"] == store.DEFAULT_SECTIONS[0]["title"]


def test_the_old_password_stops_working_afterwards(conn, key):
    store.change_password(conn, PASSWORD, "a brand new password")
    with pytest.raises(crypto.WrongPassword):
        store.unlock_key(conn, PASSWORD)
    assert store.unlock_key(conn, "a brand new password")


def test_the_old_key_can_no_longer_read_the_entries(conn, key):
    store.save_entry(conn, key, "2026-08-29", an_entry())
    store.change_password(conn, PASSWORD, "a brand new password")
    with pytest.raises(crypto.CorruptRecord):
        store.get_entry(conn, key, "2026-08-29")


def test_a_wrong_current_password_changes_nothing(conn, key):
    store.save_entry(conn, key, "2026-08-29", an_entry("still here"))
    with pytest.raises(crypto.WrongPassword):
        store.change_password(conn, "not the password", "a brand new password")
    assert store.get_entry(conn, key, "2026-08-29")["sections"][0]["text"] == "still here"


def test_a_failed_rekey_leaves_the_old_password_working(conn, key, monkeypatch):
    """Half a journal encrypted under each of two passwords would be a journal
    lost. The re-key is one transaction; this breaks it in the middle."""
    store.save_entry(conn, key, "2026-08-27", an_entry("one"))
    store.save_entry(conn, key, "2026-08-28", an_entry("two"))

    calls = {"n": 0}
    real_encrypt = crypto.encrypt

    def exploding_encrypt(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk fell over mid-rekey")
        return real_encrypt(*args, **kwargs)

    monkeypatch.setattr(crypto, "encrypt", exploding_encrypt)
    with pytest.raises(RuntimeError):
        store.change_password(conn, PASSWORD, "a brand new password")

    monkeypatch.undo()
    assert store.unlock_key(conn, PASSWORD) == key
    assert store.get_entry(conn, key, "2026-08-27")["sections"][0]["text"] == "one"
    assert store.get_entry(conn, key, "2026-08-28")["sections"][0]["text"] == "two"


# --- Sections ---


def test_a_renamed_section_keeps_its_key(conn, key):
    sections = store.get_sections(conn, key)
    sections[2]["title"] = "Wins"
    saved = store.save_sections(conn, key, sections)
    assert saved[2]["key"] == "grateful"
    assert saved[2]["title"] == "Wins"


def test_a_new_section_gets_a_key_from_its_title(conn, key):
    saved = store.save_sections(conn, key, [{"title": "What I read today"}])
    assert saved[0]["key"] == "what-i-read-today"


def test_two_sections_cannot_share_a_key(conn, key):
    saved = store.save_sections(conn, key, [{"title": "Notes"}, {"title": "Notes"}, {"title": "Notes!"}])
    assert len({section["key"] for section in saved}) == len(saved)


def test_a_journal_needs_at_least_one_section(conn, key):
    with pytest.raises(ValueError):
        store.save_sections(conn, key, [])


def test_renaming_a_section_does_not_retitle_the_past(conn, key):
    """The heading is snapshotted with the words. What was written under
    "Grateful for" was written under that, whatever it is called now."""
    store.save_entry(conn, key, "2026-08-29", {
        "sections": [{"key": "grateful", "title": "Grateful for", "text": "the ride home"}],
    })
    sections = store.get_sections(conn, key)
    sections[2]["title"] = "Wins"
    store.save_sections(conn, key, sections)
    assert store.get_entry(conn, key, "2026-08-29")["sections"][0]["title"] == "Grateful for"


# --- Entries ---


def test_an_entry_comes_back_as_it_went_in(conn, key):
    store.save_entry(conn, key, "2026-08-29", an_entry("rode to the coast", mood=5, tags=["cycling", "sun"]))
    entry = store.get_entry(conn, key, "2026-08-29")
    assert entry["sections"][0]["text"] == "rode to the coast"
    assert entry["mood"] == 5
    assert entry["tags"] == ["cycling", "sun"]
    assert entry["day"] == "2026-08-29"


def test_saving_the_same_day_twice_replaces_it(conn, key):
    store.save_entry(conn, key, "2026-08-29", an_entry("first go"))
    store.save_entry(conn, key, "2026-08-29", an_entry("second go"))
    assert len(store.entry_days(conn)) == 1
    assert store.get_entry(conn, key, "2026-08-29")["sections"][0]["text"] == "second go"


def test_empty_sections_are_not_stored(conn, key):
    store.save_entry(conn, key, "2026-08-29", {
        "sections": [
            {"key": "did", "title": "What I did", "text": "  "},
            {"key": "thought", "title": "What I was thinking", "text": "about tomorrow"},
        ],
    })
    stored = store.get_entry(conn, key, "2026-08-29")["sections"]
    assert [section["key"] for section in stored] == ["thought"]


def test_an_entry_with_nothing_in_it_is_deleted_rather_than_stored(conn, key):
    """Opening a past day to read it and closing it again must not add a blank
    to the streak."""
    store.save_entry(conn, key, "2026-08-29", an_entry())
    store.save_entry(conn, key, "2026-08-29", {"sections": [{"key": "did", "title": "What I did", "text": ""}]})
    assert store.entry_days(conn) == []


def test_tags_are_normalised(conn, key):
    store.save_entry(conn, key, "2026-08-29", an_entry(tags=["#Work", "work", "  Family  ", ""]))
    assert store.get_entry(conn, key, "2026-08-29")["tags"] == ["work", "family"]


def test_a_mood_outside_the_scale_is_dropped(conn, key):
    store.save_entry(conn, key, "2026-08-29", an_entry(mood=9))
    assert store.get_entry(conn, key, "2026-08-29")["mood"] is None


def test_a_goal_checkin_with_neither_note_nor_tick_is_dropped(conn, key):
    goal_id = store.create_goal(conn, key, "Swim a mile")
    store.save_entry(conn, key, "2026-08-29", an_entry(goals=[{"id": goal_id, "note": "", "moved": False}]))
    assert store.get_entry(conn, key, "2026-08-29")["goals"] == []


def test_a_day_that_is_not_a_date_is_refused(conn, key):
    with pytest.raises(ValueError):
        store.save_entry(conn, key, "not-a-day", an_entry())


def test_a_missing_day_is_none_rather_than_an_error(conn, key):
    assert store.get_entry(conn, key, "2026-01-01") is None


# --- Looking back ---


def test_the_calendar_reports_mood_and_length(conn, key):
    store.save_entry(conn, key, "2026-08-28", an_entry("three words here", mood=2))
    store.save_entry(conn, key, "2026-08-29", an_entry("one", mood=None))
    days = store.calendar(conn, key, "2026-08-01", "2026-08-31")
    assert [d["day"] for d in days] == ["2026-08-28", "2026-08-29"]
    assert days[0]["mood"] == 2
    assert days[0]["words"] == 3


def test_search_finds_a_word_and_says_where(conn, key):
    store.save_entry(conn, key, "2026-08-28", an_entry("we drove to Skagen for the day"))
    store.save_entry(conn, key, "2026-08-29", an_entry("stayed in, read a book"))
    results = store.search(conn, key, "skagen")
    assert [hit["day"] for hit in results] == ["2026-08-28"]
    assert "Skagen" in results[0]["snippet"]
    assert results[0]["section"] == "What I did"


def test_search_covers_tags_and_goal_notes(conn, key):
    goal_id = store.create_goal(conn, key, "Learn to sail")
    store.save_entry(conn, key, "2026-08-27", an_entry("nothing much", tags=["dentist"]))
    store.save_entry(conn, key, "2026-08-28", an_entry("nothing much", goals=[{"id": goal_id, "note": "booked a course", "moved": True}]))
    assert [hit["day"] for hit in store.search(conn, key, "dentist")] == ["2026-08-27"]
    assert [hit["day"] for hit in store.search(conn, key, "booked")] == ["2026-08-28"]


def test_search_is_newest_first(conn, key):
    for day in ("2026-08-25", "2026-08-26", "2026-08-27"):
        store.save_entry(conn, key, day, an_entry("the same word every day"))
    assert [hit["day"] for hit in store.search(conn, key, "same")] == ["2026-08-27", "2026-08-26", "2026-08-25"]


def test_an_empty_search_matches_nothing_rather_than_everything(conn, key):
    store.save_entry(conn, key, "2026-08-29", an_entry())
    assert store.search(conn, key, "   ") == []


def test_on_this_day_finds_the_same_date_in_earlier_years(conn, key):
    store.save_entry(conn, key, "2024-08-29", an_entry("two years back"))
    store.save_entry(conn, key, "2025-08-29", an_entry("one year back"))
    store.save_entry(conn, key, "2025-08-28", an_entry("nearly, but no"))
    found = store.on_this_day(conn, key, "2026-08-29")
    assert [(e["years_ago"], e["sections"][0]["text"]) for e in found] == [
        (1, "one year back"),
        (2, "two years back"),
    ]


def test_on_this_day_survives_the_29th_of_february(conn, key):
    """2025 has no 29 February. Asking for "a year before 2024-02-29" must
    skip that year, not raise."""
    store.save_entry(conn, key, "2024-02-29", an_entry("leap day"))
    assert store.on_this_day(conn, key, "2028-02-29")[0]["years_ago"] == 4


# --- Goals ---


def test_a_goal_round_trips(conn, key):
    goal_id = store.create_goal(conn, key, "Run a half marathon", why="knees", target_date="2026-12-01")
    goal = store.list_goals(conn, key)[0]
    assert (goal["id"], goal["title"], goal["why"], goal["target_date"]) == (
        goal_id, "Run a half marathon", "knees", "2026-12-01",
    )


def test_a_goal_needs_a_title(conn, key):
    with pytest.raises(ValueError):
        store.create_goal(conn, key, "   ")


def test_a_nonsense_target_date_is_dropped_not_stored(conn, key):
    store.create_goal(conn, key, "Sail", target_date="whenever")
    assert store.list_goals(conn, key)[0]["target_date"] is None


def test_days_left_counts_from_today(conn, key):
    target = date(2026, 12, 1)
    store.create_goal(conn, key, "Sail", target_date=target.isoformat())
    goal = store.list_goals(conn, key, today=date(2026, 11, 24))[0]
    assert goal["days_left"] == 7


def test_closing_a_goal_records_when(conn, key):
    goal_id = store.create_goal(conn, key, "Sail")
    store.update_goal(conn, key, goal_id, status="done")
    goal = store.list_goals(conn, key)[0]
    assert goal["status"] == "done" and goal["closed_at"]


def test_reopening_a_goal_clears_the_closing_date(conn, key):
    goal_id = store.create_goal(conn, key, "Sail")
    store.update_goal(conn, key, goal_id, status="done")
    store.update_goal(conn, key, goal_id, status="active")
    assert store.list_goals(conn, key)[0]["closed_at"] is None


def test_an_unknown_status_is_refused(conn, key):
    goal_id = store.create_goal(conn, key, "Sail")
    with pytest.raises(ValueError):
        store.update_goal(conn, key, goal_id, status="nearly")


def test_deleting_a_goal_leaves_what_was_written_about_it(conn, key):
    """A dropped goal is not a reason to rewrite someone's diary."""
    goal_id = store.create_goal(conn, key, "Sail")
    store.save_entry(conn, key, "2026-08-29", an_entry(goals=[{"id": goal_id, "note": "booked lessons", "moved": True}]))
    store.delete_goal(conn, goal_id)
    assert store.get_entry(conn, key, "2026-08-29")["goals"][0]["note"] == "booked lessons"


def test_goal_activity_is_gathered_from_the_days_themselves(conn, key):
    goal_id = store.create_goal(conn, key, "Sail")
    store.save_entry(conn, key, "2026-08-27", an_entry(goals=[{"id": goal_id, "note": "read the theory", "moved": False}]))
    store.save_entry(conn, key, "2026-08-29", an_entry(goals=[{"id": goal_id, "note": "went out", "moved": True}]))
    timeline = store.goal_timeline(conn, key, goal_id)
    assert [point["day"] for point in timeline] == ["2026-08-29", "2026-08-27"]
    assert timeline[0]["moved"] is True


def test_a_quiet_goal_is_flagged(conn, key):
    today = date(2026, 8, 29)
    goal_id = store.create_goal(conn, key, "Sail")
    store.save_entry(conn, key, (today - timedelta(days=10)).isoformat(),
                     an_entry(goals=[{"id": goal_id, "note": "last time", "moved": True}]))
    goal = store.goals_with_activity(conn, key, today, nudge_days=7)[0]
    assert goal["days_since_checkin"] == 10
    assert goal["needs_attention"]


def test_a_goal_checked_in_on_recently_is_not_flagged(conn, key):
    today = date(2026, 8, 29)
    goal_id = store.create_goal(conn, key, "Sail")
    store.save_entry(conn, key, (today - timedelta(days=2)).isoformat(),
                     an_entry(goals=[{"id": goal_id, "note": "went out", "moved": True}]))
    assert not store.goals_with_activity(conn, key, today, nudge_days=7)[0]["needs_attention"]


def test_nudging_can_be_turned_off(conn, key):
    store.create_goal(conn, key, "Sail")
    assert not store.goals_with_activity(conn, key, date(2026, 8, 29), nudge_days=0)[0]["needs_attention"]


def test_a_closed_goal_is_never_flagged(conn, key):
    goal_id = store.create_goal(conn, key, "Sail")
    store.update_goal(conn, key, goal_id, status="done")
    assert not store.goals_with_activity(conn, key, date(2026, 8, 29), nudge_days=7)[0]["needs_attention"]


# --- Statistics ---


def test_a_streak_counts_back_from_today(conn, key):
    today = date(2026, 8, 29)
    for offset in (0, 1, 2, 4):
        store.save_entry(conn, key, (today - timedelta(days=offset)).isoformat(), an_entry())
    assert store.streak(conn, today) == 3


def test_a_streak_survives_a_today_that_is_not_written_yet(conn, key):
    """Otherwise the streak would collapse to zero every midnight and come
    back in the evening, which is not what a streak means."""
    today = date(2026, 8, 29)
    for offset in (1, 2, 3):
        store.save_entry(conn, key, (today - timedelta(days=offset)).isoformat(), an_entry())
    assert store.streak(conn, today) == 3


def test_a_streak_of_nothing_is_zero(conn, key):
    assert store.streak(conn, date(2026, 8, 29)) == 0


def test_the_longest_streak_is_found_anywhere_in_the_history(conn, key):
    for day in ("2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-03-01", "2026-03-02"):
        store.save_entry(conn, key, day, an_entry())
    assert store.longest_streak(conn) == 4


def test_stats_need_no_key(conn, key):
    """What the background loop is allowed to see. If this ever needs a key,
    the streak sensor has stopped working while locked — which is every moment
    nobody is looking at the page."""
    store.save_entry(conn, key, "2026-08-29", an_entry())
    store.create_goal(conn, key, "Sail")
    figures = store.stats(conn, date(2026, 8, 29))
    assert figures["entries"] == 1
    assert figures["has_entry_today"]
    assert figures["goals_active"] == 1
    assert figures["last_entry_on"] == "2026-08-29"


def test_stats_carry_no_content(conn, key):
    """Belt and braces: the sensor payload is built from this, and a stray
    field here would put a person's words into Home Assistant's recorder."""
    store.save_entry(conn, key, "2026-08-29", an_entry("something private"))
    blob = repr(store.stats(conn, date(2026, 8, 29)))
    assert "private" not in blob


def test_export_returns_everything_in_the_clear(conn, key):
    store.save_entry(conn, key, "2026-08-29", an_entry("for the export"))
    store.create_goal(conn, key, "Sail")
    dump = store.export_all(conn, key)
    assert dump["entries"][0]["sections"][0]["text"] == "for the export"
    assert dump["goals"][0]["title"] == "Sail"
    assert len(dump["sections"]) == len(store.DEFAULT_SECTIONS)
