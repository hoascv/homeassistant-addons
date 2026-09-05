"""The seam between the template, app.js and the stylesheet.

No JS runner here, so this is the little that can be checked as text: that a
card lives on the page it is meant to, that an id the script fills exists, and
that a class it emits is styled. Cheap, and it catches the renames.
"""
import pathlib

APP_DIR = pathlib.Path(__file__).resolve().parents[1]
HTML = (APP_DIR / "templates" / "index.html").read_text(encoding="utf-8")
JS = (APP_DIR / "static" / "app.js").read_text(encoding="utf-8")
CSS = (APP_DIR / "static" / "style.css").read_text(encoding="utf-8")


def test_the_finances_card_lives_on_the_trends_tab():
    """It is something you look at, not something you do — and the home page is
    for the six logging buttons you came to press."""
    trends = HTML[HTML.index('id="page-trends"'):]
    home = HTML[HTML.index('id="page-home"'):HTML.index('id="page-ferment"')]
    assert '<section class="finances">' in trends
    assert '<section class="finances">' not in home


def test_net_with_savings_has_its_own_tile_beside_the_plain_net():
    """One is money that moved, the other is money that moved plus an estimate.
    Folding them together would lose the distinction."""
    for suffix in ("month", "total"):
        assert f'id="stat-net-{suffix}"' in HTML
        assert f'id="stat-net-savings-{suffix}"' in HTML
    assert "Net incl. savings" in HTML


def test_net_with_savings_is_coloured_on_its_own_sign():
    """A flock in the red on sales alone can still be ahead once the eggs you
    ate are counted, and the tile has to be able to say so."""
    block = JS[JS.index("stat-net-savings-month"):]
    assert 'classList.toggle("stat-positive", value >= 0)' in block[:600]


def test_the_five_money_tiles_can_wrap():
    """Five across does not fit a phone, and a figure squeezed into three lines
    is not one you can read at a glance."""
    rule = CSS[CSS.index(".summary-secondary {"):]
    assert "flex-wrap: wrap" in rule[:400]


def test_the_finance_chart_is_wired_and_styled():
    for element in ("finance-chart-wrap", "finance-chart-empty", "finance-expand-btn"):
        assert f'id="{element}"' in HTML, f"the chart has no {element}"
    for name in (".trends-swatch-revenue", ".trends-swatch-costs",
                 ".trends-swatch-net", ".chart-zero-line"):
        assert name in CSS, f"{name} is emitted but nothing styles it"


def test_the_finance_chart_uses_a_signed_axis():
    """A month whose costs beat its revenue has a net below the line. The
    counted charts span 0..max, which is right for eggs and wrong for money."""
    fn = JS[JS.index("function buildFinanceSvg("):JS.index("\n}\n", JS.index("function buildFinanceSvg("))]
    assert "chartYAxisSigned" in fn
    assert "Math.min(0" in fn, "the floor should follow the data below zero"


def test_costs_are_told_apart_by_more_than_colour():
    """Revenue green against costs red measures ΔE 6.0 for a deuteranope —
    distinguishable to most readers and not to them, so the line style has to
    carry the identity too."""
    fn = JS[JS.index("function buildFinanceSvg("):JS.index("\n}\n", JS.index("function buildFinanceSvg("))]
    assert "dashed: true" in fn
    swatch = CSS[CSS.index(".trends-swatch-costs"):]
    assert "dashed" in swatch[:200], "the legend swatch should teach the dash"


def test_an_empty_ledger_is_not_drawn_as_a_flat_line():
    """A line along the axis reads as a measured result rather than as nothing
    logged yet."""
    fn = JS[JS.index("function renderFinanceChart("):]
    assert "empty.hidden = moved > 0" in fn[:700]


def test_the_finance_chart_shares_the_trends_request():
    """Two requests would let the two charts disagree about which months they
    are showing."""
    assert "renderFinanceChart(data)" in JS
    assert 'fetch("api/finance' not in JS and "fetch(`api/finance" not in JS
