"""The log line, which is the only evidence a scan happened at all.

Before 1.1.0 the scan loop logged nothing unless something was wrong, so a
healthy watchdog and a wedged one produced identical output: three startup
lines and then silence. That is what these cover.
"""
import time

import app as wd_app
import watchdog


def _snapshot(statuses, updates=0):
    rows = [
        watchdog._row(slug=f"addon-{i}", name=f"addon {i}", installed=True, status=status)
        for i, status in enumerate(statuses)
    ]
    return {"addons": rows, "updates": updates, "unhealthy": statuses.count("degraded")}


def test_every_status_is_counted():
    line = wd_app.summarise(_snapshot(["ok", "ok", "stopped"]), 1.0)
    assert "2 ok" in line and "1 stopped" in line


def test_degraded_addons_are_named_not_just_counted():
    """A count alone sends you to the dashboard; the name is usually enough to
    know what broke."""
    snap = _snapshot(["ok", "degraded"])
    snap["addons"][1]["slug"] = "pipeline-metastore"
    line = wd_app.summarise(snap, 1.0)
    assert "degraded: pipeline-metastore" in line


def test_quiet_when_nothing_is_wrong():
    line = wd_app.summarise(_snapshot(["ok", "ok"]), 2.5)
    assert "degraded" not in line
    assert "update" not in line


def test_duration_is_reported():
    """A scan costs a Supervisor stats call per add-on; the duration is what
    tells you scans are about to overlap the interval."""
    assert "12.3s" in wd_app.summarise(_snapshot(["ok"]), 12.34)


def test_updates_are_mentioned_only_when_present():
    assert "1 update(s)" in wd_app.summarise(_snapshot(["ok"], updates=1), 1.0)
    assert "update" not in wd_app.summarise(_snapshot(["ok"], updates=0), 1.0)


def test_sensor_count_is_optional():
    """Sensors can be switched off, and the line should not then claim zero."""
    assert "sensors" not in wd_app.summarise(_snapshot(["ok"]), 1.0)
    assert "4 sensors" in wd_app.summarise(_snapshot(["ok"]), 1.0, pushed=4)


def test_log_lines_carry_a_parsable_local_timestamp(capsys):
    wd_app._log("hello")
    line = capsys.readouterr().out.strip()
    stamp = " ".join(line.split()[:2])
    # Parses, and is actually now rather than a fixed string that merely looks
    # like a date.
    parsed = time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))
    assert abs(parsed - time.time()) < 120
    assert line.endswith("[Add-on Watchdog] hello")


# --- retrieval errors ---------------------------------------------------------


def _row(slug, **kw):
    return watchdog._row(slug=slug, name=slug, installed=True, status="ok", **kw)


def test_a_retrieval_error_is_logged_once_not_every_scan(capsys):
    """A scan a minute means an unchanging 403 logged unconditionally would bury
    everything else within a day."""
    wd_app._reported_errors.clear()
    snap = {"addons": [_row("gym-tracker", records_error="HTTP 403")], "updates": 0}

    assert wd_app.log_errors(snap) == 1
    first = capsys.readouterr().out
    assert "could not retrieve gym-tracker record counts: HTTP 403" in first

    wd_app.log_errors(snap)
    assert capsys.readouterr().out == "", "same error, second scan: silent"


def test_a_changed_reason_is_logged_again(capsys):
    wd_app._reported_errors.clear()
    wd_app.log_errors({"addons": [_row("gym-tracker", records_error="HTTP 403")]})
    capsys.readouterr()
    wd_app.log_errors({"addons": [_row("gym-tracker", records_error="HTTP 404")]})
    assert "HTTP 404" in capsys.readouterr().out


def test_recovery_is_logged(capsys):
    """Otherwise the log's last word on the subject is a failure that has since
    cleared."""
    wd_app._reported_errors.clear()
    wd_app.log_errors({"addons": [_row("gym-tracker", records_error="HTTP 403")]})
    capsys.readouterr()
    wd_app.log_errors({"addons": [_row("gym-tracker")]})
    assert "recovered: gym-tracker record counts" in capsys.readouterr().out


def test_each_source_is_reported_separately(capsys):
    wd_app._reported_errors.clear()
    snap = {"addons": [_row("gym-tracker", error="HTTP 500", stats_error="timeout",
                            records_error="HTTP 403")]}
    assert wd_app.log_errors(snap) == 3
    out = capsys.readouterr().out
    assert "supervisor info" in out and "supervisor stats" in out and "record counts" in out


def test_sensor_push_failures_are_reported(capsys):
    wd_app._reported_errors.clear()
    assert wd_app.log_errors({"addons": []}, ["sensor.x: HTTP 401"]) == 1
    assert "sensor.x" in capsys.readouterr().out


def test_a_supervisor_wide_failure_is_reported(capsys):
    wd_app._reported_errors.clear()
    assert wd_app.log_errors({"addons": [], "error": "HTTP 403"}) == 1
    assert "could not retrieve supervisor: HTTP 403" in capsys.readouterr().out


def test_the_scan_line_keeps_carrying_the_failure_count():
    """The detail is logged on change only, so without this a persistent
    problem would vanish from the log after its first appearance."""
    snap = _snapshot(["ok"])
    assert "2 retrieval error(s)" in wd_app.summarise(snap, 1.0, failures=2)
    assert "retrieval error" not in wd_app.summarise(snap, 1.0, failures=0)


# --- uptime formatting --------------------------------------------------------


def test_uptime_reads_in_the_largest_sensible_unit():
    """A status page wants "3d", not 272800 seconds or "4546m"."""
    assert wd_app.uptime(45) == "45s"
    assert wd_app.uptime(1800) == "30m"
    assert wd_app.uptime(3900) == "1h", "an hour should not read as 65m"
    assert wd_app.uptime(272800) == "3d"


def test_no_uptime_is_a_dash_not_a_zero():
    """A stopped add-on has no uptime; showing 0s would imply it just started."""
    assert wd_app.uptime(None) == "—"
