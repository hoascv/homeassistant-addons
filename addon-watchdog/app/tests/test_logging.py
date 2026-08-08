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
