"""The logic the dashboard and the sensors both rest on.

No network: probes take a host and a port, so a closed port on localhost is a
perfectly good failure case, and the Supervisor calls are stubbed. What is
worth pinning down is the mapping from (Supervisor state, probe outcome) to a
single word, because that word becomes a sensor state people write automations
against.
"""
import json
import pathlib
import socket
import time

import yaml

import pytest

import watchdog
from watchdog import ProbeResult, derive_status, is_unhealthy, match_slug


# --- the distinction the add-on exists to make --------------------------------


def test_running_but_not_answering_is_degraded():
    """The Pipeline Metastore case: container up, service dead. Supervisor alone
    calls this healthy, which is exactly the gap being closed."""
    assert derive_status("started", ProbeResult(False, "unreachable")) == "degraded"


def test_running_and_answering_is_ok():
    assert derive_status("started", ProbeResult(True, "HTTP 200")) == "ok"


def test_stopped_is_stopped_even_though_no_probe_ran():
    assert derive_status("stopped", None) == "stopped"


def test_started_without_a_probe_is_running_not_ok():
    """pipeline-notebook has nothing reachable to probe. Reporting it as `ok`
    would claim a check that never happened."""
    assert derive_status("started", None) == "running"


# --- what deserves an alert ---------------------------------------------------


def test_degraded_is_always_unhealthy():
    assert is_unhealthy("degraded", ignore_stopped=True) is True


def test_stopped_is_not_unhealthy_by_default():
    """Most of the pipeline is boot: manual and is stopped on purpose; counting
    that would leave the summary permanently non-zero and therefore ignored."""
    assert is_unhealthy("stopped", ignore_stopped=True) is False


def test_stopped_can_be_made_unhealthy():
    assert is_unhealthy("stopped", ignore_stopped=False) is True


@pytest.mark.parametrize("status", ["ok", "running", "not installed"])
def test_healthy_states_are_not_alerts(status):
    assert is_unhealthy(status) is False


# --- slug matching ------------------------------------------------------------


def test_installed_slug_carries_a_repository_prefix():
    """Supervisor prefixes the repository id and swaps hyphens for underscores;
    hard-coding that id would break on someone else's install."""
    assert match_slug("6753e04e_gym_tracker") == "gym-tracker"


def test_bare_slug_matches_too():
    assert match_slug("pipeline-postgres") == "pipeline-postgres"


def test_another_repositorys_addon_is_ignored():
    assert match_slug("a0d7b954_nodered") is None


def test_near_miss_is_not_a_match():
    """`-` before the slug is required, so a longer name ending in our slug's
    letters does not sneak on to the dashboard."""
    assert match_slug("somethinggym-tracker") is None


def test_every_known_slug_matches_itself():
    for slug in watchdog.KNOWN_SLUGS:
        assert match_slug(slug) == slug


def test_every_addon_in_the_repository_has_a_probe_decision():
    """The check that actually bites: a new add-on added to this repository and
    never added to PROBES would simply not appear on the dashboard.

    Asserting PROBES against KNOWN_SLUGS cannot fail — KNOWN_SLUGS is derived
    from PROBES — so this reads the sibling add-on directories instead. A probe
    of None is a legitimate answer (pipeline-notebook); *absence* is not.
    """
    repo = pathlib.Path(__file__).resolve().parents[3]
    slugs = {
        yaml.safe_load(config.read_text())["slug"]
        for config in repo.glob("*/config.yaml")
    }
    assert slugs, f"no add-ons found under {repo}; the path assumption is wrong"
    missing = slugs - set(watchdog.PROBES) - {"addon-watchdog"}
    assert not missing, f"add-ons with no PROBES entry: {sorted(missing)}"


# --- probes -------------------------------------------------------------------


def test_tcp_probe_reports_a_closed_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        closed_port = sock.getsockname()[1]
    result = watchdog.probe_tcp("127.0.0.1", closed_port, timeout=1)
    assert result.ok is False
    assert str(closed_port) in result.detail


