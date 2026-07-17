import app as coopapp


def test_debug_reports_app_version(client):
    body = client.get("/api/debug").get_json()
    assert body["app_version"] == coopapp.APP_VERSION


def test_debug_without_supervisor_token(client):
    body = client.get("/api/debug").get_json()
    assert body["supervisor_token_set"] is False
    assert body["ha_api_reachable"] is False
    assert "SUPERVISOR_TOKEN not set" in body["ha_api_error"]


def test_debug_reports_db_ok(client):
    body = client.get("/api/debug").get_json()
    assert body["db_ok"] is True
    assert body["db_error"] is None


def test_debug_reachable_with_supervisor_token(client, fake_ha_server):
    body = client.get("/api/debug").get_json()
    assert body["supervisor_token_set"] is True
    assert body["ha_api_reachable"] is True
    assert body["ha_api_error"] is None
