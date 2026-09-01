"""The HTTP surface: routes, and as little else as possible.

Every handler here does the same three things — read the request, call one
domain function, return its result. The logic lives in shopping.py, planner.py,
importer.py and prompts.py, all of which are testable without Flask; this file
is the part that would need a test client, so there is as little of it as the
job allows.
"""
import os
import signal
import sys

from flask import Flask, Response, g, jsonify, render_template, request

import config
import importer
import planner
import prompts
import schema
import seeding
import store

APP_VERSION = "1.0.1"  # keep in sync with the "version" field in config.yaml

DB_PATH = os.environ.get("RECIPES_DB_PATH", "/data/recipes.db")
INGRESS_USER_ID_HEADER = "X-Remote-User-ID"

app = Flask(__name__)


def _log(message):
    print(f"[Recipes] {message}", flush=True)


# --- database -----------------------------------------------------------------


def get_db():
    if "db" not in g:
        g.db = schema.connect(DB_PATH)
    return g.db


@app.teardown_appcontext
def _close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def settings():
    return config.load()


# --- access control -----------------------------------------------------------


@app.before_request
def _enforce_access():
    """Ingress only. There is no published port and no api_token, so a request
    without Home Assistant's ingress header did not come through the proxy."""
    user_id = request.headers.get(INGRESS_USER_ID_HEADER)
    if not user_id:
        return Response(
            '{"error": "unauthorized", "detail": "requires Home Assistant ingress"}',
            status=401, mimetype="application/json",
        )
    allowed = settings()["allowed_user_ids"]
    if allowed and user_id not in allowed:
        return Response(
            '{"error": "forbidden", "detail": "this add-on is limited to specific users"}',
            status=403, mimetype="application/json",
        )
    return None


# --- pages --------------------------------------------------------------------


@app.route("/")
def index():
    return render_template("index.html", app_version=APP_VERSION)


@app.route("/api/summary")
def api_summary():
    db = get_db()
    cfg = settings()
    return jsonify({
        "app_version": APP_VERSION,
        "categories": cfg["categories"],
        "categories_in_use": store.categories_in_use(db),
        "default_servings": cfg["default_servings"],
        "staples": cfg["staples"],
        "counts": store.counts(db),
    })


# --- recipes ------------------------------------------------------------------


@app.route("/api/recipes")
def api_recipes():
    return jsonify(store.list_recipes(
        get_db(),
        category=request.args.get("category") or None,
        query=request.args.get("q") or None,
    ))


@app.route("/api/recipes/<int:recipe_id>")
def api_recipe(recipe_id):
    recipe = store.get_recipe(get_db(), recipe_id)
    return jsonify(recipe) if recipe else (jsonify({"error": "no such recipe"}), 404)


@app.route("/api/recipes/<int:recipe_id>", methods=["DELETE"])
def api_delete_recipe(recipe_id):
    db = get_db()
    if store.get_recipe(db, recipe_id) is None:
        return jsonify({"error": "no such recipe"}), 404
    store.delete_recipe(db, recipe_id)
    db.commit()
    return jsonify({"deleted": recipe_id})


# --- importing ----------------------------------------------------------------


@app.route("/api/import", methods=["POST"])
def api_import():
    """Paste an assistant's reply. Anything unusable is reported, not fatal."""
    body = request.get_json(silent=True) or {}
    db = get_db()
    try:
        parsed = importer.normalise(
            importer.extract_json(body.get("text") or ""),
            default_category=(body.get("category") or "").strip() or None,
        )
    except importer.PackError as exc:
        return jsonify({"error": str(exc)}), 400

    added, save_warnings = store.save_many(db, parsed["recipes"])
    db.commit()
    return jsonify({
        "added": added,
        "recipes": len(parsed["recipes"]),
        "warnings": parsed["warnings"] + save_warnings,
    })


