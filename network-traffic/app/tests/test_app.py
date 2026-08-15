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
