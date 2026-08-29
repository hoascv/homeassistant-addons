"""The service around the scan: options, one scan pass, the I/O sensors and the
JSON endpoints.

test_logging.py covers what a scan *says*; this covers what it *does* — that the
snapshot is replaced, that sensors are pushed once per scan and not once per page
load, and that the endpoints answer from the last scan rather than triggering a
new one. The scan loop is the part with no natural test seam, so it is driven
here through a fake clock and a sleep that raises on the second call: a real
`time.sleep` would either hang the suite or test nothing.
"""
import json
import threading

import pytest

import app as wd_app
import watchdog


@pytest.fixture(autouse=True)
def _restore_module_state():
    """Every one of these globals is written by the code under test, and pytest
    shares a module across the session — without this a scan test would leak its
    snapshot into an endpoint test and both would still pass, for the wrong
    reason."""
    saved = (
        wd_app._snapshot,
        wd_app._last_publish,
        wd_app._io,
        wd_app._sampler,
        wd_app._benchmark_running,
        dict(wd_app._reported_errors),
    )
    yield
    (
        wd_app._snapshot,
        wd_app._last_publish,
        wd_app._io,
        wd_app._sampler,
        wd_app._benchmark_running,
        _errors,
    ) = saved
    wd_app._reported_errors.clear()
    wd_app._reported_errors.update(_errors)


def _options(**overrides):
    base = {
        "scan_interval_seconds": 60,
        "probe_timeout_seconds": 5,
        "publish_sensors": False,
        "sensor_prefix": "addon_watchdog",
        "ignore_stopped": True,
        "tokens": {},
        "io_sample_seconds": 10,
        "benchmark_size_mb": 8,
        "benchmark_seconds": 1,
        "benchmark_min_free_gb": 1,
    }
    base.update(overrides)
    return base


def _snapshot(**overrides):
    base = {
        "generated": 1_700_000_000.0,
        "error": None,
        "unhealthy": 0,
        "updates": 0,
        "addons": [watchdog._row(slug="journal", name="Journal", installed=True, status="ok")],
    }
    base.update(overrides)
    return base


# --- options ------------------------------------------------------------------


def test_options_fall_back_to_defaults_when_the_file_is_missing(monkeypatch, capsys):
    """First boot, and every dev run outside Supervisor. Defaults rather than a
    crash, because a watchdog that will not start reports nothing at all."""
    monkeypatch.setattr(wd_app, "OPTIONS_PATH", "/nonexistent/options.json")
    options = wd_app.load_options()
    assert options["scan_interval_seconds"] == 60
    assert options["tokens"] == {}
    assert "using defaults" in capsys.readouterr().out


def test_malformed_options_do_not_stop_the_add_on(monkeypatch, tmp_path, capsys):
    bad = tmp_path / "options.json"
    bad.write_text("{not json")
    monkeypatch.setattr(wd_app, "OPTIONS_PATH", str(bad))
    assert wd_app.load_options()["publish_sensors"] is True
    assert "using defaults" in capsys.readouterr().out


def test_api_tokens_are_flattened_to_a_lookup(monkeypatch, tmp_path):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({
        "api_tokens": [
            {"slug": "gym-tracker", "token": "abc"},
            {"slug": "coop-tracker", "token": "def"},
        ]
    }))
    monkeypatch.setattr(wd_app, "OPTIONS_PATH", str(path))
    assert wd_app.load_options()["tokens"] == {"gym-tracker": "abc", "coop-tracker": "def"}


def test_a_half_filled_token_row_is_dropped_quietly(monkeypatch, tmp_path, capsys):
    """Typing a slug before the token is the normal in-progress state of the
    config UI. Logging about it every minute would be noise, not news."""
    path = tmp_path / "options.json"
    path.write_text(json.dumps({
        "api_tokens": [
            {"slug": "gym-tracker"},
            {"token": "orphan"},
            "not-a-dict",
            {"slug": "journal", "token": "keep"},
        ]
    }))
    monkeypatch.setattr(wd_app, "OPTIONS_PATH", str(path))
    assert wd_app.load_options()["tokens"] == {"journal": "keep"}
    assert capsys.readouterr().out == ""