def test_tcp_probe_reports_an_open_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        assert watchdog.probe_tcp("127.0.0.1", port, timeout=2).ok is True


def test_no_probe_or_no_hostname_yields_nothing():
    """A missing hostname means Supervisor did not tell us where to look, which
    is not the same as the service being down."""
    assert watchdog.run_probe(None, "host", 1) is None
    assert watchdog.run_probe(watchdog.PROBES["pipeline-postgres"], None, 1) is None


# --- collect() degrades rather than raising -----------------------------------


def test_collect_survives_supervisor_being_unreachable(monkeypatch):
    """This runs on a timer; an unreachable Supervisor has to surface on the
    dashboard, not kill the scan loop."""
    monkeypatch.setattr(watchdog, "supervisor_addons", lambda: ([], "HTTP 401"))
    snapshot = watchdog.collect()
    assert snapshot["error"] == "HTTP 401"
    assert snapshot["addons"] == []
    assert snapshot["unhealthy"] == 0


def test_collect_reports_uninstalled_addons(monkeypatch):
    monkeypatch.setattr(watchdog, "supervisor_addons", lambda: ([], None))
    snapshot = watchdog.collect()
    assert {r["slug"] for r in snapshot["addons"]} == set(watchdog.KNOWN_SLUGS)
    assert all(r["status"] == "not installed" for r in snapshot["addons"])
    # Nothing installed is not the same as something broken.
    assert snapshot["unhealthy"] == 0


def test_every_row_has_every_key(monkeypatch):
    """A not-installed add-on used to be a short dict, and Jinja resolves the
    missing key to Undefined — which passes `is not none` and then dies in the
    filter behind it, 500ing the whole page over one add-on you don't have."""
    monkeypatch.setattr(watchdog, "supervisor_addons", lambda: ([], None))
    for row in watchdog.collect()["addons"]:
        assert set(row) == set(watchdog.ROW_KEYS), row["slug"]


def test_collect_counts_a_degraded_addon(monkeypatch):
    monkeypatch.setattr(
        watchdog,
        "supervisor_addons",
        lambda: ([{"slug": "x_pipeline_postgres", "name": "Pipeline Postgres",
                   "state": "started", "version": "1.1.0", "version_latest": "1.1.0"}], None),
    )
    monkeypatch.setattr(
        watchdog, "supervisor_addon_info",
        lambda slug: ({"state": "started", "hostname": "x-pipeline-postgres"}, None),
    )
    monkeypatch.setattr(watchdog, "supervisor_addon_stats", lambda slug: ({}, None))
    monkeypatch.setattr(
        watchdog, "run_probe",
        lambda probe, host, timeout, token=None: ProbeResult(False, "refused")
    )

    snapshot = watchdog.collect()
    row = next(r for r in snapshot["addons"] if r["slug"] == "pipeline-postgres")
    assert row["status"] == "degraded"
    assert row["probe_detail"] == "refused"
    assert snapshot["unhealthy"] == 1


def test_an_update_between_two_scans_reaches_the_row(monkeypatch, tmp_path):
    """End to end: the version Supervisor reports is what the restart count and
    the install age are keyed to, so a scan has to carry it into track_uptime."""
    monkeypatch.setattr(watchdog, "STATE_FILE", str(tmp_path / "state.json"))
    installed = {"slug": "x_detection_hub", "name": "Detection Hub",
                 "state": "started", "version": "1.9.0", "version_latest": "1.10.0"}
    monkeypatch.setattr(watchdog, "supervisor_addons", lambda: ([installed], None))
    monkeypatch.setattr(
        watchdog, "supervisor_addon_info",
        lambda slug: ({"state": "started", "hostname": "x-detection-hub"}, None),
    )
    monkeypatch.setattr(watchdog, "supervisor_addon_stats", lambda slug: ({}, None))
    monkeypatch.setattr(watchdog, "run_probe",
                        lambda *a, **k: ProbeResult(True, "HTTP 200"))
    monkeypatch.setattr(watchdog, "fetch_stats", lambda *a, **k: (None, None))

    def row(snapshot):
        return next(r for r in snapshot["addons"] if r["slug"] == "detection-hub")

    first = row(watchdog.collect(now=1000))
    assert first["version_known"] is False, "an install it never saw"

    # Down and back on the same version: a restart that belongs to 1.9.0.
    monkeypatch.setattr(watchdog, "supervisor_addon_info",
                        lambda slug: ({"state": "stopped", "hostname": "h"}, None))
    watchdog.collect(now=1060)
    monkeypatch.setattr(watchdog, "supervisor_addon_info",
                        lambda slug: ({"state": "started", "hostname": "h"}, None))
    assert row(watchdog.collect(now=1120))["restarts"] == 1

    installed["version"] = "1.10.0"
    updated = row(watchdog.collect(now=1180))
    assert updated["version"] == "1.10.0"
    assert updated["restarts"] == 0, "1.9.0's restart followed the update across"
    assert updated["version_seconds"] == 0
    assert updated["version_known"] is True


