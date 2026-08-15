"""app.py: the ingress access-control gate. There is only one door here (no
published port), so this only needs to check the ingress header and the
restrict_to_user_ids list against it.
"""
import app as app_module


def _client():
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


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
