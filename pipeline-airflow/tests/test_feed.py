"""Reading a tracker: the HTTP contract, and the bootstrap/paging decision.

Every failure mode here cost a round trip to a live add-on at least once, so
each one is pinned to the message that would have saved it.
"""
import pytest

from trackers_feed import MAX_PAGES, PAGE_SIZE, FeedError, get, read_batch


# --- the HTTP contract ------------------------------------------------------


def test_a_successful_read_returns_the_payload_and_sends_the_token(tracker_server):
    tracker_server.serve("/api/export", {"tables": {}, "keys": {}, "max_seq": 7})
    assert get("gym_tracker", tracker_server.base_url, "tok", "/api/export")["max_seq"] == 7
    path, auth = tracker_server.requests[-1]
    assert auth == "Bearer tok"


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_token_says_so_and_names_both_settings(tracker_server, status):
    # The request arrived and was refused, which is a different fix from the
    # request never arriving — the message has to distinguish them.
    tracker_server.serve("/api/export", {"error": "nope"}, status=status)
    with pytest.raises(FeedError) as exc:
        get("gym_tracker", tracker_server.base_url, "wrong", "/api/export")
    assert "rejected the token" in str(exc.value)
    assert "gym_tracker_api_token" in str(exc.value)


def test_another_http_status_is_reported_with_its_code(tracker_server):
    tracker_server.serve("/api/export", {"error": "boom"}, status=500)
    with pytest.raises(FeedError, match="returned HTTP 500"):
        get("gym_tracker", tracker_server.base_url, "tok", "/api/export")


def test_an_unreachable_tracker_names_the_address_it_tried():
    # `Connection refused` alone withholds the only useful fact.
    with pytest.raises(FeedError) as exc:
        get("gym_tracker", "http://127.0.0.1:9", "tok", "/api/export")
    assert "http://127.0.0.1:9/api/export" in str(exc.value)
    assert "gym_tracker add-on is running" in str(exc.value)


# --- bootstrap vs incremental ----------------------------------------------


def _changes(*seqs, table="workout_logs"):
    return [{"table": table, "row_id": str(s), "row": {}, "seq": s,
             "changed_at": None, "actor": "user", "op": "U"} for s in seqs]


def test_a_first_run_takes_a_full_snapshot(tracker_server):
    tracker_server.serve("/api/changes", {"changes": []})
    tracker_server.serve("/api/export", {"tables": {"a": [{}, {}]}, "keys": {"a": "id"},
                                         "max_seq": 42})
    archived = {}
    batch = read_batch("gym_tracker", tracker_server.base_url, "tok", 0, archived.__setitem__)

    assert batch == {"mode": "bootstrap", "max_seq": 42, "from_seq": 0, "pages": 1}
    # The raw response is archived before anything interprets it.
    assert "export.json" in archived


def test_a_watermark_the_feed_cannot_bridge_falls_back_to_a_snapshot(tracker_server):
    # Not a first run, but the change log no longer reaches back that far.
    tracker_server.serve("/api/changes", {"changes": [], "full_reload_required": True})
    tracker_server.serve("/api/export", {"tables": {"a": [{}]}, "keys": {"a": "id"},
                                         "max_seq": 99})
    batch = read_batch("gym_tracker", tracker_server.base_url, "tok", 50, lambda *a: None)
    assert batch["mode"] == "bootstrap"
    assert batch["max_seq"] == 99


def test_nothing_new_is_a_batch_of_no_pages(tracker_server):
    tracker_server.serve("/api/changes", {"changes": []})
    batch = read_batch("gym_tracker", tracker_server.base_url, "tok", 272, lambda *a: None)
    # Zero pages is what lets the DAG short-circuit before paying for Spark.
    assert batch == {"mode": "incremental", "max_seq": 272, "from_seq": 272, "pages": 0}


def test_a_short_page_ends_the_paging(tracker_server):
    tracker_server.serve("/api/changes", {"changes": _changes(10, 11, 12)})
    archived = {}
    batch = read_batch("gym_tracker", tracker_server.base_url, "tok", 9, archived.__setitem__)

    assert batch["mode"] == "incremental"
    # The watermark moves to the last change seen, not to anything reported.
    assert batch["max_seq"] == 12
    assert batch["pages"] == 1
    assert "changes-9.json" in archived


def test_paging_is_bounded_so_a_stale_watermark_cannot_loop_forever(tracker_server):
    # A server that always returns a full page: without MAX_PAGES this never ends.
    full_page = _changes(*range(1, PAGE_SIZE + 1))
    tracker_server.serve("/api/changes", {"changes": full_page})
    batch = read_batch("gym_tracker", tracker_server.base_url, "tok", 0 or 1,
                       lambda *a: None)
    assert batch["pages"] == MAX_PAGES
