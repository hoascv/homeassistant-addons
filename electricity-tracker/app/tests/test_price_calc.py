from datetime import datetime

import app as electricityapp


def _opts(**overrides):
    base = electricityapp.get_price_options({})
    base.update(overrides)
    return base


def test_time_in_window_simple():
    assert electricityapp._time_in_window("14:30", "00:00", "06:00") is False
    assert electricityapp._time_in_window("03:00", "00:00", "06:00") is True
    assert electricityapp._time_in_window("06:00", "00:00", "06:00") is False  # end exclusive


def test_time_in_window_wraps_midnight():
    assert electricityapp._time_in_window("23:30", "22:00", "06:00") is True
    assert electricityapp._time_in_window("05:59", "22:00", "06:00") is True
    assert electricityapp._time_in_window("12:00", "22:00", "06:00") is False


def test_time_in_window_missing_bounds():
    assert electricityapp._time_in_window("12:00", "", "06:00") is False
    assert electricityapp._time_in_window("12:00", "22:00", "") is False


def test_grid_tariff_band_low_window():
    opts = _opts(grid_tariff_low=0.1, grid_tariff_high=0.5, grid_tariff_normal=0.3)
    dt = datetime(2026, 8, 16, 3, 0)  # 03:00, inside default low window
    assert electricityapp._grid_tariff_band(dt, opts) == "low"


def test_grid_tariff_band_high_requires_weekday_and_month():
    opts = _opts(
        grid_tariff_high_weekdays="1,2,3,4,5",  # Mon-Fri
        grid_tariff_high_months="10,11,12,1,2,3",  # winter
    )
    # Sunday in August: inside the high time window, but wrong weekday and month.
    sunday_in_august = datetime(2026, 8, 16, 18, 0)
    assert sunday_in_august.isoweekday() == 7
    assert electricityapp._grid_tariff_band(sunday_in_august, opts) == "normal"

    # Tuesday in January, same time window: both filters satisfied.
    tuesday_in_january = datetime(2026, 1, 20, 18, 0)
    assert tuesday_in_january.isoweekday() == 2
    assert electricityapp._grid_tariff_band(tuesday_in_january, opts) == "high"


def test_grid_tariff_band_empty_filters_mean_every_day():
    opts = _opts(grid_tariff_high_weekdays="", grid_tariff_high_months="")
    sunday = datetime(2026, 8, 16, 18, 0)
    assert electricityapp._grid_tariff_band(sunday, opts) == "high"


def test_compute_total_price_applies_vat_to_everything():
    opts = _opts(
        grid_tariff_normal=0.3,
        transmission_tariff=0.15,
        electricity_tax=0.008,
        vat_rate=0.25,
    )
    dt = datetime(2026, 8, 16, 12, 0)  # normal band
    total, components = electricityapp.compute_total_price(1.0, dt, opts)
    expected = (1.0 + 0.3 + 0.15 + 0.008) * 1.25
    assert total == expected
    assert components["grid_tariff_band"] == "normal"
    assert components["vat_rate"] == 0.25


def test_compute_total_price_handles_negative_spot():
    opts = _opts(grid_tariff_normal=0.3, transmission_tariff=0.1, electricity_tax=0.0, vat_rate=0.25)
    dt = datetime(2026, 8, 16, 12, 0)
    total, _ = electricityapp.compute_total_price(-0.5, dt, opts)
    assert total == (-0.5 + 0.3 + 0.1) * 1.25


def test_parse_int_set():
    assert electricityapp._parse_int_set("1, 2,3") == {1, 2, 3}
    assert electricityapp._parse_int_set("") == set()
    assert electricityapp._parse_int_set(None) == set()
    assert electricityapp._parse_int_set("1,x,3") == {1, 3}


def test_get_price_options_falls_back_on_bad_types():
    opts = electricityapp.get_price_options({"grid_tariff_low": "not-a-number", "price_area": "XX"})
    assert opts["grid_tariff_low"] == 0.0
    assert opts["price_area"] == "DK2"


# --- Warning when the tariffs were never configured ---
#
# The arithmetic is correct with them at zero, which is what makes this worth
# saying out loud: the dashboard shows a confident, precise, badly wrong number
# and nothing on screen suggests the cause is a gap in configuration.


def test_untouched_tariffs_are_flagged():
    warning = electricityapp.price_config_warning(electricityapp.get_price_options({}))
    assert warning is not None
    assert "transmission_tariff" in warning["missing"]


def test_a_combined_transport_line_counts_as_configured():
    """Many Danish suppliers bill the grid company's tariff and Energinet's as
    one line. Putting the whole figure in transmission_tariff and leaving the
    grid bands at zero is the correct configuration for those, and used to be
    nagged about forever."""
    opts = electricityapp.get_price_options({"transmission_tariff": 0.2965})
    assert electricityapp.price_config_warning(opts) is None


def test_grid_bands_alone_also_count_as_configured():
    opts = electricityapp.get_price_options({"grid_tariff_normal": 0.35})
    assert electricityapp.price_config_warning(opts) is None


def test_only_a_completely_unset_tariff_warns():
    warning = electricityapp.price_config_warning(electricityapp.get_price_options({}))
    assert warning is not None
    assert "combined line" in warning["detail"]


def test_a_fully_configured_setup_is_not_nagged():
    opts = electricityapp.get_price_options(
        {"grid_tariff_normal": 0.35, "transmission_tariff": 0.09}
    )
    assert electricityapp.price_config_warning(opts) is None


def test_any_one_grid_band_counts_as_configured():
    """A grid company with only a peak rate modelled is configured, not empty."""
    opts = electricityapp.get_price_options({"grid_tariff_high": 0.6, "transmission_tariff": 0.09})
    assert electricityapp.price_config_warning(opts) is None


def test_neither_half_alone_is_treated_as_missing():
    """Superseded by the combined-line case above: either option on its own is
    a complete configuration, because suppliers differ in how they bill it."""
    assert electricityapp.price_config_warning(
        electricityapp.get_price_options({"grid_tariff_normal": 0.35})) is None
    assert electricityapp.price_config_warning(
        electricityapp.get_price_options({"transmission_tariff": 0.09})) is None


def test_the_warning_names_where_to_fix_it():
    warning = electricityapp.price_config_warning(electricityapp.get_price_options({}))
    assert "Configuration tab" in warning["detail"]


def test_summary_carries_the_warning(conn, client):
    assert client.get("/api/summary").get_json()["price_config_warning"] is not None


def test_summary_drops_the_warning_once_tariffs_are_set(conn, client, set_options):
    set_options(grid_tariff_normal=0.35, transmission_tariff=0.09)
    assert client.get("/api/summary").get_json()["price_config_warning"] is None
