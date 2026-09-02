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


# --- the script has to survive being loaded -----------------------------------


def test_no_top_level_call_is_undefined():
    """A statement at the left margin runs during script evaluation, so an
    undefined name there does not break that one feature — it stops the whole
    file. Every figure on the page stays a dash and nothing in the UI says why.

    This shipped: the trips code in Electricity Tracker was written with an
    `el()` helper carried over from another add-on in this repo, where it does
    exist. Nothing caught it, because no test here executes JavaScript.
    """
    import os
    import re

    path = os.path.join(os.path.dirname(__file__), "..", "static", "app.js")
    with open(path, encoding="utf-8") as handle:
        source = handle.read()

    defined = set(re.findall(
        r"^(?:async\s+)?(?:function|const|let|var)\s+([A-Za-z_$][\w$]*)", source, re.M))
    called_at_load = set(re.findall(r"^([a-z][A-Za-z0-9_$]*)\(", source, re.M))
    missing = sorted(called_at_load - defined)
    assert missing == [], f"called at load but never defined: {missing}"
