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
