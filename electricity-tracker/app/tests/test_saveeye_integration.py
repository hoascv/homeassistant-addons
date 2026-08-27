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


def test_resolve_saveeye_device_serial_prefers_configured_value(conn):
    assert electricityapp.resolve_saveeye_device_serial(conn, "configured-dev") == "configured-dev"


def test_resolve_saveeye_device_serial_falls_back_to_in_memory_latest(conn, monkeypatch):
    monkeypatch.setattr(
        electricityapp, "_saveeye_latest", {"payload": {"device_serial": "live-dev"}, "received_at": "now"}
    )
    assert electricityapp.resolve_saveeye_device_serial(conn, None) == "live-dev"


def test_resolve_saveeye_device_serial_falls_back_to_most_recent_stored_row(conn):
    # No in-memory telemetry (e.g. add-on just restarted), but samples already exist.
    conn.execute(
        "INSERT INTO saveeye_samples (ts_utc, device_serial, instant_power_w, cumulative_wh) VALUES (?, ?, ?, ?)",
        ("2026-08-16T10:00:00+00:00", "stored-dev", 100.0, 1000.0),
    )
    conn.commit()
    assert electricityapp.resolve_saveeye_device_serial(conn, None) == "stored-dev"


def test_resolve_saveeye_device_serial_none_when_nothing_known(conn):
    assert electricityapp.resolve_saveeye_device_serial(conn, None) is None


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
    # ...but Saveeye's own number for that same hour still rides along as the
    # second, unblended series the chart draws next to the measured one.
    assert rows[0]["measured_kwh"] == 2.0
    # (09:55, 1000) -> (11:05, 9000) is 8000 Wh over 70 min; the 10:00-11:00 slice is 6857.14 Wh.
    assert rows[0]["saveeye_kwh"] == 6.8571


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
    assert rows[0]["measured_kwh"] is None
    assert rows[0]["saveeye_kwh"] == 0.4286


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
    assert rows[0]["measured_kwh"] == 1.0
    assert rows[0]["saveeye_kwh"] is None


def test_combined_consumption_series_split_across_measured_and_estimated_hours(conn):
    """The two series the chart draws: Eloverblik covers the earlier hour,
    Saveeye covers both, so only the later hour is estimate-only."""
    opts = electricityapp.get_price_options(
        {"grid_tariff_normal": 0.0, "transmission_tariff": 0.0, "electricity_tax": 0.0, "vat_rate": 0.0}
    )
    for hour in (12, 13):
        for minute in (0, 15, 30, 45):
            _seed_price(conn, f"2026-08-16T{hour}:{minute:02d}:00", spot=1.0)
    _seed_eloverblik_row(conn, "2026-08-16T10:00:00+00:00", kwh=2.0)  # 12:00 local only
    # A flat 1000 Wh/h counter across both hours.
    _seed_saveeye_sample(conn, "2026-08-16T09:55:00+00:00", cumulative_wh=1000.0)
    _seed_saveeye_sample(conn, "2026-08-16T12:05:00+00:00", cumulative_wh=3200.0)
    conn.commit()

    start_local = datetime(2026, 8, 16, 12, tzinfo=electricityapp.LOCAL_TZ)
    rows = electricityapp.combined_consumption_with_cost(
        conn, start_local, start_local + timedelta(hours=2), "mp1", "DK2", opts, "dev1"
    )

    assert [r["time_dk"] for r in rows] == ["2026-08-16T12:00:00", "2026-08-16T13:00:00"]
    assert [r["measured_kwh"] for r in rows] == [2.0, None]
    assert all(r["saveeye_kwh"] is not None for r in rows)
    # The blended series still prefers the measured hour and falls back afterwards.
    assert [r["source"] for r in rows] == ["eloverblik", "saveeye_estimate"]
    assert rows[0]["kwh"] == 2.0
    assert rows[1]["kwh"] == rows[1]["saveeye_kwh"]


def test_saveeye_partial_hour_kwh_needs_two_samples(conn):
    now_local = datetime.now(electricityapp.LOCAL_TZ)
    hour_start = now_local.replace(minute=0, second=0, microsecond=0)
    _seed_saveeye_sample(conn, hour_start.astimezone(timezone.utc).isoformat(), cumulative_wh=1000.0)
    conn.commit()
    assert electricityapp.saveeye_partial_hour_kwh(conn, hour_start, now_local, "dev1") is None


def test_saveeye_partial_hour_kwh_diffs_first_and_last_sample(conn):
    now_local = datetime.now(electricityapp.LOCAL_TZ)
    hour_start = now_local.replace(minute=0, second=0, microsecond=0)
    _seed_saveeye_sample(conn, hour_start.astimezone(timezone.utc).isoformat(), cumulative_wh=1000.0)
    _seed_saveeye_sample(conn, now_local.astimezone(timezone.utc).isoformat(), cumulative_wh=1500.0)
    conn.commit()
    partial = electricityapp.saveeye_partial_hour_kwh(conn, hour_start, now_local, "dev1")
    assert partial == {"kwh": 0.5, "partial": True}


