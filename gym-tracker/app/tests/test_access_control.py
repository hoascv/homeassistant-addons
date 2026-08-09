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


def test_missing_header_blocked_when_restricted(direct_client, set_options):
    """No ingress header is now a published-port request, so it is refused for
    want of a token (401) rather than for want of an allowlisted user (403)."""
    set_options(restrict_to_user_ids="abc123")
    res = direct_client.get("/api/goal")
    assert res.status_code == 401


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


# --- The published port ------------------------------------------------------
# The docs have always said api_token "is the only thing protecting the API once
# you publish the port". Until 1.4x that was false: the token was a *bypass* of
# the user allowlist, and with the allowlist at its default (empty) every
# endpoint answered an unauthenticated caller — including the whole-database
# ones. These four pin the rule that makes the sentence true.


def test_published_port_is_refused_without_a_token(direct_client, set_options):
    """The case that was open: no allowlist, no token configured, no header."""
    set_options()
    res = direct_client.get("/api/goal")
    assert res.status_code == 401
    assert res.headers["WWW-Authenticate"] == "Bearer"
    assert "api_token" in res.get_json()["detail"]


def test_published_port_is_refused_when_the_token_is_wrong(direct_client, set_options):
    set_options(api_token="s3cret-token")
    res = direct_client.get("/api/goal", headers={"Authorization": "Bearer nope"})
    assert res.status_code == 401


def test_published_port_accepts_the_configured_token(direct_client, set_options):
    set_options(api_token="s3cret-token")
    res = direct_client.get(
        "/api/goal", headers={"Authorization": "Bearer s3cret-token"}
    )
    assert res.status_code == 200


def test_the_bulk_endpoints_are_covered_too(direct_client, set_options):
    """These are the ones that made the gap worth closing rather than noting:
    /api/export serialises every row and /api/backup hands over the file."""
    set_options()
    for url in ("/api/export", "/api/stats", "/api/backup"):
        assert direct_client.get(url).status_code == 401, url


def test_ingress_still_works_without_any_token(client, set_options):
    """The flip side: closing the port must not put a credential in front of the
    UI. An ingress user with no token configured anywhere is unaffected."""
    set_options()
    assert client.get("/api/goal").status_code == 200