# --- one scan pass ------------------------------------------------------------


def test_a_scan_replaces_the_snapshot_the_page_reads(monkeypatch):
    fresh = _snapshot(unhealthy=2)
    monkeypatch.setattr(watchdog, "collect", lambda **_: fresh)
    wd_app.scan_once(_options())
    assert wd_app._snapshot is fresh


def test_a_scan_passes_the_configured_timeout_and_tokens_through(monkeypatch):
    """These are the two options that only matter if they reach collect(); a
    probe timeout that never leaves load_options is a silent no-op."""
    seen = {}

    def fake_collect(**kwargs):
        seen.update(kwargs)
        return _snapshot()

    monkeypatch.setattr(watchdog, "collect", fake_collect)
    wd_app.scan_once(_options(probe_timeout_seconds=17, tokens={"journal": "t"},
                              ignore_stopped=False))
    assert seen == {"timeout": 17, "ignore_stopped": False, "tokens": {"journal": "t"}}


def test_a_supervisor_failure_skips_publishing_entirely(monkeypatch):
    """Pushing sensors from a snapshot with no add-ons in it would blank every
    entity — the dashboard should go stale, not report everything as gone."""
    monkeypatch.setattr(watchdog, "collect", lambda **_: {
        "generated": 1.0, "error": "supervisor unreachable", "addons": [], "unhealthy": 0,
    })
    monkeypatch.setattr(watchdog, "publish", lambda *a, **k: pytest.fail("published anyway"))
    result = wd_app.scan_once(_options(publish_sensors=True))
    assert result["error"] == "supervisor unreachable"


def test_a_scan_publishes_sensors_and_records_the_result(monkeypatch):
    monkeypatch.setattr(watchdog, "collect", lambda **_: _snapshot())
    monkeypatch.setattr(watchdog, "publish", lambda *a, **k: (3, ["sensor.x: boom"]))
    monkeypatch.setattr(wd_app, "publish_io", lambda prefix: (1, []))
    wd_app.scan_once(_options(publish_sensors=True))
    assert wd_app._last_publish["pushed"] == 4  # 3 add-on sensors + 1 device sensor
    assert wd_app._last_publish["errors"] == ["sensor.x: boom"]
    assert wd_app._last_publish["at"] is not None


def test_the_io_window_is_taken_with_the_scan(monkeypatch):
    """So the dashboard, the sensors and the log line all describe the same
    interval rather than three interleaved ones."""
    class _Sampler:
        def summary(self):
            return {"device": "sda", "samples": 4, "util_percent_peak": 88}

    monkeypatch.setattr(watchdog, "collect", lambda **_: _snapshot())
    monkeypatch.setattr(wd_app, "_sampler", _Sampler())
    wd_app.scan_once(_options())
    assert wd_app._io["summary"]["util_percent_peak"] == 88


# --- the scan loop ------------------------------------------------------------


class _StopLoop(Exception):
    pass


def _run_loop_once(monkeypatch, elapsed, interval):
    """Drive `scanner` for exactly one iteration and return what it slept."""
    slept = []
    ticks = iter([0.0, elapsed])
    monkeypatch.setattr(wd_app.time, "monotonic", lambda: next(ticks))

    def fake_sleep(seconds):
        slept.append(seconds)
        raise _StopLoop

    monkeypatch.setattr(wd_app.time, "sleep", fake_sleep)
    with pytest.raises(_StopLoop):
        wd_app.scanner(_options(scan_interval_seconds=interval))
    return slept[0]


def test_the_loop_sleeps_the_remainder_not_the_whole_interval(monkeypatch):
    """A scan costs about a second per add-on. Sleeping the full interval on top
    would make a 60s setting mean 72s, and drift further with every add-on."""
    monkeypatch.setattr(wd_app, "scan_once", lambda options: _snapshot())
    assert _run_loop_once(monkeypatch, elapsed=12.0, interval=60) == 48.0