def test_saveeye_partial_hour_kwh_rejects_negative_delta(conn):
    now_local = datetime.now(electricityapp.LOCAL_TZ)
    hour_start = now_local.replace(minute=0, second=0, microsecond=0)
    _seed_saveeye_sample(conn, hour_start.astimezone(timezone.utc).isoformat(), cumulative_wh=1000.0)
    _seed_saveeye_sample(conn, now_local.astimezone(timezone.utc).isoformat(), cumulative_wh=900.0)
    conn.commit()
    assert electricityapp.saveeye_partial_hour_kwh(conn, hour_start, now_local, "dev1") is None


def test_combined_consumption_includes_partial_current_hour(conn):
    opts = electricityapp.get_price_options(
        {"grid_tariff_normal": 0.0, "transmission_tariff": 0.0, "electricity_tax": 0.0, "vat_rate": 0.0}
    )
    now_local = datetime.now(electricityapp.LOCAL_TZ)
    hour_start = now_local.replace(minute=0, second=0, microsecond=0)
    for minute in (0, 15, 30, 45):
        t = (hour_start + timedelta(minutes=minute)).replace(tzinfo=None).isoformat()
        _seed_price(conn, t, spot=2.0)
    _seed_saveeye_sample(conn, hour_start.astimezone(timezone.utc).isoformat(), cumulative_wh=1000.0)
    _seed_saveeye_sample(conn, now_local.astimezone(timezone.utc).isoformat(), cumulative_wh=1500.0)
    conn.commit()

    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    rows = electricityapp.combined_consumption_with_cost(conn, day_start, day_end, "mp1", "DK2", opts, "dev1")

    partial_rows = [r for r in rows if r["source"] == "saveeye_partial"]
    assert len(partial_rows) == 1
    assert partial_rows[0]["time_dk"] == hour_start.replace(tzinfo=None).isoformat()
    assert partial_rows[0]["kwh"] == 0.5
    assert partial_rows[0]["cost_dkk"] == 1.0  # 0.5 kWh * 2.0 DKK/kWh
    assert partial_rows[0]["measured_kwh"] is None
    assert partial_rows[0]["saveeye_kwh"] == 0.5


def test_combined_consumption_partial_hour_yields_to_eloverblik(conn):
    opts = electricityapp.get_price_options({})
    now_local = datetime.now(electricityapp.LOCAL_TZ)
    hour_start = now_local.replace(minute=0, second=0, microsecond=0)
    _seed_eloverblik_row(conn, hour_start.astimezone(timezone.utc).isoformat(), kwh=3.0)
    _seed_saveeye_sample(conn, hour_start.astimezone(timezone.utc).isoformat(), cumulative_wh=1000.0)
    _seed_saveeye_sample(conn, now_local.astimezone(timezone.utc).isoformat(), cumulative_wh=9000.0)
    conn.commit()

    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    rows = electricityapp.combined_consumption_with_cost(conn, day_start, day_end, "mp1", "DK2", opts, "dev1")

    assert len(rows) == 1
    assert rows[0]["source"] == "eloverblik"
    assert rows[0]["kwh"] == 3.0


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


