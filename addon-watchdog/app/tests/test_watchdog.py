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
        watchdog, "run_probe", lambda probe, host, timeout: ProbeResult(False, "refused")
    )

    snapshot = watchdog.collect()
    row = next(r for r in snapshot["addons"] if r["slug"] == "pipeline-postgres")
    assert row["status"] == "degraded"
    assert row["probe_detail"] == "refused"
    assert snapshot["unhealthy"] == 1
