import os
import sqlite3
from datetime import datetime, timedelta

from flask import Flask, g, jsonify, render_template, request

DB_PATH = os.environ.get("COOP_DB_PATH", "/data/coop.db")

app = Flask(__name__)


def get_db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            ts TEXT NOT NULL,
            count INTEGER,
            food_type TEXT,
            amount TEXT,
            notes TEXT
        )
        """
    )
    conn.commit()
    conn.close()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/summary")
def api_summary():
    db = get_db()
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())

    eggs_today = db.execute(
        "SELECT COALESCE(SUM(count), 0) AS total FROM logs WHERE type = 'egg' AND ts >= ?",
        (today_start.isoformat(),),
    ).fetchone()["total"]

    eggs_week = db.execute(
        "SELECT COALESCE(SUM(count), 0) AS total FROM logs WHERE type = 'egg' AND ts >= ?",
        (week_start.isoformat(),),
    ).fetchone()["total"]

    last_cleaning = db.execute(
        "SELECT ts FROM logs WHERE type = 'cleaning' ORDER BY ts DESC LIMIT 1"
    ).fetchone()

    last_feeding = db.execute(
        "SELECT ts FROM logs WHERE type = 'feeding' ORDER BY ts DESC LIMIT 1"
    ).fetchone()

    return jsonify(
        {
            "eggs_today": eggs_today,
            "eggs_week": eggs_week,
            "last_cleaning": last_cleaning["ts"] if last_cleaning else None,
            "last_feeding": last_feeding["ts"] if last_feeding else None,
        }
    )


@app.route("/api/entries")
def api_entries():
    entry_type = request.args.get("type")
    limit = min(int(request.args.get("limit", 50)), 200)

    db = get_db()
    if entry_type:
        rows = db.execute(
            "SELECT * FROM logs WHERE type = ? ORDER BY ts DESC LIMIT ?",
            (entry_type, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM logs ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()

    return jsonify([dict(row) for row in rows])


@app.route("/api/log", methods=["POST"])
def api_log():
    data = request.get_json(force=True, silent=True) or {}
    entry_type = data.get("type")

    if entry_type not in ("egg", "cleaning", "feeding"):
        return jsonify({"error": "invalid type"}), 400

    count = data.get("count")
    food_type = data.get("food_type")
    amount = data.get("amount")
    notes = data.get("notes")

    ts_input = data.get("ts")
    if ts_input:
        try:
            ts = datetime.fromisoformat(ts_input).isoformat()
        except ValueError:
            return jsonify({"error": "invalid ts"}), 400
    else:
        ts = datetime.now().isoformat()

    db = get_db()
    cur = db.execute(
        "INSERT INTO logs (type, ts, count, food_type, amount, notes) VALUES (?, ?, ?, ?, ?, ?)",
        (entry_type, ts, count, food_type, amount, notes),
    )
    db.commit()

    return jsonify({"id": cur.lastrowid, "type": entry_type, "ts": ts}), 201


@app.route("/api/entries/<int:entry_id>", methods=["PUT"])
def api_update_entry(entry_id):
    db = get_db()
    row = db.execute("SELECT * FROM logs WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404

    data = request.get_json(force=True, silent=True) or {}

    ts_input = data.get("ts")
    if ts_input:
        try:
            ts = datetime.fromisoformat(ts_input).isoformat()
        except ValueError:
            return jsonify({"error": "invalid ts"}), 400
    else:
        ts = row["ts"]

    count = data.get("count", row["count"])
    food_type = data.get("food_type", row["food_type"])
    amount = data.get("amount", row["amount"])
    notes = data.get("notes", row["notes"])

    db.execute(
        "UPDATE logs SET ts = ?, count = ?, food_type = ?, amount = ?, notes = ? WHERE id = ?",
        (ts, count, food_type, amount, notes, entry_id),
    )
    db.commit()

    return jsonify({"id": entry_id, "ts": ts}), 200


@app.route("/api/entries/<int:entry_id>", methods=["DELETE"])
def api_delete_entry(entry_id):
    db = get_db()
    db.execute("DELETE FROM logs WHERE id = ?", (entry_id,))
    db.commit()
    return "", 204


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=8099)
