import csv
import io

import app as coopapp


def _parse_csv(res):
    return list(csv.reader(io.StringIO(res.get_data(as_text=True))))


def test_export_empty_db_has_header_only(client):
    res = client.get("/api/export.csv")
    assert res.status_code == 200
    assert res.mimetype == "text/csv"
    assert "attachment; filename=coop-tracker-export-" in res.headers["Content-Disposition"]
    rows = _parse_csv(res)
    assert rows == [list(coopapp.EXPORT_COLUMNS)]


def test_export_one_entry_of_each_type_lands_in_right_columns(client):
    client.post(
        "/api/log",
        json={"type": "egg", "count": 3, "egg_sizes": "M,M,L", "ts": "2026-01-01T10:00:00"},
    )
    client.post("/api/log", json={"type": "cleaning", "notes": "bedding", "ts": "2026-01-02T10:00:00"})
    client.post(
        "/api/log",
        json={
            "type": "feeding",
            "food_type": "Grain mix",
            "amount": "2 scoops",
            "container_empty": True,
            "ts": "2026-01-03T10:00:00",
        },
    )
    client.post("/api/log", json={"type": "sale", "count": 6, "price": 15.0, "ts": "2026-01-04T10:00:00"})
    client.post(
        "/api/log",
        json={"type": "expense", "category": "Food", "cost": 24.99, "ts": "2026-01-05T10:00:00"},
    )
    client.post(
        "/api/log", json={"type": "used", "count": 2, "given_away": True, "ts": "2026-01-06T10:00:00"}
    )

    header, *rows = _parse_csv(client.get("/api/export.csv"))
    col = {name: i for i, name in enumerate(header)}
    by_type = {row[col["type"]]: row for row in rows}

    assert by_type["egg"][col["count"]] == "3"
    assert by_type["cleaning"][col["notes"]] == "bedding"
    assert by_type["feeding"][col["food_type"]] == "Grain mix"
    assert by_type["feeding"][col["amount"]] == "2 scoops"
    assert by_type["feeding"][col["container_empty"]] == "1"
    assert by_type["sale"][col["price"]] == "15.0"
    assert by_type["expense"][col["category"]] == "Food"
    assert by_type["expense"][col["cost"]] == "24.99"
    assert by_type["used"][col["given_away"]] == "1"
    # columns that don't apply to a type stay blank, not "None"
    assert by_type["egg"][col["price"]] == ""
    assert by_type["cleaning"][col["count"]] == ""
    # egg_sizes itself contains commas — a real quoting test, not just a
    # column-presence check
    assert by_type["egg"][col["egg_sizes"]] == "M,M,L"


def test_export_notes_with_commas_and_newlines_round_trip(client):
    tricky = 'found 2 eggs, one cracked\n"quoted" note'
    client.post("/api/log", json={"type": "egg", "count": 2, "notes": tricky})

    header, row = _parse_csv(client.get("/api/export.csv"))
    assert row[header.index("notes")] == tricky


def test_export_rows_ordered_by_ts_ascending(client):
    for day in (3, 1, 2):
        client.post(
            "/api/log", json={"type": "egg", "count": day, "ts": f"2026-01-0{day}T10:00:00"}
        )

    header, *rows = _parse_csv(client.get("/api/export.csv"))
    ts_col = header.index("ts")
    assert [r[ts_col] for r in rows] == sorted(r[ts_col] for r in rows)
