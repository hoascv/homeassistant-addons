"""The sensors, which are the add-on's actual output.

The dashboard is for a human looking on purpose; the sensors are what an
automation acts on at three in the morning, and they are the part nothing else
checks. What matters here is less the happy path than the shape of it: a state
that stays usable as add-ons are added, attributes that are absent rather than
null when there is no answer, and a push failure that is reported per entity
instead of aborting the rest.

The Supervisor and Core calls are stubbed at `urlopen`, one layer below the
functions under test, so the URL, method and payload actually built are what
gets asserted — stubbing `_api` would leave that untested.
"""
import io
import json
import urllib.error

import pytest

import watchdog


@pytest.fixture(autouse=True)
def _supervisor_token(monkeypatch):
    """Every call refuses without one, which is correct but would make each of
    these a test of that single guard."""
    monkeypatch.setattr(watchdog, "SUPERVISOR_TOKEN", "test-token")


def _row(**values):
    base = {"slug": "journal", "name": "Journal", "installed": True, "status": "ok"}
    base.update(values)
    return watchdog._row(**base)


class _Capture:
    """Records what was sent and replies with a canned body."""

    def __init__(self, body=b'{"result": "ok"}', status=200):
        self.calls = []
        self._body = body
        self._status = status

    def __call__(self, req, data=None, timeout=None):
        self.calls.append({
            "url": req.full_url,
            "method": req.get_method(),
            "headers": dict(req.header_items()),
            "payload": json.loads(data) if data else None,
        })
        return _Response(self._body, self._status)


class _Response(io.BytesIO):
    def __init__(self, body, status=200):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


# --- the Supervisor / Core call layer -----------------------------------------


def test_a_call_without_a_token_fails_before_it_is_made(monkeypatch):
    """Running outside Supervisor. Saying so beats a connection error that looks
    like a network fault."""
    monkeypatch.setattr(watchdog, "SUPERVISOR_TOKEN", "")
    body, err = watchdog._api(watchdog.SUPERVISOR_API, "/addons")
    assert body is None
    assert "SUPERVISOR_TOKEN not set" in err


def test_a_push_sends_state_and_attributes_to_the_core_api(monkeypatch):
    capture = _Capture()
    monkeypatch.setattr(watchdog.urllib.request, "urlopen", capture)
    watchdog.push_sensor("sensor.x", "ok", {"friendly_name": "X"})

    call = capture.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/states/sensor.x")
    assert call["payload"] == {"state": "ok", "attributes": {"friendly_name": "X"}}
    assert call["headers"]["Authorization"] == "Bearer test-token"


def test_an_http_error_is_returned_with_its_body_not_raised(monkeypatch):
    """A 401 from Core has to reach the log as a reason, because the add-on
    keeps scanning either way and the reason is the only clue."""
    def boom(req, data=None, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {},
                                     io.BytesIO(b"bad token"))

    monkeypatch.setattr(watchdog.urllib.request, "urlopen", boom)
    body, err = watchdog.push_sensor("sensor.x", "ok", {})
    assert body is None
    assert err.startswith("HTTP 401")
    assert "bad token" in err


