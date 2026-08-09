import app as tracker_app

"""Access control: an optional per-user allowlist enforced from the
Home Assistant ingress user-ID header. See app.py's _enforce_user_allowlist
and ARCHITECTURE.md §21."""
import app as coopapp

HEADER = coopapp.INGRESS_USER_ID_HEADER


def test_unrestricted_by_default(client):
    # No restrict_to_user_ids set -> anyone through ingress may access.
    assert client.get("/api/summary").status_code == 200
    assert client.get("/").status_code == 200


def test_allowed_user_passes(client, set_options):
    set_options(restrict_to_user_ids="alice-id, bob-id")
    res = client.get("/api/summary", headers={HEADER: "bob-id"})
    assert res.status_code == 200


def test_disallowed_user_blocked(client, set_options):
    set_options(restrict_to_user_ids="alice-id")
    res = client.get("/api/summary", headers={HEADER: "mallory-id"})
    assert res.status_code == 403
    assert b"Access restricted" in res.data
    # the blocked user is shown their own id so they can request access
    assert b"mallory-id" in res.data


def test_missing_header_blocked_when_restricted(direct_client, set_options):
    # A request without the ingress header isn't coming through HA's proxy, so
    # it is a published-port request: refused for want of a token (401) rather
    # than for want of an allowlisted user (403). Either way it does not pass.
    set_options(restrict_to_user_ids="alice-id")
    assert direct_client.get("/api/summary").status_code == 401


def test_restriction_covers_writes_and_reads(client, set_options):
    set_options(restrict_to_user_ids="alice-id")
    assert client.get("/").status_code == 403
    assert client.post("/api/log", json={"type": "egg", "count": 1}).status_code == 403


def test_allowlist_parsing_tolerates_separators(client, set_options):
    set_options(restrict_to_user_ids="  alice-id ,,\n bob-id  ")
    assert coopapp.get_allowed_user_ids() == {"alice-id", "bob-id"}
    assert client.get("/api/summary", headers={HEADER: "alice-id"}).status_code == 200


def test_debug_reports_user_id_and_restriction(client, set_options):
    set_options(restrict_to_user_ids="alice-id")
    body = client.get("/api/debug", headers={HEADER: "alice-id"}).get_json()
    assert body["ingress_user_id"] == "alice-id"
    assert body["access_restricted"] is True


def test_debug_reports_unrestricted(client):
    body = client.get("/api/debug").get_json()
    assert body["access_restricted"] is False


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
    res = direct_client.get("/api/summary")
    assert res.status_code == 401
    assert res.headers["WWW-Authenticate"] == "Bearer"
    assert "api_token" in res.get_json()["detail"]


def test_published_port_is_refused_when_the_token_is_wrong(direct_client, set_options):
    set_options(api_token="s3cret-token")
    res = direct_client.get("/api/summary", headers={"Authorization": "Bearer nope"})
    assert res.status_code == 401


def test_published_port_accepts_the_configured_token(direct_client, set_options):
    set_options(api_token="s3cret-token")
    res = direct_client.get(
        "/api/summary", headers={"Authorization": "Bearer s3cret-token"}
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
    assert client.get("/api/summary").status_code == 200