def test_an_overrunning_scan_still_sleeps_and_says_so(monkeypatch, capsys):
    """Busy-looping on a slow Supervisor would make the problem worse."""
    monkeypatch.setattr(wd_app, "scan_once", lambda options: _snapshot())
    assert _run_loop_once(monkeypatch, elapsed=75.0, interval=60) == 1.0
    assert "longer than the 60s interval" in capsys.readouterr().out


def test_the_loop_outlives_a_scan_that_raises(monkeypatch, capsys):
    """The whole add-on is this loop; one bad scan must not end it."""
    def boom(options):
        raise RuntimeError("supervisor exploded")

    monkeypatch.setattr(wd_app, "scan_once", boom)
    assert _run_loop_once(monkeypatch, elapsed=1.0, interval=60) == 59.0
    assert "scan raised RuntimeError: supervisor exploded" in capsys.readouterr().out


# --- the device sensors -------------------------------------------------------


def _summary(**overrides):
    base = {
        "device": "sda8", "samples": 6,
        "util_percent": 40.0, "util_percent_peak": 91.0,
        "iops": 120.0, "iops_peak": 400.0,
        "read_latency_ms": 1.0, "read_latency_ms_peak": 9.0,
        "write_latency_ms": 2.0, "write_latency_ms_peak": 30.0,
    }
    base.update(overrides)
    return base


def test_nothing_is_pushed_before_the_first_sample(monkeypatch):
    monkeypatch.setattr(wd_app, "_io", {"summary": None})
    assert wd_app.publish_io("addon_watchdog") == (0, [])


def test_every_device_sensor_is_pushed_with_a_measurement_state_class(monkeypatch):
    """state_class is what makes the recorder keep long-term statistics — without
    it the history this add-on exists to leave behind is only recent states."""
    pushed = {}

    def fake_push(entity, state, attrs):
        pushed[entity] = (state, attrs)
        return None, None

    monkeypatch.setattr(wd_app, "_io", {"summary": _summary(), "benchmark": None})
    monkeypatch.setattr(watchdog, "push_sensor", fake_push)
    count, errors = wd_app.publish_io("addon_watchdog")

    assert (count, errors) == (4, [])
    assert set(pushed) == {
        "sensor.addon_watchdog_disk_util",
        "sensor.addon_watchdog_disk_iops",
        "sensor.addon_watchdog_disk_read_latency_ms",
        "sensor.addon_watchdog_disk_write_latency_ms",
    }
    state, attrs = pushed["sensor.addon_watchdog_disk_util"]
    assert state == "91.0"
    assert attrs["state_class"] == "measurement"
    assert attrs["unit_of_measurement"] == "%"
    assert attrs["device"] == "sda8"
    assert attrs["mean_percent"] == 40.0


def test_a_metric_the_kernel_did_not_report_is_skipped_not_pushed_as_none(monkeypatch):
    """An entity whose state is the string "None" is worse than no entity: it
    graphs, and it graphs a lie."""
    monkeypatch.setattr(wd_app, "_io", {"summary": _summary(iops_peak=None), "benchmark": None})
    monkeypatch.setattr(watchdog, "push_sensor", lambda *a: (None, None))
    count, _ = wd_app.publish_io("addon_watchdog")
    assert count == 3


def test_a_failed_push_is_reported_against_its_entity(monkeypatch):
    monkeypatch.setattr(wd_app, "_io", {"summary": _summary(), "benchmark": None})
    monkeypatch.setattr(watchdog, "push_sensor", lambda *a: (None, "401 unauthorized"))
    count, errors = wd_app.publish_io("addon_watchdog")
    assert count == 0
    assert all(err.startswith("sensor.addon_watchdog_disk_") for err in errors)
    assert all("401 unauthorized" in err for err in errors)


def test_the_sensor_prefix_is_honoured(monkeypatch):
    """It is an option, so it has to reach the entity id — two watchdogs on one
    Home Assistant would otherwise fight over the same entities."""
    pushed = []
    monkeypatch.setattr(wd_app, "_io", {"summary": _summary(), "benchmark": None})
    monkeypatch.setattr(watchdog, "push_sensor",
                        lambda entity, *a: (pushed.append(entity), (None, None))[1])
    wd_app.publish_io("spare_host")
    assert all(entity.startswith("sensor.spare_host_") for entity in pushed)