# --- record counts ------------------------------------------------------------


def test_stats_are_only_fetched_from_addons_that_publish_them():
    """Postgres holds plenty of rows and is deliberately not asked: counting
    them would mean the watchdog carrying database credentials."""
    assert watchdog.fetch_stats("pipeline-postgres", "host", 1) == (None, None)
    assert set(watchdog.STATS_PATHS) == {
        "gym-tracker", "coop-tracker", "detection-hub", "electricity-tracker", "knowledge"
    }


def test_stats_without_a_hostname_is_not_an_error():
    assert watchdog.fetch_stats("gym-tracker", None, 1) == (None, None)


def test_unreachable_stats_endpoint_yields_no_counts(monkeypatch):
    """A tracker older than the release that added /api/stats returns 404, and
    that is a missing number rather than a fault."""
    def boom(*args, **kwargs):
        raise OSError("404")

    monkeypatch.setattr(watchdog.urllib.request, "urlopen", boom)
    data, err = watchdog.fetch_stats("gym-tracker", "gym", 1)
    assert data is None and "unreachable" in err


def test_counts_reach_the_row(monkeypatch):
    monkeypatch.setattr(
        watchdog, "supervisor_addons",
        lambda: ([{"slug": "x_gym_tracker", "name": "Gym Tracker", "state": "started",
                   "version": "1.29.0", "version_latest": "1.29.0"}], None),
    )
    monkeypatch.setattr(
        watchdog, "supervisor_addon_info",
        lambda slug: ({"state": "started", "hostname": "x-gym-tracker"}, None),
    )
    monkeypatch.setattr(watchdog, "supervisor_addon_stats", lambda slug: ({}, None))
    monkeypatch.setattr(
        watchdog, "run_probe",
        lambda p, h, t, token=None: watchdog.ProbeResult(True, "HTTP 200")
    )
    monkeypatch.setattr(
        watchdog, "fetch_stats",
        lambda slug, host, timeout, token=None: (
            {"records": 42, "record_counts": {"weight_logs": 42}, "db_bytes": 4096}, None),
    )

    row = next(r for r in watchdog.collect()["addons"] if r["slug"] == "gym-tracker")
    assert row["records"] == 42
    assert row["record_counts"] == {"weight_logs": 42}


def test_a_degraded_addon_is_not_asked_for_counts(monkeypatch):
    """It will not answer, and waiting for a second timeout slows every scan."""
    asked = []
    monkeypatch.setattr(
        watchdog, "supervisor_addons",
        lambda: ([{"slug": "x_gym_tracker", "name": "Gym Tracker", "state": "started"}], None),
    )
    monkeypatch.setattr(
        watchdog, "supervisor_addon_info",
        lambda slug: ({"state": "started", "hostname": "x-gym-tracker"}, None),
    )
    monkeypatch.setattr(watchdog, "supervisor_addon_stats", lambda slug: ({}, None))
    monkeypatch.setattr(
        watchdog, "run_probe",
        lambda p, h, t, token=None: watchdog.ProbeResult(False, "refused")
    )
    monkeypatch.setattr(
        watchdog, "fetch_stats", lambda *a, **kw: (asked.append(a), (None, None))[1]
    )

    row = next(r for r in watchdog.collect()["addons"] if r["slug"] == "gym-tracker")
    assert row["status"] == "degraded"
    assert row["records"] is None
    assert asked == []