def test_a_transport_failure_is_returned_not_raised(monkeypatch):
    """This runs on a timer; an exception escaping here would end the loop."""
    def boom(req, data=None, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(watchdog.urllib.request, "urlopen", boom)
    body, err = watchdog.supervisor_addons()
    assert body == []
    assert "connection refused" in err


def test_supervisor_addons_unwraps_the_data_envelope(monkeypatch):
    monkeypatch.setattr(watchdog.urllib.request, "urlopen",
                        _Capture(json.dumps({"data": {"addons": [{"slug": "a"}]}}).encode()))
    addons, err = watchdog.supervisor_addons()
    assert err is None
    assert addons == [{"slug": "a"}]


def test_a_missing_data_envelope_is_empty_not_a_crash(monkeypatch):
    """A Supervisor version that answers differently should degrade, not take
    the scan loop down with it."""
    monkeypatch.setattr(watchdog.urllib.request, "urlopen", _Capture(b'{"unexpected": 1}'))
    assert watchdog.supervisor_addons() == ([], None)
    assert watchdog.supervisor_addon_info("x") == ({}, None)
    assert watchdog.supervisor_addon_stats("x") == ({}, None)


# --- the summary sensor -------------------------------------------------------


def test_the_summary_state_is_a_count_so_it_survives_new_addons(monkeypatch):
    """A word like "unhealthy" would need re-deciding every time an add-on is
    added; `> 0` is the whole condition an automation ever needs."""
    capture = _Capture()
    monkeypatch.setattr(watchdog.urllib.request, "urlopen", capture)
    watchdog.publish({"addons": [], "unhealthy": 3, "updates": 1})

    summary = [c for c in capture.calls if c["url"].endswith("_unhealthy")][0]
    assert summary["payload"]["state"] == "3"
    assert summary["payload"]["attributes"]["unit_of_measurement"] == "add-ons"
    assert summary["payload"]["attributes"]["updates_available"] == 1


def test_the_summary_names_the_degraded_addons(monkeypatch):
    """So the notification can say which one without a second lookup."""
    capture = _Capture()
    monkeypatch.setattr(watchdog.urllib.request, "urlopen", capture)
    watchdog.publish({
        "unhealthy": 2,
        "addons": [
            _row(slug="journal", status="degraded"),
            _row(slug="knowledge", status="ok"),
            _row(slug="pipeline-spark", status="degraded"),
        ],
    })
    summary = [c for c in capture.calls if c["url"].endswith("_unhealthy")][0]
    assert summary["payload"]["attributes"]["degraded"] == ["journal", "pipeline-spark"]


def test_the_summary_records_whether_stopped_was_counted(monkeypatch):
    """Otherwise a zero is ambiguous: nothing wrong, or stopped add-ons ignored."""
    capture = _Capture()
    monkeypatch.setattr(watchdog.urllib.request, "urlopen", capture)
    watchdog.publish({"addons": [], "unhealthy": 0}, ignore_stopped=False)
    summary = capture.calls[0]
    assert summary["payload"]["attributes"]["counts_stopped"] is True


# --- one sensor per add-on ----------------------------------------------------


def test_an_uninstalled_addon_gets_no_sensor(monkeypatch):
    """Publishing one would create an entity for something that is not there,
    and it would never go away."""
    capture = _Capture()
    monkeypatch.setattr(watchdog.urllib.request, "urlopen", capture)
    pushed, errors = watchdog.publish({
        "unhealthy": 0,
        "addons": [_row(slug="journal", installed=False, status="not installed")],
    })
    # `pushed` counts add-on sensors only — the summary is pushed unconditionally
    # and reported separately, so zero here means "no add-on sensor was created".
    assert pushed == 0
    assert errors == []
    assert not [c for c in capture.calls if "journal" in c["url"]]


def test_the_entity_id_is_the_slug_with_underscores(monkeypatch):
    """Hyphens are not legal in an entity id; a sensor named with one would be
    silently unusable in a template."""
    capture = _Capture()
    monkeypatch.setattr(watchdog.urllib.request, "urlopen", capture)
    watchdog.publish({"unhealthy": 0, "addons": [_row(slug="pipeline-postgres-replica")]})
    assert any(
        c["url"].endswith("/states/sensor.addon_watchdog_pipeline_postgres_replica")
        for c in capture.calls
    )


def test_the_state_is_the_status_word_automations_key_off(monkeypatch):
    capture = _Capture()
    monkeypatch.setattr(watchdog.urllib.request, "urlopen", capture)
    watchdog.publish({"unhealthy": 1, "addons": [_row(status="degraded")]})
    row = [c for c in capture.calls if "journal" in c["url"]][0]
    assert row["payload"]["state"] == "degraded"


def test_the_row_attributes_carry_what_the_dashboard_shows(monkeypatch):
    capture = _Capture()
    monkeypatch.setattr(watchdog.urllib.request, "urlopen", capture)
    watchdog.publish({
        "unhealthy": 0,
        "addons": [_row(
            version="1.2.0", version_latest="1.3.0", update_available=True,
            cpu_percent=4.5, memory_usage=268435456, uptime_seconds=3600,
            uptime_known=True, restarts=2, probe="web UI answers",
            probe_detail="HTTP 401", supervisor_watchdog=True,
            disk_read_bps=1000, disk_write_bps=2000,
        )],
    })
    attrs = [c for c in capture.calls if "journal" in c["url"]][0]["payload"]["attributes"]
    assert attrs["friendly_name"] == "Journal status"
    assert attrs["addon_version"] == "1.2.0"
    assert attrs["latest_version"] == "1.3.0"
    assert attrs["update_available"] is True
    assert attrs["memory_usage_mb"] == 256.0  # bytes, reported in MB
    assert attrs["probe_detail"] == "HTTP 401"
    assert attrs["supervisor_watchdog"] is True


def test_an_addon_holding_no_data_has_no_records_attribute(monkeypatch):
    """Absent rather than null, so a template testing the attribute gets a clean
    answer instead of having to distinguish "no data" from "zero rows"."""
    capture = _Capture()
    monkeypatch.setattr(watchdog.urllib.request, "urlopen", capture)
    watchdog.publish({"unhealthy": 0, "addons": [_row(records=None)]})
    attrs = [c for c in capture.calls if "journal" in c["url"]][0]["payload"]["attributes"]
    assert "records" not in attrs
    assert "db_size_mb" not in attrs


def test_record_counts_are_flattened_when_the_addon_reports_them(monkeypatch):
    capture = _Capture()
    monkeypatch.setattr(watchdog.urllib.request, "urlopen", capture)
    watchdog.publish({
        "unhealthy": 0,
        "addons": [_row(slug="coop-tracker", records=1200,
                        record_counts={"eggs": 1000, "feedings": 200},
                        db_bytes=5242880)],
    })
    attrs = [c for c in capture.calls if "coop" in c["url"]][0]["payload"]["attributes"]
    assert attrs["records"] == 1200
    assert attrs["record_counts"] == {"eggs": 1000, "feedings": 200}
    assert attrs["db_size_mb"] == 5.0


def test_a_self_report_is_flattened_one_level_deep(monkeypatch):
    """A Lovelace card reads state_attr(...) one level deep comfortably and a
    dict of dicts awkwardly."""
    capture = _Capture()
    monkeypatch.setattr(watchdog.urllib.request, "urlopen", capture)
    watchdog.publish({
        "unhealthy": 0,
        "addons": [_row(slug="pipeline-postgres", report_ok=True,
                        report_detail="backup 20m ago", report_age=1200,
                        report_metrics={"lag_bytes": 0, "rows": 5})],
    })
    attrs = [c for c in capture.calls if "postgres" in c["url"]][0]["payload"]["attributes"]
    assert attrs["report_ok"] is True
    assert attrs["report"] == "backup 20m ago"
    assert attrs["report_lag_bytes"] == 0
    assert attrs["report_rows"] == 5


def test_one_failed_push_does_not_stop_the_rest(monkeypatch):
    """Publishing is a loop over every add-on; aborting on the first failure
    would mean one bad entity blanks every sensor after it."""
    calls = []

    def flaky(req, data=None, timeout=None):
        calls.append(req.full_url)
        if "knowledge" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, io.BytesIO(b"nope"))
        return _Response(b'{"ok": true}')

    monkeypatch.setattr(watchdog.urllib.request, "urlopen", flaky)
    pushed, errors = watchdog.publish({
        "unhealthy": 0,
        "addons": [_row(slug="journal"), _row(slug="knowledge"), _row(slug="gym-tracker")],
    })

    assert pushed == 2  # journal and gym-tracker; knowledge failed
    assert len(errors) == 1
    assert errors[0].startswith("sensor.addon_watchdog_knowledge:")
    assert any("gym_tracker" in url for url in calls), "stopped early"


