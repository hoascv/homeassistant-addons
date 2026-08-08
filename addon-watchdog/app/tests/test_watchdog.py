"""The logic the dashboard and the sensors both rest on.

No network: probes take a host and a port, so a closed port on localhost is a
perfectly good failure case, and the Supervisor calls are stubbed. What is
worth pinning down is the mapping from (Supervisor state, probe outcome) to a
single word, because that word becomes a sensor state people write automations
against.
"""
import socket

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


def test_probe_table_covers_every_known_addon():
    """A new add-on added to PROBES without a decision about how to check it
    would silently report as `running` forever."""
    assert set(watchdog.PROBES) == set(watchdog.KNOWN_SLUGS)


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


# --- record counts ------------------------------------------------------------


def test_stats_are_only_fetched_from_addons_that_publish_them():
    """Postgres holds plenty of rows and is deliberately not asked: counting
    them would mean the watchdog carrying database credentials."""
    assert watchdog.fetch_stats("pipeline-postgres", "host", 1) is None
    assert set(watchdog.STATS_PATHS) == {"gym-tracker", "coop-tracker"}


def test_stats_without_a_hostname_is_not_an_error():
    assert watchdog.fetch_stats("gym-tracker", None, 1) is None


def test_unreachable_stats_endpoint_yields_no_counts(monkeypatch):
    """A tracker older than the release that added /api/stats returns 404, and
    that is a missing number rather than a fault."""
    def boom(*args, **kwargs):
        raise OSError("404")

    monkeypatch.setattr(watchdog.urllib.request, "urlopen", boom)
    assert watchdog.fetch_stats("gym-tracker", "gym", 1) is None


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
        lambda slug, host, timeout, token=None: {
            "records": 42, "record_counts": {"weight_logs": 42}, "db_bytes": 4096},
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
        watchdog, "fetch_stats", lambda *a, **kw: asked.append(a) or None
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
    monkeypatch.setattr(watchdog, "fetch_stats", lambda *a: None)

    row = next(r for r in watchdog.collect()["addons"] if r["slug"] == "gym-tracker")
    assert row["status"] == "ok", "a 403 still means something is answering"
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
        lambda *a: {"records": 7, "record_counts": {"weight_logs": 7}, "db_bytes": 1024},
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
        lambda slug, host, timeout, tok=None: (seen.__setitem__("stats", tok), None)[1],
    )

    watchdog.collect(tokens={"gym-tracker": "tok123"})
    assert seen["probe"] == "tok123"
    assert seen["stats"] == "tok123"
