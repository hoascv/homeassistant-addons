"""Reading an amount off a photographed receipt.

The OCR happens elsewhere; this is text in, candidates out. Which means the
hard part — deciding which of the eleven numbers on a till receipt is the one
you actually paid — can be tested against real receipt shapes without a
photograph or a Tesseract install.

The traps are specific and worth naming, because each one is a plausible number
in the right place: VAT sits near the bottom in a line of its own; cash tendered
is larger than the total; the change given back is larger still on a small
purchase; and the date at the top parses as a four-figure sum.
"""
import datetime

import pytest

import receipts

NETTO = """NETTO
Sundbyvester Plads 1
02-09-2026  14:32
Tlf 43 55 66 77

Layers pellets 20kg     249,95
Halm bigballe            89,50
Rabat                   -10,00

Subtotal                329,45
Moms 25%                 65,89
I ALT                   329,45
Kontant                 500,00
Byttepenge              170,55
"""


# --- reading Danish numbers ---------------------------------------------------


@pytest.mark.parametrize("line,expected", [
    ("I ALT 329,45", [329.45]),
    ("I ALT 1.234,56", [1234.56]),
    ("Total 1234.56", [1234.56]),
    # A lone dot with three digits after it is grouping, not a decimal point —
    # "1.234" on a Danish receipt is one thousand two hundred and thirty-four.
    ("Total 1.234", [1234.0]),
    ("Total 89", [89.0]),
])
def test_amounts_are_read_the_danish_way(line, expected):
    assert receipts.amounts_in(line) == expected


def test_a_quantity_is_not_a_price():
    """"20kg" is what you bought, not what it cost."""
    assert receipts.amounts_in("Layers pellets 20kg 249,95") == [249.95]


def test_a_date_or_a_time_is_not_a_price():
    """The year is the trap: 2026 is a perfectly plausible sum."""
    assert receipts.amounts_in("02-09-2026  14:32") == []
    assert receipts.amounts_in("2026-09-02 14:32:07") == []


def test_a_rate_is_not_a_price():
    assert 25.0 not in receipts.amounts_in("Moms 25% 65,89")


# --- picking the total --------------------------------------------------------


def test_the_total_is_found_on_a_real_receipt():
    assert receipts.find_total(NETTO) == 329.45


@pytest.mark.parametrize("marker", ["I ALT", "Total", "At betale", "Beløb", "Sum"])
def test_the_words_that_mean_total(marker):
    assert receipts.find_total(f"Item 10,00\n{marker} 42,50") == 42.50


def test_vat_is_never_the_total():
    """It sits near the bottom in a line of its own and, at 25%, is a
    plausible-looking figure a fifth the size of the real one."""
    assert receipts.find_total("Moms 25%  65,89\nI ALT  329,45") == 329.45


def test_cash_tendered_is_never_the_total():
    """It is larger than the total, so a "biggest number wins" rule picks it."""
    assert receipts.find_total(NETTO) != 500.00


def test_change_given_is_never_the_total():
    assert receipts.find_total(NETTO) != 170.55


def test_a_subtotal_loses_to_the_total():
    assert receipts.find_total("Subtotal 100,00\nI ALT 125,00") == 125.00


def test_the_last_total_wins():
    """A receipt that says "total" twice means the second one."""
    assert receipts.find_total("Total 50,00\nrest\nTotal 75,00") == 75.00


def test_an_unmarked_receipt_falls_back_to_the_largest():
    """No total line at all — the biggest number is nearly always it, and a
    guess beats refusing when the human confirms it anyway."""
    assert receipts.find_total("Foder 249,95\nHalm 89,50") == 249.95


def test_nothing_that_looks_like_money_reports_nothing():
    """Better than an invented number in a form somebody is about to save."""
    assert receipts.find_total("blurred beyond reading") is None
    assert receipts.read("")["amount"] is None


# --- the alternatives offered -------------------------------------------------


def test_the_best_guess_comes_first_and_the_rest_are_prices():
    """The runner-up should be the next plausible price, not the shop's phone
    number — the guess is wrong often enough to need one tap to the right one."""
    found = receipts.candidates(NETTO)
    assert found[0] == 329.45
    assert found[1:] == [249.95, 89.5]


def test_the_choices_do_not_include_the_year_or_the_phone_number():
    found = receipts.candidates(NETTO)
    assert 2026.0 not in found
    assert all(value not in found for value in (43.0, 55.0, 66.0, 77.0))


# --- the date -----------------------------------------------------------------


@pytest.mark.parametrize("line,expected", [
    ("02-09-2026", "2026-09-02"),
    ("02/09/2026", "2026-09-02"),
    ("02.09.2026", "2026-09-02"),
    ("2026-09-02", "2026-09-02"),
    ("02-09-26", "2026-09-02"),
])
def test_dates_in_the_formats_tills_print(line, expected):
    assert receipts.find_date(line, today=datetime.date(2026, 9, 2)) == expected


def test_a_date_in_the_future_is_a_misread():
    assert receipts.find_date("02-09-2031", today=datetime.date(2026, 9, 2)) is None


