"""Tests for how app.py consumes Saveeye telemetry: config parsing,
cumulative-counter interpolation, DB persistence, and merging estimated
hours into Eloverblik's consumption view. The MQTT client itself is covered
by test_saveeye.py."""
from datetime import datetime, timedelta, timezone

import app as electricityapp


def test_get_saveeye_config_defaults():
    cfg = electricityapp.get_saveeye_config({})
    assert cfg["enabled"] is False
    assert cfg["mqtt_host"] == "core-mosquitto"
    assert cfg["mqtt_port"] == 1883
    assert cfg["mqtt_topic"] == "saveeye/telemetry"
    assert cfg["device_serial"] is None


def test_get_saveeye_config_bad_port_falls_back():
    cfg = electricityapp.get_saveeye_config({"saveeye_mqtt_port": "not-a-port"})
    assert cfg["mqtt_port"] == 1883


def test_interp_series_basic():
    samples = [(0.0, 100.0), (60.0, 160.0)]
    assert electricityapp._interp_series(samples, 30.0) == 130.0


def test_interp_series_exact_boundary():
    samples = [(0.0, 100.0), (60.0, 160.0), (120.0, 200.0)]
    assert electricityapp._interp_series(samples, 60.0) == 160.0


def test_interp_series_outside_range_returns_none():
    samples = [(0.0, 100.0), (60.0, 160.0)]
    assert electricityapp._interp_series(samples, -1.0) is None
    assert electricityapp._interp_series(samples, 61.0) is None


def test_interp_series_needs_at_least_two_samples():
    assert electricityapp._interp_series([(0.0, 100.0)], 0.0) is None
    assert electricityapp._interp_series([], 0.0) is None


def _seed_saveeye_sample(conn, ts_utc, cumulative_wh, power_w=None, device_serial="dev1"):
    conn.execute(
        "INSERT INTO saveeye_samples (ts_utc, device_serial, instant_power_w, cumulative_wh) VALUES (?, ?, ?, ?)",
        (ts_utc, device_serial, power_w, cumulative_wh),
    )


def test_saveeye_hourly_kwh_diffs_bracketing_samples(conn):
    # Danish local 2026-08-16T12:00-13:00 = UTC 10:00-11:00 (CEST, +2h).
    _seed_saveeye_sample(conn, "2026-08-16T09:55:00+00:00", cumulative_wh=1000.0)
    _seed_saveeye_sample(conn, "2026-08-16T10:05:00+00:00", cumulative_wh=1100.0)
    _seed_saveeye_sample(conn, "2026-08-16T10:55:00+00:00", cumulative_wh=1300.0)
    _seed_saveeye_sample(conn, "2026-08-16T11:05:00+00:00", cumulative_wh=1400.0)
    conn.commit()

    start_local = datetime(2026, 8, 16, 12, tzinfo=electricityapp.LOCAL_TZ)
    end_local = datetime(2026, 8, 16, 14, tzinfo=electricityapp.LOCAL_TZ)
    result = electricityapp.saveeye_hourly_kwh(conn, start_local, end_local, "dev1")

    assert "2026-08-16T12:00:00" in result
    # Interpolated: at 10:00 UTC, between (09:55, 1000) and (10:05, 1100) -> 1050.
    # At 11:00 UTC, between (10:55, 1300) and (11:05, 1400) -> 1350. Diff = 300 Wh = 0.3 kWh.
    assert result["2026-08-16T12:00:00"] == 0.3


def test_saveeye_hourly_kwh_skips_hours_without_bracketing_samples(conn):
    _seed_saveeye_sample(conn, "2026-08-16T09:55:00+00:00", cumulative_wh=1000.0)
    _seed_saveeye_sample(conn, "2026-08-16T10:05:00+00:00", cumulative_wh=1100.0)
    conn.commit()
    start_local = datetime(2026, 8, 16, 12, tzinfo=electricityapp.LOCAL_TZ)
    end_local = datetime(2026, 8, 16, 14, tzinfo=electricityapp.LOCAL_TZ)
    result = electricityapp.saveeye_hourly_kwh(conn, start_local, end_local, "dev1")
    assert "2026-08-16T13:00:00" not in result  # no samples anywhere near 11:00-12:00 UTC


def test_saveeye_hourly_kwh_without_device_serial_returns_empty(conn):
    start_local = datetime(2026, 8, 16, 12, tzinfo=electricityapp.LOCAL_TZ)
    assert electricityapp.saveeye_hourly_kwh(conn, start_local, start_local + timedelta(hours=1), None) == {}


def test_persist_saveeye_sample_writes_latest_reading(conn, monkeypatch):
    monkeypatch.setattr(
        electricityapp,
        "_saveeye_latest",
        {
            "payload": {"device_serial": "dev1", "instant_power_w": 500.0, "cumulative_wh": 12345.0},
            "received_at": "2026-08-16T10:00:00+00:00",
        },
    )
    electricityapp._persist_saveeye_sample(conn)
    rows = conn.execute("SELECT * FROM saveeye_samples").fetchall()
    assert len(rows) == 1
    assert rows[0]["cumulative_wh"] == 12345.0


def test_persist_saveeye_sample_skips_duplicate_timestamp(conn, monkeypatch):
    payload = {"device_serial": "dev1", "instant_power_w": 500.0, "cumulative_wh": 12345.0}
    monkeypatch.setattr(electricityapp, "_saveeye_latest", {"payload": payload, "received_at": "2026-08-16T10:00:00+00:00"})
    electricityapp._persist_saveeye_sample(conn)
    electricityapp._persist_saveeye_sample(conn)  # same received_at again
    rows = conn.execute("SELECT * FROM saveeye_samples").fetchall()
    assert len(rows) == 1