# --- API tokens ---------------------------------------------------------------


def test_token_is_sent_as_a_bearer_header():
    req = watchdog._request("http://gym:8099/api/stats", token="secret")
    assert req.get_header("Authorization") == "Bearer secret"


def test_no_token_means_no_header():
    assert watchdog._request("http://gym:8099/api/stats").get_header("Authorization") is None


def test_a_403_without_a_token_says_what_to_do(monkeypatch):
    """restrict_to_user_ids refuses any caller without a matching ingress-user
    header. The add-on is alive — the probe asked exactly that — but the empty
    Records column would otherwise be a mystery."""
    monkeypatch.setattr(
        watchdog, "supervisor_addons",
        lambda: ([{"slug": "x_gym_tracker", "name": "Gym Tracker", "state": "started"}], None),
    )
    monkeypatch.setattr(
        watchdog, "supervisor_addon_info",
        lambda slug: ({"state": "started", "hostname": "x-gym-tracker"}, None),
    )
    monkeypatch.setattr(watchdog, "supervisor_addon_stats", lambda slug: ({}, None))
    monkeypatch.setattr(watchdog, "run_probe", lambda *a: ProbeResult(True, "HTTP 403"))
    monkeypatch.setattr(watchdog, "fetch_stats", lambda *a: (None, "HTTP 403"))

    row = next(r for r in watchdog.collect()["addons"] if r["slug"] == "gym-tracker")
    assert row["status"] == "ok", "a 403 still means something is answering"
    assert "api_token" in row["probe_detail"]


def test_a_401_without_a_token_says_the_same_thing(monkeypatch):
    """From Gym Tracker 1.32.0 / Coop Tracker 1.44.0 the trackers refuse any
    request that did not come through ingress unless it carries their api_token,
    so the watchdog now meets 401 where it used to meet 200. Same situation, same
    fix, and the hint has to fire for it or the blank column is a mystery again."""
    monkeypatch.setattr(
        watchdog, "supervisor_addons",
        lambda: ([{"slug": "x_gym_tracker", "name": "Gym Tracker", "state": "started"}], None),
    )
    monkeypatch.setattr(
        watchdog, "supervisor_addon_info",
        lambda slug: ({"state": "started", "hostname": "x-gym-tracker"}, None),
    )
    monkeypatch.setattr(watchdog, "supervisor_addon_stats", lambda slug: ({}, None))
    monkeypatch.setattr(watchdog, "run_probe", lambda *a: ProbeResult(True, "HTTP 401"))
    monkeypatch.setattr(watchdog, "fetch_stats", lambda *a: (None, "HTTP 401"))

    row = next(r for r in watchdog.collect()["addons"] if r["slug"] == "gym-tracker")
    assert row["status"] == "ok", "a 401 still means something is answering"
    assert "api_token" in row["probe_detail"]


def test_the_hint_is_dropped_once_a_token_is_configured(monkeypatch):
    monkeypatch.setattr(
        watchdog, "supervisor_addons",
        lambda: ([{"slug": "x_gym_tracker", "name": "Gym Tracker", "state": "started"}], None),
    )
    monkeypatch.setattr(
        watchdog, "supervisor_addon_info",
        lambda slug: ({"state": "started", "hostname": "x-gym-tracker"}, None),
    )
    monkeypatch.setattr(watchdog, "supervisor_addon_stats", lambda slug: ({}, None))
    monkeypatch.setattr(watchdog, "run_probe", lambda *a: ProbeResult(True, "HTTP 200"))
    monkeypatch.setattr(
        watchdog, "fetch_stats",
        lambda *a: ({"records": 7, "record_counts": {"weight_logs": 7}, "db_bytes": 1024}, None),
    )

    row = next(
        r for r in watchdog.collect(tokens={"gym-tracker": "s"})["addons"]
        if r["slug"] == "gym-tracker"
    )
    assert row["probe_detail"] == "HTTP 200"
    assert row["records"] == 7


