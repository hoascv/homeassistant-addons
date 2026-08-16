from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import app as electricityapp
import eloverblik
import energidataservice


def test_sync_prices_writes_rows_and_state(conn, set_options):
    set_options(price_area="DK2")
    fake_rows = [
        {"time_dk": "2026-08-16T00:00:00", "price_dkk_kwh": 1.0},
        {"time_dk": "2026-08-16T00:15:00", "price_dkk_kwh": 1.1},
    ]
    with patch.object(energidataservice, "fetch_day_ahead_prices", return_value=fake_rows):
        electricityapp.sync_prices(conn, electricityapp._read_options())

    rows = conn.execute("SELECT * FROM prices ORDER BY time_dk").fetchall()
    assert len(rows) == 2
    assert rows[0]["spot_price_dkk_kwh"] == 1.0
    assert electricityapp._get_app_state(conn, "last_price_sync") is not None


def test_sync_prices_upserts_on_rerun(conn, set_options):
    set_options(price_area="DK2")
    with patch.object(
        energidataservice, "fetch_day_ahead_prices", return_value=[{"time_dk": "2026-08-16T00:00:00", "price_dkk_kwh": 1.0}]
    ):
        electricityapp.sync_prices(conn, electricityapp._read_options())
    with patch.object(
        energidataservice, "fetch_day_ahead_prices", return_value=[{"time_dk": "2026-08-16T00:00:00", "price_dkk_kwh": 2.0}]
    ):
        electricityapp.sync_prices(conn, electricityapp._read_options())

    rows = conn.execute("SELECT * FROM prices").fetchall()
    assert len(rows) == 1
    assert rows[0]["spot_price_dkk_kwh"] == 2.0


def test_sync_prices_failure_does_not_raise(conn, set_options):
    set_options(price_area="DK2")
    with patch.object(
        energidataservice, "fetch_day_ahead_prices", side_effect=energidataservice.EnergiDataServiceError("boom")
    ):
        electricityapp.sync_prices(conn, electricityapp._read_options())  # must not raise
    assert conn.execute("SELECT COUNT(*) AS n FROM prices").fetchone()["n"] == 0


def test_sync_consumption_skipped_when_not_configured(conn):
    electricityapp.sync_consumption(conn, {})  # no refresh token / metering point
    assert conn.execute("SELECT COUNT(*) AS n FROM consumption").fetchone()["n"] == 0


def test_sync_consumption_writes_rows(conn, set_options):
    set_options(eloverblik_refresh_token="rt", eloverblik_metering_point="mp1", eloverblik_backfill_days=5)
    fake_rows = [{"time_utc": "2026-08-15T22:00:00+00:00", "kwh": 0.5, "quality": "A04"}]
    with patch.object(eloverblik, "get_access_token", return_value="access-token"), patch.object(
        eloverblik, "get_hourly_consumption", return_value=fake_rows
    ):
        electricityapp.sync_consumption(conn, electricityapp._read_options(), today_local=date(2026, 8, 16))

    rows = conn.execute("SELECT * FROM consumption").fetchall()
    assert len(rows) == 1
    assert rows[0]["kwh"] == 0.5
    assert electricityapp._get_app_state(conn, "last_consumption_sync") is not None


def test_get_eloverblik_access_token_is_cached(conn, monkeypatch):
    calls = []

    def fake_get_access_token(refresh_token, timeout=15):
        calls.append(refresh_token)
        return "token-1"

    monkeypatch.setattr(eloverblik, "get_access_token", fake_get_access_token)
    t1 = electricityapp._get_eloverblik_access_token("rt")
    t2 = electricityapp._get_eloverblik_access_token("rt")
    assert t1 == t2 == "token-1"
    assert len(calls) == 1  # second call served from cache


def _seed_price(conn, time_dk, spot=1.0, price_area="DK2"):
    conn.execute(
        "INSERT INTO prices (time_dk, price_area, spot_price_dkk_kwh, fetched_at) VALUES (?, ?, ?, ?)",
        (time_dk, price_area, spot, "2026-08-16T00:00:00+00:00"),
    )


def _seed_consumption(conn, time_utc, kwh, metering_point="mp1"):
    conn.execute(
        "INSERT INTO consumption (time_utc, metering_point, kwh, quality, fetched_at) VALUES (?, ?, ?, ?, ?)",
        (time_utc, metering_point, kwh, "A04", "2026-08-16T00:00:00+00:00"),
    )


def test_consumption_with_cost_matches_hourly_price(conn):
    opts = electricityapp.get_price_options({"grid_tariff_normal": 0.0, "transmission_tariff": 0.0, "electricity_tax": 0.0, "vat_rate": 0.0})
    # Danish local 2026-08-16T12:00 = UTC 2026-08-16T10:00 (CEST, +2h).
    for minute in (0, 15, 30, 45):
        _seed_price(conn, f"2026-08-16T12:{minute:02d}:00", spot=1.0 + minute / 100)
    _seed_consumption(conn, "2026-08-16T10:00:00+00:00", kwh=2.0)
    conn.commit()

    start = datetime(2026, 8, 16, 10, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    rows = electricityapp.consumption_with_cost(conn, start, end, "mp1", "DK2", opts)
    assert len(rows) == 1
    expected_price = (1.0 + 1.15 + 1.30 + 1.45) / 4  # average of the four quarters
    assert round(rows[0]["price_dkk_kwh"], 4) == round(expected_price, 4)
    assert round(rows[0]["cost_dkk"], 4) == round(2.0 * expected_price, 4)


def test_consumption_with_cost_price_missing_leaves_cost_none(conn):
    opts = electricityapp.get_price_options({})
    _seed_consumption(conn, "2026-08-16T10:00:00+00:00", kwh=1.0)
    conn.commit()
    start = datetime(2026, 8, 16, 10, tzinfo=timezone.utc)
    rows = electricityapp.consumption_with_cost(conn, start, start + timedelta(hours=1), "mp1", "DK2", opts)
    assert rows[0]["cost_dkk"] is None
    assert rows[0]["price_dkk_kwh"] is None


def test_publish_sensors_pushes_price_and_consumption(conn, fake_ha_server, set_options):
    set_options(eloverblik_metering_point="mp1")
    opts = electricityapp.get_price_options({})
    now_local = datetime.now(electricityapp.LOCAL_TZ)
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    for i in range(4 * 24):
        _seed_price(conn, (today_start + timedelta(minutes=15 * i)).replace(tzinfo=None).isoformat(), spot=1.0)
    _seed_consumption(conn, today_start.astimezone(timezone.utc).isoformat(), kwh=1.5)
    conn.commit()

    electricityapp.publish_sensors(conn, electricityapp._read_options())

    entities = {c["path"] for c in fake_ha_server}
    assert "/states/sensor.electricity_tracker_price_now" in entities
    assert "/states/sensor.electricity_tracker_consumption_today" in entities