def test_persist_saveeye_sample_noop_without_data(conn):
    electricityapp._persist_saveeye_sample(conn)
    assert conn.execute("SELECT COUNT(*) AS n FROM saveeye_samples").fetchone()["n"] == 0


def _seed_price(conn, time_dk, spot=1.0, price_area="DK2"):
    conn.execute(
        "INSERT INTO prices (time_dk, price_area, spot_price_dkk_kwh, fetched_at) VALUES (?, ?, ?, ?)",
        (time_dk, price_area, spot, "2026-08-16T00:00:00+00:00"),
    )


def _seed_eloverblik_row(conn, time_utc, kwh, metering_point="mp1"):
    conn.execute(
        "INSERT INTO consumption (time_utc, metering_point, kwh, quality, fetched_at) VALUES (?, ?, ?, ?, ?)",
        (time_utc, metering_point, kwh, "A04", "2026-08-16T00:00:00+00:00"),
    )


def test_combined_consumption_prefers_eloverblik_over_saveeye_for_same_hour(conn):
    opts = electricityapp.get_price_options(
        {"grid_tariff_normal": 0.0, "transmission_tariff": 0.0, "electricity_tax": 0.0, "vat_rate": 0.0}
    )
    for minute in (0, 15, 30, 45):
        _seed_price(conn, f"2026-08-16T12:{minute:02d}:00", spot=1.0)
    _seed_eloverblik_row(conn, "2026-08-16T10:00:00+00:00", kwh=2.0)  # 12:00 local, measured
    _seed_saveeye_sample(conn, "2026-08-16T09:55:00+00:00", cumulative_wh=1000.0)
    _seed_saveeye_sample(conn, "2026-08-16T11:05:00+00:00", cumulative_wh=9000.0)  # would imply a huge estimate
    conn.commit()

    start_local = datetime(2026, 8, 16, 12, tzinfo=electricityapp.LOCAL_TZ)
    end_local = start_local + timedelta(hours=1)
    rows = electricityapp.combined_consumption_with_cost(conn, start_local, end_local, "mp1", "DK2", opts, "dev1")

    assert len(rows) == 1
    assert rows[0]["source"] == "eloverblik"
    assert rows[0]["kwh"] == 2.0  # not overridden by the Saveeye estimate


def test_combined_consumption_fills_gap_with_saveeye_estimate(conn):
    opts = electricityapp.get_price_options(
        {"grid_tariff_normal": 0.0, "transmission_tariff": 0.0, "electricity_tax": 0.0, "vat_rate": 0.0}
    )
    for minute in (0, 15, 30, 45):
        _seed_price(conn, f"2026-08-16T12:{minute:02d}:00", spot=2.0)
    # No Eloverblik row for this hour — only Saveeye samples bracketing it.
    _seed_saveeye_sample(conn, "2026-08-16T09:55:00+00:00", cumulative_wh=1000.0)
    _seed_saveeye_sample(conn, "2026-08-16T11:05:00+00:00", cumulative_wh=1500.0)
    conn.commit()

    start_local = datetime(2026, 8, 16, 12, tzinfo=electricityapp.LOCAL_TZ)
    end_local = start_local + timedelta(hours=1)
    rows = electricityapp.combined_consumption_with_cost(conn, start_local, end_local, "mp1", "DK2", opts, "dev1")

    assert len(rows) == 1
    assert rows[0]["source"] == "saveeye_estimate"
    # Interpolated: (09:55, 1000) -> (11:05, 1500) is 500 Wh over 70 minutes.
    # At 10:00 (5 min in): 1035.714. At 11:00 (65 min in): 1464.286. Diff = 428.57 Wh.
    assert rows[0]["kwh"] == 0.4286
    assert rows[0]["cost_dkk"] == round(0.4286 * 2.0, 4)


def test_combined_consumption_without_saveeye_serial_is_eloverblik_only(conn):
    opts = electricityapp.get_price_options({})
    _seed_eloverblik_row(conn, "2026-08-16T10:00:00+00:00", kwh=1.0)
    conn.commit()
    start_local = datetime(2026, 8, 16, 12, tzinfo=electricityapp.LOCAL_TZ)
    rows = electricityapp.combined_consumption_with_cost(
        conn, start_local, start_local + timedelta(hours=1), "mp1", "DK2", opts, None
    )
    assert len(rows) == 1
    assert rows[0]["source"] == "eloverblik"


def test_api_saveeye_now_disabled_by_default(client):
    res = client.get("/api/saveeye/now")
    assert res.get_json() == {"enabled": False}


def test_api_saveeye_now_enabled_with_no_data_yet(client, set_options):
    set_options(saveeye_enabled=True)
    res = client.get("/api/saveeye/now")
    data = res.get_json()
    assert data["enabled"] is True
    assert data["payload"] is None


def test_api_saveeye_now_reports_latest_payload(client, set_options, monkeypatch):
    set_options(saveeye_enabled=True)
    monkeypatch.setattr(
        electricityapp,
        "_saveeye_latest",
        {"payload": {"device_serial": "dev1", "instant_power_w": 400.0}, "received_at": "now"},
    )
    data = client.get("/api/saveeye/now").get_json()
    assert data["payload"]["instant_power_w"] == 400.0
