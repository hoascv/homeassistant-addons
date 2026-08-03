import app as tracker_app

HEADER = "X-Remote-User-ID"


def test_unrestricted_by_default(client):
    assert client.get("/api/goal").status_code == 200


def test_allowed_user_passes(client, set_options):
    set_options(restrict_to_user_ids="abc123, def456")
    assert client.get("/api/goal", headers={HEADER: "abc123"}).status_code == 200


def test_disallowed_user_blocked(client, set_options):
    set_options(restrict_to_user_ids="abc123")
    res = client.get("/api/goal", headers={HEADER: "someone-else"})
    assert res.status_code == 403
    assert b"Access restricted" in res.data


def test_missing_header_blocked_when_restricted(client, set_options):
    set_options(restrict_to_user_ids="abc123")
    res = client.get("/api/goal")
    assert res.status_code == 403


def test_whitespace_and_newline_separated_ids(client, set_options):
    set_options(restrict_to_user_ids="abc123\n def456  ghi789")
    for uid in ("abc123", "def456", "ghi789"):
        assert client.get("/api/goal", headers={HEADER: uid}).status_code == 200


def test_allowlist_covers_the_challenge_endpoints(client, set_options):
    """The allowlist is a before_request hook, so it must cover every route —
    including ones added after it was written."""
    set_options(restrict_to_user_ids="abc123")
    for url in ("/api/challenges", "/api/challenges/stats", "/api/goal/history"):
        assert client.get(url).status_code == 403, url
        assert client.get(url, headers={"X-Remote-User-ID": "abc123"}).status_code == 200, url


# --- Is token auth even usable? ---------------------------------------------
# A pipeline presenting a token gets the same 403 whether the token is wrong or
# no token is configured at all. These pin the signal that tells them apart.


def test_api_access_summary_says_off_when_no_token(client, set_options):
    set_options(api_token="")
    summary = tracker_app._api_access_summary()
    assert "OFF" in summary
    assert "no api_token configured" in summary


def test_api_access_summary_says_on_with_length(client, set_options):
    set_options(api_token="  s3cret-token  ")
    summary = tracker_app._api_access_summary()
    assert "ON" in summary
    # Length is of the stripped token, so it matches what a caller would send.
    assert "12 characters" in summary
    # The value itself must never reach a log.
    assert "s3cret-token" not in summary


def test_debug_reports_token_state_but_never_the_value(client, set_options):
    set_options(api_token="s3cret-token", restrict_to_user_ids="")
    data = client.get("/api/debug").get_json()
    assert data["api_token_set"] is True
    assert data["api_token_length"] == 12
    assert data["restrict_to_user_ids_set"] is False
    assert "s3cret-token" not in client.get("/api/debug").get_data(as_text=True)


def test_debug_reports_when_no_token_is_configured(client, set_options):
    set_options(api_token="")
    data = client.get("/api/debug").get_json()
    assert data["api_token_set"] is False
    assert data["api_token_length"] == 0
