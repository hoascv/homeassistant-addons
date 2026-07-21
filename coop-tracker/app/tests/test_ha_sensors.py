import time

import app as coopapp


def test_push_ha_sensors_noop_when_disabled(conn, options_path, fake_ha_server):
    coopapp._push_ha_sensors(conn)
    assert fake_ha_server == []


def test_push_ha_sensors_noop_without_supervisor_token(conn, set_options, monkeypatch):
    set_options(ha_sensors_enabled=True)
    monkeypatch.setattr(coopapp, "SUPERVISOR_TOKEN", None)
    coopapp._push_ha_sensors(conn)  # must not raise


def test_push_ha_sensors_posts_all_expected_entities(client, set_options, fake_ha_server, wait_until):
    set_options(ha_sensors_enabled=True)
    client.post("/api/log", json={"type": "egg", "count": 5})

    # the push runs in a background thread (see app.py's
    # _push_ha_sensors_async) so it isn't guaranteed done the instant the
    # request returns
    wait_until(lambda: len(fake_ha_server) >= 9)

    paths = {c["path"] for c in fake_ha_server}
    expected = {
        "/states/sensor.coop_tracker_eggs_today",
        "/states/sensor.coop_tracker_eggs_week",
        "/states/sensor.coop_tracker_eggs_available",
        "/states/sensor.coop_tracker_last_cleaning",
        "/states/sensor.coop_tracker_last_feeding",
        "/states/sensor.coop_tracker_revenue_month",
        "/states/sensor.coop_tracker_cost_month",
        "/states/sensor.coop_tracker_net_month",
        "/states/binary_sensor.coop_tracker_eggs_overdue",
    }
    assert expected.issubset(paths)

    eggs_today = next(
        c for c in fake_ha_server if c["path"] == "/states/sensor.coop_tracker_eggs_today"
    )
    assert eggs_today["body"]["state"] == 5
    assert eggs_today["body"]["attributes"]["unit_of_measurement"] == "eggs"


def test_push_ha_sensors_updates_after_write(client, set_options, fake_ha_server, wait_until):
    set_options(ha_sensors_enabled=True)
    client.post("/api/log", json={"type": "egg", "count": 1})
    assert wait_until(lambda: len(fake_ha_server) > 0)  # pushed on the write path
    first_count = len(fake_ha_server)

    client.post("/api/log", json={"type": "egg", "count": 1})
    assert wait_until(lambda: len(fake_ha_server) > first_count)  # pushed again on the second write


def test_eggs_overdue_binary_sensor_on_when_never_collected(conn, set_options, fake_ha_server):
    set_options(ha_sensors_enabled=True, reminder_threshold_days=1)
    coopapp._push_ha_sensors(conn)
    overdue = next(
        c for c in fake_ha_server if c["path"] == "/states/binary_sensor.coop_tracker_eggs_overdue"
    )
    assert overdue["body"]["state"] == "on"


def test_eggs_overdue_binary_sensor_off_when_recent(client, set_options, fake_ha_server, wait_until):
    set_options(ha_sensors_enabled=True, reminder_threshold_days=2)
    client.post("/api/log", json={"type": "egg", "count": 1})
    wait_until(
        lambda: any(
            c["path"] == "/states/binary_sensor.coop_tracker_eggs_overdue" for c in fake_ha_server
        )
    )
    overdue = next(
        c for c in fake_ha_server if c["path"] == "/states/binary_sensor.coop_tracker_eggs_overdue"
    )
    assert overdue["body"]["state"] == "off"


def test_log_endpoint_does_not_block_on_unreachable_home_assistant(client, set_options, monkeypatch):
    # A non-routable address: connection attempts to it just hang until
    # they time out, rather than failing immediately — simulating Home
    # Assistant/Supervisor being briefly unreachable.
    set_options(ha_sensors_enabled=True)
    monkeypatch.setattr(coopapp, "HA_API_BASE", "http://10.255.255.1")
    monkeypatch.setattr(coopapp, "SUPERVISOR_TOKEN", "fake-token")

    start = time.time()
    res = client.post("/api/log", json={"type": "egg", "count": 1})
    elapsed = time.time() - start

    assert res.status_code == 201
    assert elapsed < 1.0  # the HA push runs in the background, not inline