def test_a_failed_summary_push_is_reported_too(monkeypatch):
    def boom(req, data=None, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 503, "down", {}, io.BytesIO(b""))

    monkeypatch.setattr(watchdog.urllib.request, "urlopen", boom)
    pushed, errors = watchdog.publish({"unhealthy": 0, "addons": []})
    assert pushed == 0
    assert errors == ["summary: HTTP 503: "]


def test_the_prefix_reaches_every_entity(monkeypatch):
    capture = _Capture()
    monkeypatch.setattr(watchdog.urllib.request, "urlopen", capture)
    watchdog.publish({"unhealthy": 0, "addons": [_row()]}, prefix="spare")
    assert all("/states/sensor.spare_" in c["url"] for c in capture.calls)


# --- bytes to megabytes -------------------------------------------------------


def test_zero_and_none_bytes_report_as_none_not_zero_mb():
    """`0` from Supervisor means "not reported" here rather than an empty
    database, and rounding it to 0.0 MB would state something untrue."""
    assert watchdog._mb(0) is None
    assert watchdog._mb(None) is None


def test_bytes_are_rounded_to_one_decimal():
    assert watchdog._mb(1024 * 1024) == 1.0
    assert watchdog._mb(1536 * 1024) == 1.5


# --- record counts from the trackers ------------------------------------------


