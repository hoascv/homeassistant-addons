import sqlite3
import urllib.error
from datetime import datetime

import app as coopapp


def test_month_bounds_regular_month():
    start, end = coopapp._month_bounds(2026, 3)
    assert start == datetime(2026, 3, 1)
    assert end == datetime(2026, 4, 1)


def test_month_bounds_december_rolls_into_next_year():
    start, end = coopapp._month_bounds(2026, 12)
    assert start == datetime(2026, 12, 1)
    assert end == datetime(2027, 1, 1)


def test_parse_hhmm_valid():
    assert coopapp._parse_hhmm("07:30") == coopapp.dtime(7, 30)


def test_parse_hhmm_invalid():
    assert coopapp._parse_hhmm("not-a-time") is None
    assert coopapp._parse_hhmm(None) is None


def test_is_valid_backup_accepts_matching_schema(tmp_path):
    path = tmp_path / "valid.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE logs (type TEXT, ts TEXT, count INTEGER, food_type TEXT, amount TEXT, notes TEXT)"
    )
    conn.commit()
    conn.close()
    assert coopapp._is_valid_backup(str(path)) is True


def test_is_valid_backup_rejects_missing_columns(tmp_path):
    path = tmp_path / "invalid.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE logs (type TEXT, ts TEXT)")
    conn.commit()
    conn.close()
    assert coopapp._is_valid_backup(str(path)) is False


def test_is_valid_backup_rejects_non_sqlite_file(tmp_path):
    path = tmp_path / "not-a-db.txt"
    path.write_text("hello")
    assert coopapp._is_valid_backup(str(path)) is False


def test_ha_api_request_reports_http_error(monkeypatch):
    monkeypatch.setattr(coopapp, "SUPERVISOR_TOKEN", "fake-token")

    def _raise(*a, **k):
        raise urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(coopapp.urllib.request, "urlopen", _raise)
    data, err = coopapp._ha_api_request("GET", "/config")
    assert data is None
    assert err.startswith("HTTP 401")


def test_ha_api_request_reports_url_error(monkeypatch):
    monkeypatch.setattr(coopapp, "SUPERVISOR_TOKEN", "fake-token")

    def _raise(*a, **k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(coopapp.urllib.request, "urlopen", _raise)
    data, err = coopapp._ha_api_request("GET", "/config")
    assert data is None
    assert "connection refused" in err


def test_ha_api_request_reports_unexpected_exception(monkeypatch):
    monkeypatch.setattr(coopapp, "SUPERVISOR_TOKEN", "fake-token")

    def _raise(*a, **k):
        raise ValueError("boom")

    monkeypatch.setattr(coopapp.urllib.request, "urlopen", _raise)
    data, err = coopapp._ha_api_request("GET", "/config")
    assert data is None
    assert err == "boom"
