import app as coopapp


def test_index_page_renders(client):
    res = client.get("/")
    assert res.status_code == 200
    assert coopapp.APP_VERSION.encode() in res.data


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


def test_debug_reports_statsmodels_availability(client):
    body = client.get("/api/debug").get_json()
    assert body["statsmodels_available"] == coopapp.STATSMODELS_AVAILABLE
    if coopapp.STATSMODELS_AVAILABLE:
        assert body["statsmodels_error"] is None
    else:
        assert body["statsmodels_error"]


def test_debug_reports_advanced_forecast_enabled(client, set_options):
    set_options(advanced_forecast_enabled=True)
    body = client.get("/api/debug").get_json()
    assert body["advanced_forecast_enabled"] is True


def test_debug_reports_opencv_availability(client):
    body = client.get("/api/debug").get_json()
    assert body["opencv_available"] == coopapp.OPENCV_AVAILABLE
    if coopapp.OPENCV_AVAILABLE:
        assert body["opencv_error"] is None
    else:
        assert body["opencv_error"]


def test_debug_reports_egg_vision_enabled(client, set_options):
    set_options(egg_vision_enabled=True)
    body = client.get("/api/debug").get_json()
    assert body["egg_vision_enabled"] is True