def test_tokens_reach_the_probe_and_the_stats_call(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        watchdog, "supervisor_addons",
        lambda: ([{"slug": "x_gym_tracker", "name": "Gym Tracker", "state": "started"}], None),
    )
    monkeypatch.setattr(
        watchdog, "supervisor_addon_info",
        lambda slug: ({"state": "started", "hostname": "x-gym-tracker"}, None),
    )
    monkeypatch.setattr(watchdog, "supervisor_addon_stats", lambda slug: ({}, None))
    monkeypatch.setattr(
        watchdog, "run_probe",
        lambda p, h, t, tok=None: (seen.__setitem__("probe", tok), ProbeResult(True, "HTTP 200"))[1],
    )
    monkeypatch.setattr(
        watchdog, "fetch_stats",
        lambda slug, host, timeout, tok=None: (seen.__setitem__("stats", tok), (None, None))[1],
    )

    watchdog.collect(tokens={"gym-tracker": "tok123"})
    assert seen["probe"] == "tok123"
    assert seen["stats"] == "tok123"


def test_records_prefers_the_all_tables_total(monkeypatch):
    """total_all covers the file; total is the tracked subset."""
    class _Resp:
        status = 200
        def read(self, *a): return json.dumps({
            "counts": {"logs": 75}, "other_counts": {"egg_vision_samples": 120},
            "total": 75, "other_total": 120, "total_all": 195, "db_bytes": 11010048,
        }).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(watchdog.urllib.request, "urlopen", lambda *a, **kw: _Resp())
    data, err = watchdog.fetch_stats("coop-tracker", "coop", 1)
    assert err is None
    assert data["records"] == 195, "the headline number covers every table"
    assert data["other_counts"] == {"egg_vision_samples": 120}


def test_records_falls_back_for_a_tracker_without_the_split(monkeypatch):
    """A tracker on the previous release reports only `total`; showing its
    number beats showing nothing."""
    class _Resp:
        status = 200
        def read(self, *a): return json.dumps({"counts": {"logs": 75}, "total": 75}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(watchdog.urllib.request, "urlopen", lambda *a, **kw: _Resp())
    data, _ = watchdog.fetch_stats("coop-tracker", "coop", 1)
    assert data["records"] == 75
    assert data["other_counts"] == {}


# --- self-reports (backup freshness, replication lag) -------------------------


def _write_report(tmp_path, slug, **body):
    body.setdefault("updated_at", time.time())
    (tmp_path / f"{slug}.json").write_text(json.dumps(body))
    return tmp_path


def test_a_healthy_report_is_read(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "STATUS_DIR", str(tmp_path))
    _write_report(tmp_path, "pipeline-postgres", ok=True, detail="full backup complete",
                  metrics={"archiving_ok": True})
    report, err = watchdog.read_report("pipeline-postgres")
    assert err is None and report["ok"] is True
    assert report["detail"] == "full backup complete"
    assert report["metrics"] == {"archiving_ok": True}


def test_no_report_is_not_an_error(tmp_path, monkeypatch):
    """Most add-ons write none; absence must not look like a fault."""
    monkeypatch.setattr(watchdog, "STATUS_DIR", str(tmp_path))
    assert watchdog.read_report("pipeline-minio") == (None, None)


def test_a_stale_report_is_not_ok(tmp_path, monkeypatch):
    """The interesting failure is the writer having stopped — backups that quietly
    stopped running a month ago still leave a file saying they succeeded."""
    monkeypatch.setattr(watchdog, "STATUS_DIR", str(tmp_path))
    _write_report(tmp_path, "pipeline-postgres", ok=True, detail="full backup complete",
                  updated_at=time.time() - 90000)
    report, _ = watchdog.read_report("pipeline-postgres")
    assert report["ok"] is False
    assert "min ago" in report["detail"]


def test_a_corrupt_report_is_an_error_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "STATUS_DIR", str(tmp_path))
    (tmp_path / "pipeline-postgres.json").write_text("{not json")
    report, err = watchdog.read_report("pipeline-postgres")
    assert report is None and "unreadable" in err


