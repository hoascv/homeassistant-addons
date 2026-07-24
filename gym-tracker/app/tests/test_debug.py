def test_debug_reports_basics(client):
    data = client.get("/api/debug").get_json()
    assert data["app_version"]
    assert data["db_ok"] is True
    assert data["supervisor_token_set"] is False  # dev mode in tests
    assert "reminders" in data
    assert data["access_restricted"] is False


def test_debug_reflects_access_restriction(client, set_options):
    set_options(restrict_to_user_ids="abc123")
    data = client.get("/api/debug", headers={"X-Remote-User-ID": "abc123"}).get_json()
    assert data["access_restricted"] is True
    assert data["ingress_user_id"] == "abc123"


def test_version_matches_config(client):
    import os
    data = client.get("/api/debug").get_json()
    config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config.yaml")
    with open(config_path) as f:
        text = f.read()
    assert f'version: "{data["app_version"]}"' in text