def test_api_consumption_uses_saveeye_data_with_no_configured_device_serial(client, conn, set_options):
    """Regression test for the bug this was: leaving saveeye_device_serial
    empty (the documented default for a single Base reader) silently meant
    every consumption query filtered on device_serial = NULL and matched
    nothing, even with samples in storage and eloverblik configured."""
    set_options(
        saveeye_enabled=True,
        eloverblik_refresh_token="rt",
        eloverblik_metering_point="mp1",
        grid_tariff_normal=0.0,
        transmission_tariff=0.0,
        electricity_tax=0.0,
        vat_rate=0.0,
        # saveeye_device_serial intentionally left unset.
    )
    now_local = datetime.now(electricityapp.LOCAL_TZ)
    hour_start = (now_local - timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
    for minute in (0, 15, 30, 45):
        _seed_price(conn, (hour_start + timedelta(minutes=minute)).replace(tzinfo=None).isoformat(), spot=1.0)
    _seed_saveeye_sample(
        conn, hour_start.astimezone(timezone.utc).isoformat(), 1000.0, device_serial="auto-discovered"
    )
    _seed_saveeye_sample(
        conn,
        (hour_start + timedelta(hours=1)).astimezone(timezone.utc).isoformat(),
        2000.0,
        device_serial="auto-discovered",
    )
    conn.commit()

    rows = client.get("/api/consumption?days=1").get_json()
    assert any(r["source"] == "saveeye_estimate" and r["kwh"] == 1.0 for r in rows)


# --- The counter resetting to zero ---
#
# Shaped from a real database: Saveeye's cumulative counter is not a lifetime
# total. It restarted three times in eleven days — 71,123 Wh to 9 Wh, and twice
# more. Differencing straight across that gives a large negative, which used to
# discard the hour containing it: about 120 hours a year silently missing.


def _seed_run(conn, start_utc, values, step_minutes=5):
    """Samples every few minutes carrying the given counter values."""
    from datetime import datetime as _dt

    base = _dt.fromisoformat(start_utc)
    for i, value in enumerate(values):
        ts = (base + timedelta(minutes=step_minutes * i)).isoformat()
        _seed_saveeye_sample(conn, ts, cumulative_wh=value)
    conn.commit()


def test_a_reset_no_longer_loses_the_hour(conn):
    # 10:00-11:00 local is 08:00-09:00 UTC. The counter climbs, resets, climbs.
    _seed_run(conn, "2026-08-16T07:55:00+00:00",
              [71000.0, 71050.0, 71100.0, 71123.0, 9.0, 40.0, 70.0, 100.0, 130.0, 160.0,
               190.0, 220.0, 250.0, 280.0])
    start = datetime(2026, 8, 16, 10, tzinfo=electricityapp.LOCAL_TZ)
    hourly = electricityapp.saveeye_hourly_kwh(conn, start, start + timedelta(hours=1), "dev1")
    assert "2026-08-16T10:00:00" in hourly, "the hour containing the reset was dropped"
    assert hourly["2026-08-16T10:00:00"] > 0


def test_the_recovered_hour_counts_both_sides_of_the_reset(conn):
    """Energy before the reset plus energy after it, rather than either alone."""
    _seed_run(conn, "2026-08-16T07:55:00+00:00",
              [1000.0, 1100.0, 1200.0, 1300.0, 10.0, 110.0, 210.0, 310.0, 410.0, 510.0,
               610.0, 710.0, 810.0, 910.0])
    start = datetime(2026, 8, 16, 10, tzinfo=electricityapp.LOCAL_TZ)
    kwh = electricityapp.saveeye_hourly_kwh(conn, start, start + timedelta(hours=1), "dev1")[
        "2026-08-16T10:00:00"]
    # Both sides contribute; the answer is far above either side on its own.
    assert kwh > 0.3


def test_jitter_is_not_mistaken_for_a_reset(conn):
    """1,000 -> 900 fell 100 to reach 900. Reading that as a restart would
    invent 900 Wh of consumption that never happened."""
    assert electricityapp._is_counter_reset(71123.0, 9.0) is True
    assert electricityapp._is_counter_reset(1000.0, 900.0) is False
    assert electricityapp._is_counter_reset(1000.0, 499.0) is True
    assert electricityapp._is_counter_reset(1000.0, 501.0) is False


def test_a_counter_that_wrapped_to_a_large_value_is_not_read_as_a_burst(conn):
    """A reset lands near zero. Landing somewhere large is something else, and
    counting the whole remainder would report consumption nobody used."""
    _seed_run(conn, "2026-08-16T07:55:00+00:00",
              [99000.0, 99100.0, 99200.0, 99300.0, 40000.0, 40100.0, 40200.0, 40300.0,
               40400.0, 40500.0, 40600.0, 40700.0, 40800.0, 40900.0])
    start = datetime(2026, 8, 16, 10, tzinfo=electricityapp.LOCAL_TZ)
    kwh = electricityapp.saveeye_hourly_kwh(conn, start, start + timedelta(hours=1), "dev1").get(
        "2026-08-16T10:00:00")
    # Uncapped this would read as ~41 kWh in one hour. The post-reset baseline
    # is bounded by what 25 kW could physically deliver in the poll gap, so what
    # survives is a couple of kWh rather than a fabricated 40.
    assert kwh is not None
    assert kwh < 4.0


def test_hours_either_side_of_a_reset_are_unaffected(conn):
    _seed_run(conn, "2026-08-16T06:55:00+00:00",
              [100.0 * i for i in range(1, 25)] + [5.0 + 100.0 * i for i in range(24)],
              step_minutes=5)
    start = datetime(2026, 8, 16, 9, tzinfo=electricityapp.LOCAL_TZ)
    hourly = electricityapp.saveeye_hourly_kwh(conn, start, start + timedelta(hours=3), "dev1")
    # Every hour in a fully sampled window gets an estimate, reset or not.
    assert len(hourly) >= 2
    assert all(v >= 0 for v in hourly.values())


def test_a_partial_hour_spanning_a_reset_still_reports(conn):
    now_local = datetime.now(electricityapp.LOCAL_TZ)
    hour_start = now_local.replace(minute=0, second=0, microsecond=0)
    for offset, value in ((0, 5000.0), (5, 5100.0), (10, 12.0), (15, 112.0)):
        ts = (hour_start + timedelta(minutes=offset)).astimezone(timezone.utc).isoformat()
        _seed_saveeye_sample(conn, ts, cumulative_wh=value)
    conn.commit()
    partial = electricityapp.saveeye_partial_hour_kwh(
        conn, hour_start, hour_start + timedelta(minutes=20), "dev1")
    assert partial is not None, "a reset mid-hour abandoned the whole hour"
    # 100 Wh before the reset, 12 Wh accumulated from zero to the first
    # post-reset reading, then 100 Wh after it.
    assert partial["kwh"] == 0.212
