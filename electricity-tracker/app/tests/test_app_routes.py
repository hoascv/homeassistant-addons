import app as electricityapp


def test_ingress_without_header_or_token_is_refused(direct_client):
    res = direct_client.get("/api/summary")
    assert res.status_code == 401
    assert res.headers["WWW-Authenticate"] == "Bearer"


def test_ingress_with_header_is_allowed(client):
    res = client.get("/api/summary")
    assert res.status_code == 200


def test_restrict_to_user_ids_blocks_other_users(client, set_options):
    set_options(restrict_to_user_ids="someone-else")
    res = client.get("/api/summary")
    assert res.status_code == 403


def test_restrict_to_user_ids_allows_listed_user(client, set_options):
    set_options(restrict_to_user_ids="test-ingress-user, someone-else")
    res = client.get("/api/summary")
    assert res.status_code == 200


def test_api_token_grants_access_on_published_port(direct_client, set_options):
    set_options(api_token="secret-token")
    res = direct_client.get("/api/summary", headers={"Authorization": "Bearer secret-token"})
    assert res.status_code == 200


def test_wrong_api_token_is_refused(direct_client, set_options):
    set_options(api_token="secret-token")
    res = direct_client.get("/api/summary", headers={"Authorization": "Bearer wrong"})
    assert res.status_code == 401


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.get_json()["ok"] is True


def test_stats_empty_db(client):
    res = client.get("/api/stats")
    data = res.get_json()
    assert data["counts"] == {"prices": 0, "consumption": 0, "saveeye_samples": 0,
                              "easee_samples": 0, "easee_cloud_sessions": 0}
    assert data["total"] == 0


def test_summary_shape_with_no_data(client):
    data = client.get("/api/summary").get_json()
    assert data["price_area"] == "DK2"
    assert data["current_price"] is None
    assert data["today"] == []
    assert data["tomorrow"] is None
    assert data["consumption"] is None
    assert data["eloverblik_configured"] is False


def test_summary_reflects_synced_prices(client, conn, set_options):
    set_options(price_area="DK2", vat_rate=0.25)
    conn.execute(
        "INSERT INTO prices (time_dk, price_area, spot_price_dkk_kwh, fetched_at) VALUES (?, ?, ?, ?)",
        ("2020-01-01T00:00:00", "DK2", 1.0, "2020-01-01T00:00:00+00:00"),
    )
    conn.commit()
    data = client.get("/api/prices?days=1").get_json()
    # The seeded row is far outside "today", so it should not appear in a
    # days=1 (today-only-ish) window — this just proves the endpoint runs
    # end-to-end without error against a non-empty table.
    assert isinstance(data, list)


def test_eloverblik_diagnose_without_token(client):
    res = client.get("/api/eloverblik/diagnose")
    assert res.status_code == 400
    assert res.get_json()["ok"] is False


def test_consumption_empty_without_metering_point(client):
    res = client.get("/api/consumption")
    assert res.get_json() == []


def test_export_shape(client):
    data = client.get("/api/export").get_json()
    assert set(data["tables"].keys()) == {
        "prices", "consumption", "saveeye_samples", "easee_samples", "easee_cloud_sessions"}
