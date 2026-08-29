"""app.py: the ingress access-control gate. There is only one door here (no
published port), so this only needs to check the ingress header and the
restrict_to_user_ids list against it.
"""
import app as app_module


def _client():
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


class FakeCapture:
    """A stand-in for capture.Capture — status()/pause()/resume() only, no
    real subprocess or flag file involved."""

    def __init__(self, paused=False, running=True):
        self.paused = paused
        self.running = running
        self.pause_called = False
        self.resume_called = False

    def status(self):
        return {"running": self.running, "paused": self.paused, "pid": 1,
                 "restarts": 0, "last_error": None}

    def pause(self):
        self.pause_called = True
        self.paused = True

    def resume(self):
        self.resume_called = True
        self.paused = False


class FakeLifecycle:
    """A stand-in for lifecycle.Lifecycle — datalake_usage()/clear_datalake()
    only, no real MinIO client involved."""

    def __init__(self, usage=(3, 1024, None), clear_result=(3, 1024, None)):
        self._usage = usage
        self._clear_result = clear_result
        self.clear_called = False

    def status(self):
        return {}

    def datalake_usage(self):
        return self._usage

    def clear_datalake(self):
        self.clear_called = True
        return self._clear_result


def test_missing_ingress_header_is_401(monkeypatch):
    monkeypatch.setattr(app_module, "_options", {"restrict_to_user_ids": ""})
    resp = _client().get("/api/status")
    assert resp.status_code == 401


def test_ingress_header_with_no_restriction_list_is_allowed(monkeypatch):
    monkeypatch.setattr(app_module, "_options", {"restrict_to_user_ids": ""})
    monkeypatch.setattr(app_module, "_capture", None)
    monkeypatch.setattr(app_module, "_lifecycle", None)
    resp = _client().get("/api/status", headers={"X-Remote-User-ID": "abc123"})
    assert resp.status_code == 200


def test_user_not_on_the_restriction_list_is_403(monkeypatch):
    monkeypatch.setattr(app_module, "_options", {"restrict_to_user_ids": "user-a, user-b"})
    resp = _client().get("/api/status", headers={"X-Remote-User-ID": "someone-else"})
    assert resp.status_code == 403


def test_user_on_the_restriction_list_is_allowed(monkeypatch):
    monkeypatch.setattr(app_module, "_options", {"restrict_to_user_ids": "user-a, user-b"})
    monkeypatch.setattr(app_module, "_capture", None)
    monkeypatch.setattr(app_module, "_lifecycle", None)
    resp = _client().get("/api/status", headers={"X-Remote-User-ID": "user-a"})
    assert resp.status_code == 200


def test_health_reflects_capture_state(monkeypatch):
    monkeypatch.setattr(app_module, "_options", {"restrict_to_user_ids": ""})
    monkeypatch.setattr(app_module, "_capture", None)
    resp = _client().get("/api/health", headers={"X-Remote-User-ID": "abc123"})
    # No capture started at all still answers (any status under 500 is what
    # the Add-on Watchdog's probe treats as alive) — but reports unhealthy.
    assert resp.status_code == 503
    assert resp.get_json()["ok"] is False