# --- the endpoints ------------------------------------------------------------


def test_status_answers_from_the_last_scan_without_starting_one(monkeypatch):
    """A page load that fanned out to every add-on would take as long as the
    slowest probe, which is why the scan is on a timer in the first place."""
    monkeypatch.setattr(watchdog, "collect", lambda **_: pytest.fail("scanned on request"))
    wd_app._snapshot = _snapshot(unhealthy=1)
    body = wd_app.app.test_client().get("/api/status").get_json()
    assert body["unhealthy"] == 1
    assert body["addons"][0]["slug"] == "journal"


def test_health_is_ok_once_a_scan_has_landed():
    wd_app._snapshot = _snapshot()
    wd_app._last_publish = {"pushed": 9, "errors": [], "at": 1.0}
    body = wd_app.app.test_client().get("/api/health").get_json()
    assert body["ok"] is True
    assert body["sensors_pushed"] == 9
    assert body["age_seconds"] >= 0


def test_health_is_not_ok_before_the_first_scan():
    """This is the endpoint a sibling watchdog would probe, so "never scanned"
    has to read as not-ok rather than as a missing field."""
    wd_app._snapshot = {"generated": None, "addons": [], "unhealthy": 0, "error": "no scan yet"}
    body = wd_app.app.test_client().get("/api/health").get_json()
    assert body["ok"] is False
    assert body["last_scan"] is None
    assert body["age_seconds"] is None


def test_health_is_not_ok_while_supervisor_is_unreachable():
    """A scan that ran but retrieved nothing is not health, even though it is
    recent — reporting ok here is exactly the false negative this add-on exists
    to avoid making about others."""
    wd_app._snapshot = _snapshot(error="supervisor unreachable")
    assert wd_app.app.test_client().get("/api/health").get_json()["ok"] is False


def test_io_reports_the_sample_the_benchmark_and_the_saturation():
    wd_app._io = {
        "summary": _summary(), "benchmark": {"randread": {"iops": 5000}},
        "saturation": 22.5, "error": None, "benchmark_error": None,
    }
    wd_app._benchmark_running = False
    body = wd_app.app.test_client().get("/api/io").get_json()
    assert body["summary"]["device"] == "sda8"
    assert body["benchmark"]["randread"]["iops"] == 5000
    assert body["saturation_percent"] == 22.5
    assert body["running"] is False


def test_a_benchmark_starts_in_the_background_and_answers_202(monkeypatch):
    monkeypatch.setattr(wd_app, "start_benchmark", lambda options: (True, None))
    response = wd_app.app.test_client().post("/api/benchmark")
    assert response.status_code == 202
    assert response.get_json() == {"started": True, "error": None}


# --- the benchmark ------------------------------------------------------------


def test_a_benchmark_runs_off_the_request_thread(monkeypatch):
    """fio writes real data for tens of seconds. Running it inline would hold
    the HTTP worker open for the duration and time the request out."""
    finished = threading.Event()

    def fake_run(**kwargs):
        finished.set()
        return {"randread": {"iops": 5000}, "randwrite": {"iops": 900}}, None

    monkeypatch.setattr(wd_app.diskio, "run_benchmark", fake_run)
    started, err = wd_app.start_benchmark(_options())

    assert (started, err) == (True, None)
    assert finished.wait(timeout=5), "benchmark never ran"
    _settle()
    assert wd_app._io["benchmark"]["randread"]["iops"] == 5000
    assert wd_app._io["benchmark_error"] is None
    assert wd_app._benchmark_running is False


def test_a_second_benchmark_is_refused_while_one_is_running(monkeypatch):
    """Two fio runs on one device measure each other, not the device."""
    wd_app._benchmark_running = True
    monkeypatch.setattr(wd_app.diskio, "run_benchmark",
                        lambda **k: pytest.fail("ran a second benchmark"))
    started, err = wd_app.start_benchmark(_options())
    assert started is False
    assert "already running" in err


