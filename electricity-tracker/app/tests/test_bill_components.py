"""The two parts of a bill that are not a per-kWh market price.

Shaped from a real Danish invoice: of 2,254 kr over 1,636 kWh, the spot-linked
energy line was 57.5%, the pass-through tariff 26.9%, and a flat "Transport
fast" standing charge 14.9%. Without the last two the add-on was reporting
about 58% of what was actually being paid.
"""
from datetime import datetime, timedelta

import app as electricityapp


def _opts(**overrides):
    base = {"grid_tariff_normal": 0.0, "transmission_tariff": 0.0,
            "electricity_tax": 0.0, "vat_rate": 0.25}
    base.update(overrides)
    return electricityapp.get_price_options(base)


# --- the supplier's margin on top of spot ---


def test_the_markup_is_added_to_spot_before_vat(conn):
    opts = _opts(supplier_markup=0.05, electricity_tax=0.0)
    total, parts = electricityapp.compute_total_price(1.00, datetime(2026, 8, 16, 12), opts)
    assert parts["supplier_markup_dkk_kwh"] == 0.05
    assert total == (1.00 + 0.05) * 1.25


def test_the_markup_is_reported_apart_from_spot(conn):
    """The market price is a fact and the margin is a contract. Folding one
    into the other would make a bill impossible to check against this."""
    _, parts = electricityapp.compute_total_price(0.40, datetime(2026, 8, 16, 12), _opts(supplier_markup=0.08))
    assert parts["spot_dkk_kwh"] == 0.40
    assert parts["supplier_markup_dkk_kwh"] == 0.08


def test_no_markup_configured_changes_nothing(conn):
    plain = electricityapp.compute_total_price(0.5, datetime(2026, 8, 16, 12), _opts())[0]
    zero = electricityapp.compute_total_price(0.5, datetime(2026, 8, 16, 12), _opts(supplier_markup=0.0))[0]
    assert plain == zero


def test_the_markup_reaches_the_stored_quarter_prices(conn):
    for minute in (0, 15, 30, 45):
        conn.execute("INSERT INTO prices (time_dk, price_area, spot_price_dkk_kwh, fetched_at) "
                     "VALUES (?, 'DK2', 1.0, 'x')", (f"2026-08-16T12:{minute:02d}:00",))
    conn.commit()
    start = datetime(2026, 8, 16, 12, tzinfo=electricityapp.LOCAL_TZ)
    rows = electricityapp.quarter_prices_with_total(conn, start, start + timedelta(hours=1),
                                                    "DK2", _opts(supplier_markup=0.10))
    assert all(r["total_dkk_kwh"] == round((1.0 + 0.10) * 1.25, 4) for r in rows)


# --- the standing charge ---


def test_the_standing_charge_accrues_by_the_day(conn):
    """A month's charge over the days in that month, not dropped whole on the
    1st — otherwise a month-to-date total is unusable until the month ends."""
    opts = _opts(fixed_charge_monthly=310.0)  # 31 days in August -> 10 kr/day
    start = datetime(2026, 8, 1, tzinfo=electricityapp.LOCAL_TZ)
    one_day = electricityapp.fixed_charge_for_window(opts, start, start + timedelta(days=1))
    assert one_day == round(10.0 * 1.25, 4)
    week = electricityapp.fixed_charge_for_window(opts, start, start + timedelta(days=7))
    assert week == round(70.0 * 1.25, 4)


def test_a_whole_month_accrues_exactly_the_month(conn):
    opts = _opts(fixed_charge_monthly=300.0)
    start = datetime(2026, 8, 1, tzinfo=electricityapp.LOCAL_TZ)
    end = datetime(2026, 9, 1, tzinfo=electricityapp.LOCAL_TZ)
    assert electricityapp.fixed_charge_for_window(opts, start, end) == round(300.0 * 1.25, 4)


def test_a_short_month_has_a_higher_daily_rate(conn):
    """Charged per month, not per day: February's days each carry more."""
    opts = _opts(fixed_charge_monthly=280.0)
    feb = datetime(2026, 2, 1, tzinfo=electricityapp.LOCAL_TZ)
    aug = datetime(2026, 8, 1, tzinfo=electricityapp.LOCAL_TZ)
    assert (electricityapp.fixed_charge_for_window(opts, feb, feb + timedelta(days=1))
            > electricityapp.fixed_charge_for_window(opts, aug, aug + timedelta(days=1)))


def test_vat_is_applied_to_the_standing_charge(conn):
    opts = _opts(fixed_charge_monthly=310.0, vat_rate=0.25)
    start = datetime(2026, 8, 1, tzinfo=electricityapp.LOCAL_TZ)
    assert electricityapp.fixed_charge_for_window(opts, start, start + timedelta(days=1)) == 12.5


def test_no_standing_charge_configured_costs_nothing(conn):
    start = datetime(2026, 8, 1, tzinfo=electricityapp.LOCAL_TZ)
    assert electricityapp.fixed_charge_for_window(_opts(), start, start + timedelta(days=30)) == 0.0


def test_an_empty_window_accrues_nothing(conn):
    opts = _opts(fixed_charge_monthly=300.0)
    start = datetime(2026, 8, 1, tzinfo=electricityapp.LOCAL_TZ)
    assert electricityapp.fixed_charge_for_window(opts, start, start) == 0.0


# --- reproducing the real invoice ---


def test_the_real_invoice_reconciles(conn):
    """1,636 kWh at the invoice's own rates must come to its own total.

    Energy 0.6333, pass-through tariff 0.2965, elafgift 0.0080, all per kWh and
    ex VAT, plus 268.59 kr standing charge for the quarter, plus 25% VAT.
    """
    kwh = 1636
    per_kwh = 0.6333 + 0.2965 + 0.0080
    energy = kwh * per_kwh
    total = (energy + 268.59) * 1.25

    # The invoice states 2254.18 kr and 137.78 øre/kWh, and also states that it
    # calculated with more decimals than it printed. So the rates above are
    # rounded to four places and the reconciliation lands within a krone, not
    # exactly — which is the strongest claim the printed figures support.
    assert abs(total - 2254.18) < 1.0
    assert abs(total / kwh * 100 - 137.78) < 0.1

    # The same standing charge through the add-on: a quarter is three months.
    opts = _opts(fixed_charge_monthly=268.59 / 3)
    start = datetime(2026, 3, 1, tzinfo=electricityapp.LOCAL_TZ)
    end = datetime(2026, 6, 1, tzinfo=electricityapp.LOCAL_TZ)
    assert round(electricityapp.fixed_charge_for_window(opts, start, end), 2) == round(268.59 * 1.25, 2)