def test_health_reports_ok_while_paused(monkeypatch):
    """A deliberate pause is not tcpdump having died — the probe must not
    read it as degraded."""
    monkeypatch.setattr(app_module, "_options", {"restrict_to_user_ids": ""})
    monkeypatch.setattr(app_module, "_capture", FakeCapture(paused=True, running=False))
    resp = _client().get("/api/health", headers={"X-Remote-User-ID": "abc123"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert "paused" in body["detail"]


def test_pause_endpoint_calls_capture_pause(monkeypatch):
    monkeypatch.setattr(app_module, "_options", {"restrict_to_user_ids": ""})
    cap = FakeCapture()
    monkeypatch.setattr(app_module, "_capture", cap)
    resp = _client().post("/api/pause", headers={"X-Remote-User-ID": "abc123"})
    assert resp.status_code == 200
    assert resp.get_json() == {"paused": True}
    assert cap.pause_called is True


def test_resume_endpoint_calls_capture_resume(monkeypatch):
    monkeypatch.setattr(app_module, "_options", {"restrict_to_user_ids": ""})
    cap = FakeCapture(paused=True)
    monkeypatch.setattr(app_module, "_capture", cap)
    resp = _client().post("/api/resume", headers={"X-Remote-User-ID": "abc123"})
    assert resp.status_code == 200
    assert resp.get_json() == {"paused": False}
    assert cap.resume_called is True


def test_pause_endpoint_requires_ingress_too(monkeypatch):
    monkeypatch.setattr(app_module, "_options", {"restrict_to_user_ids": ""})
    resp = _client().post("/api/pause")
    assert resp.status_code == 401


def test_report_status_treats_pause_as_healthy_not_degraded(monkeypatch):
    calls = []
    monkeypatch.setattr(
        app_module.status, "write_status",
        lambda ok, detail, metrics=None: calls.append((ok, detail)),
    )
    monkeypatch.setattr(app_module, "_capture", FakeCapture(paused=True, running=False))
    monkeypatch.setattr(app_module, "_lifecycle", None)

    app_module._report_status()

    ok, detail = calls[0]
    assert ok is True
    assert "capture paused" in detail


def test_datalake_usage_endpoint_reports_count_and_bytes(monkeypatch):
    monkeypatch.setattr(app_module, "_options", {"restrict_to_user_ids": ""})
    monkeypatch.setattr(app_module, "_lifecycle", FakeLifecycle(usage=(5, 2048, None)))
    resp = _client().get("/api/datalake-usage", headers={"X-Remote-User-ID": "abc123"})
    assert resp.status_code == 200
    assert resp.get_json() == {"count": 5, "bytes": 2048, "error": None}


def test_datalake_usage_endpoint_not_ready_before_startup(monkeypatch):
    monkeypatch.setattr(app_module, "_options", {"restrict_to_user_ids": ""})
    monkeypatch.setattr(app_module, "_lifecycle", None)
    resp = _client().get("/api/datalake-usage", headers={"X-Remote-User-ID": "abc123"})
    assert resp.status_code == 503


def test_clear_datalake_endpoint_calls_lifecycle_clear(monkeypatch):
    monkeypatch.setattr(app_module, "_options", {"restrict_to_user_ids": ""})
    life = FakeLifecycle(clear_result=(7, 4096, None))
    monkeypatch.setattr(app_module, "_lifecycle", life)
    resp = _client().post("/api/clear-datalake", headers={"X-Remote-User-ID": "abc123"})
    assert resp.status_code == 200
    assert resp.get_json() == {"deleted": 7, "bytes": 4096, "error": None}
    assert life.clear_called is True


def test_clear_datalake_endpoint_surfaces_partial_failure(monkeypatch):
    monkeypatch.setattr(app_module, "_options", {"restrict_to_user_ids": ""})
    life = FakeLifecycle(clear_result=(2, 200, "some-key: access denied"))
    monkeypatch.setattr(app_module, "_lifecycle", life)
    resp = _client().post("/api/clear-datalake", headers={"X-Remote-User-ID": "abc123"})
    assert resp.status_code == 502
    body = resp.get_json()
    assert body["deleted"] == 2
    assert "access denied" in body["error"]


def test_clear_datalake_endpoint_requires_ingress(monkeypatch):
    monkeypatch.setattr(app_module, "_options", {"restrict_to_user_ids": ""})
    resp = _client().post("/api/clear-datalake")
    assert resp.status_code == 401


# --- options ------------------------------------------------------------------


def test_options_fall_back_to_defaults_when_the_file_is_missing(monkeypatch, capsys):
    """Every dev run outside Supervisor takes this path, and so does a first
    boot before the options file is written."""
    monkeypatch.setattr(app_module, "OPTIONS_PATH", "/nonexistent/options.json")
    options = app_module.load_options()
    assert options["capture_interfaces"] == "any"
    assert options["rotate_seconds"] == 300
    assert options["minio_bucket"] == "raw"
    assert "using defaults" in capsys.readouterr().out


def test_malformed_options_do_not_stop_the_capture(monkeypatch, tmp_path, capsys):
    """A half-written options file should cost the settings, not the add-on —
    this one runs with elevated capabilities and is awkward to restart."""
    bad = tmp_path / "options.json"
    bad.write_text("{not json")
    monkeypatch.setattr(app_module, "OPTIONS_PATH", str(bad))
    assert app_module.load_options()["retention_files"] == 12
    assert "using defaults" in capsys.readouterr().out


def test_configured_options_override_the_defaults(monkeypatch, tmp_path):
    import json

    path = tmp_path / "options.json"
    path.write_text(json.dumps({"rotate_seconds": 60, "bpf_filter": "port 53"}))
    monkeypatch.setattr(app_module, "OPTIONS_PATH", str(path))
    options = app_module.load_options()
    assert options["rotate_seconds"] == 60
    assert options["bpf_filter"] == "port 53"
    assert options["minio_bucket"] == "raw", "an unset key should keep its default"


# --- the status file the watchdog reads ---------------------------------------


def _capture_status_write(monkeypatch):
    """Intercept the status file rather than writing to /share in a test."""
    written = {}

    def fake_write(ok, detail, metrics=None):
        written.update({"ok": ok, "detail": detail, "metrics": metrics})

    monkeypatch.setattr(app_module.status, "write_status", fake_write)
    return written


def test_a_running_capture_reports_healthy(monkeypatch):
    written = _capture_status_write(monkeypatch)
    monkeypatch.setattr(app_module, "_capture", FakeCapture(running=True))
    monkeypatch.setattr(app_module, "_lifecycle", FakeLifecycle())
    app_module._report_status()
    assert written["ok"] is True
    assert written["detail"] == "capturing"


def test_a_dead_tcpdump_reports_unhealthy_with_the_reason(monkeypatch):
    """The whole point of the status file: the dashboard answers fine with a
    dead tcpdump behind it, so the watchdog cannot learn this from a probe."""
    written = _capture_status_write(monkeypatch)
    monkeypatch.setattr(app_module, "_capture", FakeCapture(running=False))
    monkeypatch.setattr(app_module, "_lifecycle", FakeLifecycle())
    app_module._report_status()
    assert written["ok"] is False
    assert "tcpdump is not running" in written["detail"]


def test_an_upload_error_is_appended_to_the_detail(monkeypatch):
    """A capture that is running while nothing reaches MinIO is still a fault,
    and it is invisible in the capture status alone."""
    written = _capture_status_write(monkeypatch)

    class _Lifecycle(FakeLifecycle):
        def status(self):
            return {"last_error": "MinIO refused the connection"}

    monkeypatch.setattr(app_module, "_capture", FakeCapture(running=True))
    monkeypatch.setattr(app_module, "_lifecycle", _Lifecycle())
    app_module._report_status()
    assert "MinIO refused the connection" in written["detail"]


def test_a_paused_capture_reports_healthy_even_with_an_upload_error(monkeypatch):
    written = _capture_status_write(monkeypatch)

    class _Lifecycle(FakeLifecycle):
        def status(self):
            return {"last_error": "backlog"}

    monkeypatch.setattr(app_module, "_capture", FakeCapture(paused=True, running=False))
    monkeypatch.setattr(app_module, "_lifecycle", _Lifecycle())
    app_module._report_status()
    assert written["ok"] is True
    assert "capture paused" in written["detail"]


def test_the_metrics_carry_both_halves(monkeypatch):
    """The watchdog flattens these onto the sensor as report_* attributes, so a
    key dropped here is an attribute that silently stops existing."""
    written = _capture_status_write(monkeypatch)

    class _Lifecycle(FakeLifecycle):
        def status(self):
            return {"uploaded": 12, "pending": 1}

    monkeypatch.setattr(app_module, "_capture", FakeCapture(running=True))
    monkeypatch.setattr(app_module, "_lifecycle", _Lifecycle())
    app_module._report_status()
    assert written["metrics"]["running"] is True
    assert written["metrics"]["uploaded"] == 12


def test_reporting_before_startup_does_not_raise(monkeypatch):
    """status_loop starts alongside the other two threads; it can run once
    before either has been assigned."""
    written = _capture_status_write(monkeypatch)
    monkeypatch.setattr(app_module, "_capture", None)
    monkeypatch.setattr(app_module, "_lifecycle", None)
    app_module._report_status()
    assert written["ok"] is False


class _StopLoop(Exception):
    pass


def test_the_status_loop_outlives_a_report_that_raises(monkeypatch, capsys):
    """This thread is the only thing keeping the watchdog informed; one bad
    report must not end it."""
    def boom():
        raise RuntimeError("share is read-only")

    monkeypatch.setattr(app_module, "_report_status", boom)

    def fake_sleep(_seconds):
        raise _StopLoop

    monkeypatch.setattr(app_module.time, "sleep", fake_sleep)
    import pytest

    with pytest.raises(_StopLoop):
        app_module.status_loop(poll_seconds=1)
    assert "status report raised RuntimeError" in capsys.readouterr().out


# --- the dashboard ------------------------------------------------------------


def test_status_endpoint_reports_both_loops(monkeypatch):
    monkeypatch.setattr(app_module, "_capture", FakeCapture(running=True))
    monkeypatch.setattr(app_module, "_lifecycle", FakeLifecycle())
    client = _client()
    body = client.get("/api/status", headers={"X-Remote-User-ID": "u"}).get_json()
    assert body["capture"]["running"] is True
    assert body["started_at"]


def test_status_endpoint_requires_ingress():
    assert _client().get("/api/status").status_code == 401


def test_the_page_renders_before_either_loop_has_started(monkeypatch):
    """main() serves the app on the same thread it starts the loops from, so a
    page load can arrive before Capture and Lifecycle exist."""
    monkeypatch.setattr(app_module, "_capture", None)
    monkeypatch.setattr(app_module, "_lifecycle", None)
    monkeypatch.setattr(app_module, "_options", app_module.load_options())
    response = _client().get("/", headers={"X-Remote-User-ID": "u"})
    assert response.status_code == 200


def test_uptime_reads_in_the_largest_sensible_unit():
    assert app_module.uptime(45) == "45s"
    assert app_module.uptime(600) == "10m"
    assert app_module.uptime(7200) == "2h"
    assert app_module.uptime(259200) == "3d"


def test_no_uptime_is_a_dash_not_a_zero():
    assert app_module.uptime(None) == "—"