def test_the_configured_size_and_duration_reach_fio(monkeypatch):
    seen = {}

    def fake_run(**kwargs):
        seen.update(kwargs)
        return {"randread": {"iops": 1}, "randwrite": {"iops": 1}}, None

    monkeypatch.setattr(wd_app.diskio, "run_benchmark", fake_run)
    wd_app.start_benchmark(_options(benchmark_size_mb=512, benchmark_seconds=20,
                                    benchmark_min_free_gb=4))
    _settle()
    assert seen == {"size_mb": 512, "seconds": 20, "min_free_gb": 4}


def test_a_refused_benchmark_is_recorded_and_clears_the_flag(monkeypatch, capsys):
    """Too little free space is the common one, and the reason has to survive to
    the page — the button would otherwise appear to do nothing."""
    monkeypatch.setattr(wd_app.diskio, "run_benchmark",
                        lambda **k: (None, "only 1GB free, need 2GB"))
    wd_app.start_benchmark(_options())
    _settle()
    assert wd_app._io["benchmark_error"] == "only 1GB free, need 2GB"
    assert wd_app._benchmark_running is False
    assert "benchmark failed" in capsys.readouterr().out


def test_a_benchmark_that_raises_still_clears_the_flag(monkeypatch, capsys):
    """Otherwise the flag latches on and the button is dead until a restart."""
    def boom(**kwargs):
        raise OSError("fio not installed")

    monkeypatch.setattr(wd_app.diskio, "run_benchmark", boom)
    wd_app.start_benchmark(_options())
    _settle()
    assert wd_app._benchmark_running is False
    assert "OSError" in wd_app._io["benchmark_error"]
    assert "benchmark raised OSError" in capsys.readouterr().out


# --- the I/O sampler ----------------------------------------------------------


def test_the_sampler_polls_and_publishes_its_error_once(monkeypatch, capsys):
    """Sampling runs every few seconds; logging a persistent read failure every
    time would bury the scan lines it sits between."""
    class _Sampler:
        def __init__(self, interval):
            self.polls = 0
            self.error = "cannot read /proc/diskstats"

        def poll(self):
            self.polls += 1

    monkeypatch.setattr(wd_app.diskio, "Sampler", _Sampler)
    monkeypatch.setattr(wd_app.diskio, "load_benchmark", lambda: {"randread": {"iops": 10}})
    _run_sampler_iterations(monkeypatch, 2)

    assert wd_app._sampler.polls == 2
    assert wd_app._io["error"] == "cannot read /proc/diskstats"
    assert wd_app._io["benchmark"]["randread"]["iops"] == 10
    assert capsys.readouterr().out.count("disk sampling:") == 1


def test_the_sampler_loop_outlives_a_poll_that_raises(monkeypatch, capsys):
    class _Sampler:
        def __init__(self, interval):
            self.error = None

        def poll(self):
            raise RuntimeError("counter vanished")

    monkeypatch.setattr(wd_app.diskio, "Sampler", _Sampler)
    monkeypatch.setattr(wd_app.diskio, "load_benchmark", lambda: None)
    _run_sampler_iterations(monkeypatch, 2)
    assert capsys.readouterr().out.count("disk sample raised RuntimeError") == 2


def _run_sampler_iterations(monkeypatch, count):
    """Let `io_sampler` go round `count` times, then break out of its `while
    True` through the sleep it always reaches."""
    remaining = {"n": count}

    def fake_sleep(_seconds):
        remaining["n"] -= 1
        if remaining["n"] <= 0:
            raise _StopLoop

    monkeypatch.setattr(wd_app.time, "sleep", fake_sleep)
    with pytest.raises(_StopLoop):
        wd_app.io_sampler(_options(io_sample_seconds=1))


def _settle(timeout=5):
    """The benchmark runs on a daemon thread; join it rather than sleeping."""
    for thread in threading.enumerate():
        if thread is not threading.current_thread() and thread.daemon:
            thread.join(timeout=timeout)