def test_a_report_without_a_timestamp_is_rejected(tmp_path, monkeypatch):
    """Without updated_at there is no way to tell fresh from ancient, and
    trusting it would be worse than ignoring it."""
    monkeypatch.setattr(watchdog, "STATUS_DIR", str(tmp_path))
    (tmp_path / "pipeline-postgres.json").write_text(json.dumps({"ok": True}))
    report, err = watchdog.read_report("pipeline-postgres")
    assert report is None and "updated_at" in err


def test_a_failing_report_degrades_an_addon_whose_port_answers():
    """Postgres with a broken backup answers its port perfectly. The service is
    fine and the job it was given is not, and that should still be alertable."""
    ok_probe = ProbeResult(True, "port 5432 open")
    bad = {"ok": False, "detail": "backup failed", "metrics": {}, "age": 10}
    assert derive_status("started", ok_probe, bad) == "degraded"
    assert is_unhealthy("degraded") is True


def test_a_healthy_report_does_not_override_a_failing_probe():
    bad_probe = ProbeResult(False, "refused")
    good = {"ok": True, "detail": "streaming, 0s behind", "metrics": {}, "age": 5}
    assert derive_status("started", bad_probe, good) == "degraded"


def test_a_stopped_addon_is_not_judged_on_a_stale_report():
    """It is stopped on purpose; its last report is necessarily old."""
    stale = {"ok": False, "detail": "last reported 400 min ago", "metrics": {}, "age": 24000}
    assert derive_status("stopped", None, stale) == "stopped"


# --- uptime and restarts ------------------------------------------------------


def test_uptime_is_a_lower_bound_until_a_restart_is_seen():
    """Supervisor exposes no container start time, so the first observation of
    an already-running add-on cannot know when it really started."""
    state, now = {}, 1000
    first = watchdog.track_uptime(state, "gym-tracker", True, 100, now)
    assert first["uptime_seconds"] == 0
    assert first["uptime_known"] is False, "claimed to know a start it never saw"

    later = watchdog.track_uptime(state, "gym-tracker", True, 200, now + 600)
    assert later["uptime_seconds"] == 600
    assert later["uptime_known"] is False, "still the same unobserved start"


def test_a_restart_makes_the_clock_exact():
    state, now = {}, 1000
    watchdog.track_uptime(state, "gym-tracker", True, 100, now)
    watchdog.track_uptime(state, "gym-tracker", False, None, now + 60)
    back = watchdog.track_uptime(state, "gym-tracker", True, 0, now + 120)

    assert back["restarts"] == 1
    assert back["last_restart_at"] == now + 120
    assert back["uptime_seconds"] == 0
    assert back["uptime_known"] is True, "this start was actually observed"


def test_first_sighting_is_not_a_restart():
    """An add-on that was already running when the watchdog started has not
    restarted; counting it would put a phantom restart on every install."""
    state = {}
    assert watchdog.track_uptime(state, "gym-tracker", True, 5, 1000)["restarts"] == 0


def test_a_restart_between_two_scans_is_caught_by_the_counter_reset():
    """Up at both ends, so the state transition is invisible — but the
    container's cumulative network counter went backwards, which only happens
    when it was replaced."""
    state, now = {}, 1000
    watchdog.track_uptime(state, "pipeline-spark", True, 5_000_000, now)
    after = watchdog.track_uptime(state, "pipeline-spark", True, 12_000, now + 60)
    assert after["restarts"] == 1
    assert after["uptime_seconds"] == 0


def test_a_growing_counter_is_not_a_restart():
    state, now = {}, 1000
    watchdog.track_uptime(state, "pipeline-spark", True, 1000, now)
    assert watchdog.track_uptime(state, "pipeline-spark", True, 2000, now + 60)["restarts"] == 0


