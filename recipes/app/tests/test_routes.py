"""The HTTP surface.

Thin by design — each handler reads the request, calls one domain function and
returns it — so these check the wiring and the status codes rather than the
logic, which is covered where it lives.
"""
import json

import pytest

import store
from conftest import a_recipe


def test_ingress_is_required(db_path, options):
    import app as recipesapp
    recipesapp.app.config.update(TESTING=True)
    with recipesapp.app.test_client() as bare:
        assert bare.get("/api/recipes").status_code == 401


def test_a_user_outside_the_restriction_list_is_refused(db_path, options):
    import app as recipesapp
    options(restrict_to_user_ids="someone-else")
    recipesapp.app.config.update(TESTING=True)
    with recipesapp.app.test_client() as other:
        other.environ_base["HTTP_X_REMOTE_USER_ID"] = "not-them"
        assert other.get("/api/recipes").status_code == 403


def test_recipes_can_be_listed_filtered_and_fetched(client, conn):
    store.save_recipe(conn, a_recipe("Curry", category="Family"))
    store.save_recipe(conn, a_recipe("Stew", category="Bulk"))
    conn.commit()

    assert len(client.get("/api/recipes").get_json()) == 2
    assert len(client.get("/api/recipes?category=Bulk").get_json()) == 1
    assert len(client.get("/api/recipes?q=cur").get_json()) == 1


def test_an_unknown_recipe_is_a_404(client):
    assert client.get("/api/recipes/999").status_code == 404
    assert client.delete("/api/recipes/999").status_code == 404


def test_importing_a_pack_stores_it(client):
    pack = json.dumps({"recipes": [{
        "name": "Curry", "category": "Family", "servings": 4,
        "ingredients": [{"name": "chicken", "shop_name": "kylling",
                         "amount": 600, "unit": "g"}]}]})
    body = client.post("/api/import", json={"text": pack}).get_json()
    assert body["added"] == 1
    assert len(client.get("/api/recipes").get_json()) == 1


def test_a_preview_stores_nothing(client):
    """So a paste can be checked before it is committed to."""
    pack = json.dumps({"recipes": [{
        "name": "Curry", "category": "Family",
        "ingredients": [{"name": "chicken", "shop_name": "kylling"}]}]})
    body = client.post("/api/import/preview", json={"text": pack}).get_json()
    assert body["recipes"][0]["name"] == "Curry"
    assert client.get("/api/recipes").get_json() == []


def test_an_unreadable_paste_is_a_400_with_a_usable_message(client):
    response = client.post("/api/import", json={"text": "not json at all"})
    assert response.status_code == 400
    assert response.get_json()["error"]


def test_the_prompt_names_the_category_and_asks_for_both_names(client):
    body = client.get("/api/prompt?kind=new&category=Bulk&count=3").get_json()
    assert body["category"] == "Bulk"
    assert "Bulk" in body["prompt"]
    assert "shop_name" in body["prompt"]


def test_the_prompt_lists_what_is_already_held_so_it_is_not_repeated(client, conn):
    store.save_recipe(conn, a_recipe("Chicken curry", category="Family"))
    conn.commit()
    body = client.get("/api/prompt?category=Family").get_json()
    assert "Chicken curry" in body["prompt"]


@pytest.mark.parametrize("kind", ["new", "snacks", "more_like"])
def test_every_prompt_kind_produces_something(client, kind):
    body = client.get(f"/api/prompt?kind={kind}&category=Family&name=X").get_json()
    assert len(body["prompt"]) > 200


def test_planning_a_recipe_returns_the_list_it_produces(client, conn):
    recipe_id = store.save_recipe(conn, a_recipe())
    conn.commit()
    body = client.post("/api/plan", json={"recipe_id": recipe_id, "servings": 6}).get_json()
    assert body["entries"][0]["servings"] == 6
    assert body["list"]["sections"][0]["items"][0]["name"] == "ris"


def test_planning_an_unknown_recipe_is_a_404(client):
    assert client.post("/api/plan", json={"recipe_id": 999}).status_code == 404


def test_a_recipe_can_be_removed_from_the_plan(client, conn):
    recipe_id = store.save_recipe(conn, a_recipe())
    conn.commit()
    client.post("/api/plan", json={"recipe_id": recipe_id})
    assert client.delete(f"/api/plan/{recipe_id}").get_json()["entries"] == []


def test_the_whole_plan_can_be_cleared(client, conn):
    recipe_id = store.save_recipe(conn, a_recipe())
    conn.commit()
    client.post("/api/plan", json={"recipe_id": recipe_id})
    assert client.delete("/api/plan").get_json()["entries"] == []


def test_an_item_can_be_ticked_and_unticked(client, conn):
    recipe_id = store.save_recipe(conn, a_recipe())
    conn.commit()
    plan = client.post("/api/plan", json={"recipe_id": recipe_id}).get_json()
    key = plan["list"]["sections"][0]["items"][0]["key"]

    client.post("/api/list/tick", json={"key": key, "ticked": True})
    assert client.get("/api/plan").get_json()["list"]["ticked_items"] == 1

    client.post("/api/list/tick", json={"key": key, "ticked": False})
    assert client.get("/api/plan").get_json()["list"]["ticked_items"] == 0


def test_ticking_without_a_key_is_refused(client):
    assert client.post("/api/list/tick", json={}).status_code == 400


def test_health_answers_for_the_watchdog(client):
    assert client.get("/api/health").get_json()["ok"] is True


def test_stats_reports_the_counts(client, conn):
    store.save_recipe(conn, a_recipe())
    conn.commit()
    counts = client.get("/api/stats").get_json()["counts"]
    assert counts["recipes"] == 1
    assert counts["ingredients"] == 1


def test_the_summary_carries_the_configured_categories_and_staples(client, options):
    options(categories="Family, Bulk, Quick", staples="salt, peber")
    body = client.get("/api/summary").get_json()
    assert body["categories"] == ["Family", "Bulk", "Quick"]
    assert body["staples"] == ["salt", "peber"]
