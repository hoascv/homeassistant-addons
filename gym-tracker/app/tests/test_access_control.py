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
