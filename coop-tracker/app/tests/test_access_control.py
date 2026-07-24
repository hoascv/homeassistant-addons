"""Access control: an optional per-user allowlist enforced from the
Home Assistant ingress user-ID header. See app.py's _enforce_user_allowlist
and ARCHITECTURE.md §21."""
import app as coopapp

HEADER = coopapp.INGRESS_USER_ID_HEADER


def test_unrestricted_by_default(client):
    # No restrict_to_user_ids set -> anyone through ingress may access.
    assert client.get("/api/summary").status_code == 200
    # ...even with no user header at all (dev/local use).
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


def test_missing_header_blocked_when_restricted(client, set_options):
    # A request without the ingress header isn't coming through HA's proxy
    # and can't be trusted once a restriction is in force.
    set_options(restrict_to_user_ids="alice-id")
    assert client.get("/api/summary").status_code == 403


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