def test_a_date_from_before_there_were_chickens_is_too():
    assert receipts.find_date("02-09-1999", today=datetime.date(2026, 9, 2)) is None


def test_an_impossible_date_is_skipped_not_fatal():
    assert receipts.find_date("32-13-2026", today=datetime.date(2026, 9, 2)) is None


# --- the vendor ---------------------------------------------------------------


def test_the_shop_name_is_taken_from_the_top():
    """Till receipts put it there in the largest type, which is also the part
    OCR reads most reliably."""
    assert receipts.find_vendor(NETTO) == "NETTO"


def test_a_receipt_of_pure_numbers_names_no_vendor():
    assert receipts.find_vendor("123\n456") is None


# --- the whole reading --------------------------------------------------------


def test_a_real_receipt_end_to_end():
    found = receipts.read(NETTO, today=datetime.date(2026, 9, 2))
    assert found["amount"] == 329.45
    assert found["date"] == "2026-09-02"
    assert found["vendor"] == "NETTO"


def test_ocr_noise_does_not_raise():
    """Tesseract on a creased thermal receipt returns something closer to this
    than to the sample above, and the form still has to open."""
    noise = "|||  ]]  \\n  N€TT0  \\n  ,,,  \\n  1O0O,,,OO  \\n\\x00\\x01"
    found = receipts.read(noise)
    assert isinstance(found, dict)
    assert "amount" in found


# --- the endpoint -------------------------------------------------------------


def test_scanning_is_refused_where_the_engine_is_absent(client, monkeypatch):
    """armv7 gets no OpenCV and no Tesseract. A 503 saying so beats a 500, and
    the button is hidden there anyway."""
    import app as coop
    monkeypatch.setattr(coop, "TESSERACT_AVAILABLE", False)
    response = client.post("/api/expenses/scan",
                           json={"photo": "data:image/jpeg;base64,/9j/4AAQ"})
    assert response.status_code == 503
    assert "not available" in response.get_json()["error"]


def test_an_unreadable_upload_is_a_400_not_a_500(client, monkeypatch):
    import app as coop
    monkeypatch.setattr(coop, "TESSERACT_AVAILABLE", True)
    monkeypatch.setattr(coop, "OPENCV_AVAILABLE", True)
    response = client.post("/api/expenses/scan", json={"photo": "not a data uri"})
    assert response.status_code == 400


def test_a_reading_is_offered_never_recorded(client, monkeypatch):
    """The whole safety argument. OCR on a creased thermal receipt is wrong
    often enough that logging its guess unattended would put bad numbers in the
    books faster than typing them by hand would."""
    import app as coop
    before = len(client.get("/api/entries").get_json())
    monkeypatch.setattr(coop, "_ocr_receipt", lambda photo: (NETTO, None))
    body = client.post("/api/expenses/scan",
                       json={"photo": "data:image/jpeg;base64,/9j/4AAQ"}).get_json()

    assert body["amount"] == 329.45
    assert body["found_anything"] is True
    assert len(client.get("/api/entries").get_json()) == before, "it logged something"


def test_a_receipt_with_no_amount_says_so(client, monkeypatch):
    import app as coop
    monkeypatch.setattr(coop, "_ocr_receipt", lambda photo: ("blurred", None))
    body = client.post("/api/expenses/scan",
                       json={"photo": "data:image/jpeg;base64,/9j/4AAQ"}).get_json()
    assert body["found_anything"] is False
    assert body["amount"] is None


def test_the_debug_page_reports_whether_scanning_is_possible(client):
    body = client.get("/api/debug").get_json()
    assert "tesseract_available" in body


# --- the form -----------------------------------------------------------------


def _static(name):
    import os
    sub = "templates" if name.endswith(".html") else "static"
    with open(os.path.join(os.path.dirname(__file__), "..", sub, name),
              encoding="utf-8") as handle:
        return handle.read()


def test_the_button_is_hidden_where_scanning_is_impossible():
    """A button that can only ever answer "not available on this architecture"
    is worse than no button."""
    js = _static("app.js")
    fn = js[js.index("async function revealReceiptScan("):]
    fn = fn[:fn.index("\n}\n")]
    assert "block.hidden = !(await receiptScanningAvailable())" in fn


def test_the_scan_fills_the_form_rather_than_saving():
    js = _static("app.js")
    fn = js[js.index("function applyReceipt("):js.index("document.addEventListener(\"click\", (event) => {\n  const chip")]
    assert "form.elements.cost.value" in fn
    assert "fetch(\"api/entries\"" not in fn


def test_the_photo_is_shrunk_before_upload():
    """A phone's 12MP JPEG is several megabytes of detail Tesseract does not
    use, over somebody's home wifi."""
    js = _static("app.js")
    assert "shrinkImage(file, 1600)" in js


def test_a_non_breaking_space_groups_thousands():
    """Some tills print U+00A0 between the thousands. The pattern matches one,
    so the strip has to remove one — otherwise float() is handed "1 234"
    and the whole scan raises instead of returning a number."""
    assert receipts.amounts_in("I ALT 1 234,56") == [1234.56]
    assert receipts.amounts_in("I ALT 1 234,56") == [1234.56]
