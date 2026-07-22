import app as coopapp


def test_get_notify_services_extracts_notify_domain(monkeypatch):
    services_payload = [
        {"domain": "light", "services": {"turn_on": {}}},
        {"domain": "notify", "services": {"mobile_app_phone": {}, "persistent_notification": {}}},
    ]
    monkeypatch.setattr(
        coopapp, "_ha_api_request", lambda *a, **k: (services_payload, None)
    )
    names, err = coopapp.get_notify_services()
    assert err is None
    assert names == ["mobile_app_phone", "persistent_notification"]


def test_get_notify_services_empty_when_no_notify_domain(monkeypatch):
    services_payload = [{"domain": "light", "services": {"turn_on": {}}}]
    monkeypatch.setattr(
        coopapp, "_ha_api_request", lambda *a, **k: (services_payload, None)
    )
    names, err = coopapp.get_notify_services()
    assert names == []
    assert err is None


def test_notifications_endpoint_reports_reminder_config(client, set_options):
    set_options(reminder_enabled=True, notify_service="mobile_app_phone", reminder_threshold_days=3)
    body = client.get("/api/notifications").get_json()
    assert body["reminder"]["enabled"] is True
    assert body["reminder"]["notify_service"] == "mobile_app_phone"
    assert body["reminder"]["threshold_days"] == 3
    assert body["services"] == []


def test_notify_test_without_service_configured_fails(client):
    res = client.post("/api/notify-test")
    assert res.status_code == 502
    assert res.get_json()["status"] == "error"


def test_notify_test_sends_via_ha(client, set_options, fake_ha_server):
    set_options(notify_service="mobile_app_phone")
    res = client.post("/api/notify-test")
    assert res.status_code == 200
    assert res.get_json()["status"] == "sent"

    notify_calls = [c for c in fake_ha_server if c["path"] == "/services/notify/mobile_app_phone"]
    assert len(notify_calls) == 1
    assert notify_calls[0]["body"]["title"] == "Coop Tracker test"