def test_a_stopped_addon_has_no_uptime():
    state = {}
    watchdog.track_uptime(state, "pipeline-spark", True, 10, 1000)
    stopped = watchdog.track_uptime(state, "pipeline-spark", False, None, 1100)
    assert stopped["uptime_seconds"] is None


def test_a_new_restart_does_not_inherit_the_previous_reason():
    state, now = {}, 1000
    watchdog.track_uptime(state, "gym-tracker", True, 10, now)
    watchdog.track_uptime(state, "gym-tracker", False, None, now + 10)
    watchdog.track_uptime(state, "gym-tracker", True, 0, now + 20)
    state["gym-tracker"]["last_restart_reason"] = "exited with non-zero exit code 137"

    watchdog.track_uptime(state, "gym-tracker", False, None, now + 30)
    again = watchdog.track_uptime(state, "gym-tracker", True, 0, now + 40)
    assert again["restarts"] == 2
    assert again["last_restart_reason"] is None, "stale explanation carried forward"


# --- restarts belong to a version ---------------------------------------------


def test_an_update_resets_the_restart_count():
    """The question the count answers is "is this build stable", so restarts
    accumulated by the previous one are noise against the new one."""
    state, now = {}, 1000
    watchdog.track_uptime(state, "detection-hub", True, 10, now, version="1.9.0")
    watchdog.track_uptime(state, "detection-hub", False, None, now + 60)
    on_old = watchdog.track_uptime(state, "detection-hub", True, 0, now + 120, version="1.9.0")
    assert on_old["restarts"] == 1

    updated = watchdog.track_uptime(state, "detection-hub", True, 0, now + 180, version="1.10.0")
    assert updated["restarts"] == 0, "carried the old build's restarts forward"
    assert updated["version_at"] == now + 180
    assert updated["version_known"] is True, "this update was actually observed"


def test_the_updates_own_restart_is_not_counted():
    """Supervisor restarts the add-on to install it. That is the install, not a
    fault of the version being installed."""
    state, now = {}, 1000
    watchdog.track_uptime(state, "gym-tracker", True, 5_000_000, now, version="1.32.2")
    after = watchdog.track_uptime(state, "gym-tracker", True, 900, now + 60, version="1.32.3")
    assert after["restarts"] == 0
    assert after["last_restart_at"] is None
    assert after["uptime_seconds"] == 0, "the update restarted it, so the clock starts again"


def test_an_update_restarts_the_clock_even_when_the_counters_hide_it():
    """rx resets with the container, but a busy minute can leave the new counter
    above the old one — the version moving is the proof the drop would have been."""
    state, now = {}, 1000
    watchdog.track_uptime(state, "gym-tracker", True, 100, now, version="1.32.2")
    watchdog.track_uptime(state, "gym-tracker", True, 200, now + 60, version="1.32.2")
    updated = watchdog.track_uptime(state, "gym-tracker", True, 9_999, now + 120,
                                    version="1.32.3")
    assert updated["uptime_seconds"] == 0
    assert updated["uptime_known"] is True


def test_the_first_version_ever_recorded_is_not_an_update():
    """A state file written before versions were tracked, or a fresh install:
    either way nothing restarted, and how long that version has been there is
    older than anything observable."""
    state, now = {}, 1000
    first = watchdog.track_uptime(state, "gym-tracker", True, 10, now, version="1.32.3")
    assert first["restarts"] == 0
    assert first["version_known"] is False, "claimed to have seen an install it did not"
    assert first["version_seconds"] == 0

    later = watchdog.track_uptime(state, "gym-tracker", True, 20, now + 3600, version="1.32.3")
    assert later["version_seconds"] == 3600
    assert later["version_known"] is False, "still the same unobserved install"