@app.route("/api/import/preview", methods=["POST"])
def api_import_preview():
    """The same parse, stored nowhere — so a paste can be checked first."""
    body = request.get_json(silent=True) or {}
    try:
        parsed = importer.normalise(
            importer.extract_json(body.get("text") or ""),
            default_category=(body.get("category") or "").strip() or None,
        )
    except importer.PackError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({
        "recipes": [{"name": r["name"], "category": r["category"],
                     "ingredients": len(r["ingredients"])} for r in parsed["recipes"]],
        "warnings": parsed["warnings"],
    })


@app.route("/api/prompt")
def api_prompt():
    """The text to take to an assistant."""
    db = get_db()
    kind = request.args.get("kind", "new")
    category = (request.args.get("category") or settings()["categories"][0]).strip()
    try:
        count = max(1, min(20, int(request.args.get("count", 5))))
    except (TypeError, ValueError):
        count = 5

    if kind == "snacks":
        text = prompts.snacks_prompt(category, count=count)
    elif kind == "more_like":
        text = prompts.more_like_prompt(request.args.get("name") or "", category, count=count)
    else:
        have = [r["name"] for r in store.list_recipes(db, category=category)]
        text = prompts.new_recipes_prompt(category, count=count,
                                          theme=request.args.get("theme"), avoid=have)
    return jsonify({"kind": kind, "category": category, "prompt": text})


# --- the plan and the list ----------------------------------------------------


@app.route("/api/plan")
def api_plan():
    db = get_db()
    return jsonify({
        "entries": [{"recipe": e["recipe"]["name"], "recipe_id": e["recipe"]["id"],
                     "category": e["recipe"]["category"], "servings": e["servings"]}
                    for e in planner.entries(db)],
        "list": planner.shopping_list(db, staples=settings()["staples"]),
    })


@app.route("/api/plan", methods=["POST"])
def api_plan_add():
    body = request.get_json(silent=True) or {}
    db = get_db()
    recipe_id = body.get("recipe_id")
    if store.get_recipe(db, recipe_id) is None:
        return jsonify({"error": "no such recipe"}), 404
    planner.add(db, recipe_id, body.get("servings") or settings()["default_servings"])
    db.commit()
    return api_plan()


@app.route("/api/plan/<int:recipe_id>", methods=["DELETE"])
def api_plan_remove(recipe_id):
    db = get_db()
    planner.remove(db, recipe_id)
    db.commit()
    return api_plan()


@app.route("/api/plan", methods=["DELETE"])
def api_plan_clear():
    db = get_db()
    planner.clear(db)
    db.commit()
    return api_plan()


@app.route("/api/list/tick", methods=["POST"])
def api_tick():
    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").strip()
    if not key:
        return jsonify({"error": "key is required"}), 400
    db = get_db()
    planner.set_tick(db, key, bool(body.get("ticked")))
    db.commit()
    return jsonify({"key": key, "ticked": bool(body.get("ticked"))})


# --- diagnostics --------------------------------------------------------------


@app.route("/api/health")
def api_health():
    """What the Add-on Watchdog probes."""
    return jsonify({"ok": True, "app_version": APP_VERSION})


@app.route("/api/stats")
def api_stats():
    db = get_db()
    try:
        size = os.path.getsize(DB_PATH)
    except OSError:
        size = None
    return jsonify({"app_version": APP_VERSION, "db_bytes": size,
                    "counts": store.counts(db)})


# --- entrypoint ---------------------------------------------------------------


def _shutdown(signum, _frame):
    _log(f"received signal {signum}, shutting down")
    sys.exit(0)


def main():
    signal.signal(signal.SIGTERM, _shutdown)
    store.init_db(DB_PATH)

    cfg = config.load()
    conn = schema.connect(DB_PATH)
    try:
        added = seeding.seed_if_needed(conn, cfg["family_category"], cfg["bulk_category"])
    finally:
        conn.close()
    if added:
        _log(f"seeded {added} base recipes")

    from waitress import serve
    port = int(os.environ.get("RECIPES_PORT", "8099"))
    _log(f"starting Recipes {APP_VERSION}, serving on 0.0.0.0:{port}")
    serve(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
