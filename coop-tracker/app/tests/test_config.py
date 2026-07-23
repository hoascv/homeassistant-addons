import app as coopapp


def test_default_currency_is_dkk(options_path):
    assert coopapp.get_currency() == coopapp.CURRENCIES["DKK"]


def test_currency_override(set_options):
    set_options(currency="USD")
    currency = coopapp.get_currency()
    assert currency["symbol"] == "$"
    assert currency["position"] == "prefix"


def test_unknown_currency_code_falls_back_to_default(set_options):
    set_options(currency="XXX")
    assert coopapp.get_currency() == coopapp.CURRENCIES["DKK"]


def test_reminder_config_defaults(options_path):
    cfg = coopapp.get_reminder_config()
    assert cfg == {
        "enabled": False,
        "check_time": "18:00",
        "threshold_days": 2,
        "notify_service": "",
    }


def test_reminder_config_overrides_and_trims_notify_service(set_options):
    set_options(
        reminder_enabled=True,
        reminder_check_time="07:30",
        reminder_threshold_days=5,
        notify_service=" mobile_app_phone ",
    )
    cfg = coopapp.get_reminder_config()
    assert cfg["enabled"] is True
    assert cfg["check_time"] == "07:30"
    assert cfg["threshold_days"] == 5
    assert cfg["notify_service"] == "mobile_app_phone"


def test_ha_sensors_disabled_by_default(options_path):
    assert coopapp.get_ha_sensors_enabled() is False


def test_ha_sensors_enabled_when_set(set_options):
    set_options(ha_sensors_enabled=True)
    assert coopapp.get_ha_sensors_enabled() is True


def test_options_missing_file_returns_defaults(options_path):
    # the options_path fixture points at a path that doesn't exist yet,
    # matching a freshly-installed add-on before its first Configuration save
    assert coopapp._read_options() == {}


def test_egg_vision_config_defaults(options_path):
    cfg = coopapp.get_egg_vision_config()
    assert cfg == {"enabled": False, "coin_diameter_mm": 24.5}


def test_egg_vision_config_overrides(set_options):
    set_options(egg_vision_enabled=True, egg_vision_coin_diameter_mm=21.21)
    cfg = coopapp.get_egg_vision_config()
    assert cfg["enabled"] is True
    assert cfg["coin_diameter_mm"] == 21.21