def test_a_reinstalled_version_still_counts_its_restarts():
    """Only a *change* resets. Scanning the same version repeatedly must not keep
    zeroing the count, or no restart would ever be visible."""
    state, now = {}, 1000
    watchdog.track_uptime(state, "coop-tracker", True, 10, now, version="1.44.2")
    watchdog.track_uptime(state, "coop-tracker", False, None, now + 60, version="1.44.2")
    back = watchdog.track_uptime(state, "coop-tracker", True, 0, now + 120, version="1.44.2")
    assert back["restarts"] == 1
    assert back["version_at"] == now, "the install time moved on an unchanged version"


def test_an_addon_that_reports_no_version_is_not_treated_as_updated():
    state, now = {}, 1000
    watchdog.track_uptime(state, "pipeline-spark", True, 10, now, version="1.0.2")
    watchdog.track_uptime(state, "pipeline-spark", False, None, now + 60)
    same = watchdog.track_uptime(state, "pipeline-spark", True, 0, now + 120, version=None)
    assert same["restarts"] == 1, "a missing version wiped the count"
    assert same["version_at"] == now


def test_version_age_is_absent_before_any_version_is_seen():
    """Nothing here may invent a date: a row with no recorded version shows no
    age rather than "0s ago"."""
    state = {}
    row = watchdog.track_uptime(state, "pipeline-notebook", True, 10, 1000)
    assert row["version_at"] is None
    assert row["version_seconds"] is None
    assert row["version_known"] is False


def test_state_survives_a_round_trip(tmp_path, monkeypatch):
    """Uptime is measured from observations, so losing this file would make
    every add-on look freshly started after the watchdog itself restarts."""
    monkeypatch.setattr(watchdog, "STATE_FILE", str(tmp_path / "state.json"))
    state = {}
    watchdog.track_uptime(state, "gym-tracker", True, 10, 1000)
    watchdog.save_state(state)
    assert watchdog.load_state() == state


def test_unreadable_state_is_an_empty_history_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "STATE_FILE", str(tmp_path / "state.json"))
    (tmp_path / "state.json").write_text("{not json")
    assert watchdog.load_state() == {}


# --- restart reasons ----------------------------------------------------------


def test_reasons_are_matched_by_display_name(monkeypatch):
    """Supervisor names add-ons by display name in its log, not by slug. This is
    a real line from a Supervisor log."""
    log = (
        "2026-08-08 10:47:24.944 ERROR (MainThread) [supervisor.apps.app] "
        "App Add-on Watchdog exited with non-zero exit code 137\n"
        "2026-08-08 10:47:25.008 INFO (MainThread) [supervisor.docker.manager] Cleanup images\n"
    )
    monkeypatch.setattr(watchdog, "_api", lambda *a, **kw: (log, None))
    reasons, err = watchdog.supervisor_restart_reasons({"addon-watchdog": "Add-on Watchdog"})
    assert err is None
    assert "exit code 137" in reasons["addon-watchdog"]


def test_the_most_recent_explanation_wins(monkeypatch):
    log = (
        "old line: App Gym Tracker exited with non-zero exit code 1\n"
        "new line: App Gym Tracker exited with non-zero exit code 137\n"
    )
    monkeypatch.setattr(watchdog, "_api", lambda *a, **kw: (log, None))
    reasons, _ = watchdog.supervisor_restart_reasons({"gym-tracker": "Gym Tracker"})
    assert "137" in reasons["gym-tracker"]


def test_a_refused_log_endpoint_costs_the_reason_not_the_restart(monkeypatch):
    """The role may not be allowed to read Supervisor's log. Restarts are still
    counted; only the explanation is missing, and the error says so."""
    monkeypatch.setattr(watchdog, "_api", lambda *a, **kw: (None, "HTTP 403"))
    reasons, err = watchdog.supervisor_restart_reasons({"gym-tracker": "Gym Tracker"})
    assert reasons == {} and err == "HTTP 403"


def test_unrelated_log_lines_are_ignored(monkeypatch):
    monkeypatch.setattr(
        watchdog, "_api",
        lambda *a, **kw: ("INFO [supervisor.store.git] Update app repository\n", None),
    )
    reasons, _ = watchdog.supervisor_restart_reasons({"gym-tracker": "Gym Tracker"})
    assert reasons == {}