def test_stats_are_not_fetched_for_an_addon_that_publishes_none():
    """Journal is the case: it holds a great deal and reports none of it, by
    design. Asking anyway would be a 404 logged every minute."""
    assert watchdog.fetch_stats("journal", "host", 5) == (None, None)


def test_a_tracker_older_than_api_stats_says_so(monkeypatch):
    """404 here is a version gap, not a fault — the hint is the difference
    between "upgrade this add-on" and "something is broken"."""
    def old(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b""))

    monkeypatch.setattr(watchdog.urllib.request, "urlopen", old)
    counts, err = watchdog.fetch_stats("gym-tracker", "host", 5)
    assert counts is None
    assert "predates /api/stats" in err


@pytest.mark.parametrize("code", [401, 403])
def test_a_refused_stats_call_names_the_option_that_fixes_it(monkeypatch, code):
    """401 is the published port wanting api_token, 403 is restrict_to_user_ids.
    Both leave the Records column empty, and an empty column with no explanation
    anywhere is the thing this hint exists to prevent."""
    def refused(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, code, "no", {}, io.BytesIO(b""))

    monkeypatch.setattr(watchdog.urllib.request, "urlopen", refused)
    _, err = watchdog.fetch_stats("coop-tracker", "host", 5)
    assert "api_tokens" in err


def test_an_unreachable_tracker_is_reported_as_such(monkeypatch):
    def down(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(watchdog.urllib.request, "urlopen", down)
    counts, err = watchdog.fetch_stats("knowledge", "host", 5)
    assert counts is None
    assert err.startswith("unreachable:")


def test_a_non_json_stats_body_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(watchdog.urllib.request, "urlopen",
                        lambda req, timeout=None: _Response(b"<html>"))
    counts, err = watchdog.fetch_stats("knowledge", "host", 5)
    assert counts is None
    assert err.startswith("bad JSON:")


def test_a_response_without_counts_is_rejected(monkeypatch):
    """Something answered on that port; it was not a tracker."""
    monkeypatch.setattr(watchdog.urllib.request, "urlopen",
                        lambda req, timeout=None: _Response(b'{"total": 5}'))
    counts, err = watchdog.fetch_stats("knowledge", "host", 5)
    assert counts is None
    assert err == "response had no 'counts' object"


def test_total_all_is_preferred_over_the_tracked_subset(monkeypatch):
    body = json.dumps({
        "total": 100, "total_all": 140,
        "counts": {"topics": 100}, "other_counts": {"scratch": 40},
        "db_bytes": 2097152,
    }).encode()
    monkeypatch.setattr(watchdog.urllib.request, "urlopen",
                        lambda req, timeout=None: _Response(body))
    counts, err = watchdog.fetch_stats("knowledge", "host", 5)
    assert err is None
    assert counts["records"] == 140
    assert counts["record_counts"] == {"topics": 100}
    assert counts["db_bytes"] == 2097152


def test_a_tracker_predating_the_total_split_falls_back_to_total(monkeypatch):
    """Showing nothing because the newer key is absent would be a regression for
    an add-on that is answering perfectly well."""
    monkeypatch.setattr(watchdog.urllib.request, "urlopen",
                        lambda req, timeout=None: _Response(b'{"total": 7, "counts": {"a": 7}}'))
    counts, _ = watchdog.fetch_stats("knowledge", "host", 5)
    assert counts["records"] == 7
    assert counts["other_counts"] == {}
