"""Reading an amount and a date out of a photographed receipt.

The OCR itself happens in app.py; everything here is text in, candidates out,
so the hard part — deciding which of the eleven numbers on a receipt is the one
you actually paid — can be tested without a photograph or a Tesseract install.

**This assists, it never decides.** Every figure it produces is offered to the
expense form for a human to confirm, because a photographed till receipt is
creased, thermal, and half in shadow, and OCR on one is wrong often enough that
silently logging its guess would put bad numbers in the books faster than
typing them by hand would.

Danish conventions throughout: `1.234,56` is one thousand two hundred and
thirty-four kroner fifty-six, and the words that mark a total are "i alt" and
"at betale" as often as "total".
"""
import datetime
import re

# Lines that name what you paid. Ordered by how strongly they mean it: "at
# betale" is unambiguous, "total" gets used for subtotals too.
TOTAL_MARKERS = (
    ("at betale", 10),
    ("i alt", 9),
    ("ialt", 9),
    ("total", 7),
    ("beløb", 6),
    ("belob", 6),
    ("sum", 5),
    ("amount due", 9),
    ("to pay", 9),
)

# Lines whose number is emphatically not the total, however large. VAT is the
# classic trap: it sits near the bottom in a line of its own, and on a 25% rate
# it is a plausible-looking figure a fifth the size of the real one.
DISQUALIFYING = (
    "moms",          # VAT
    "subtotal",
    "delsum",
    "byttepenge",    # change given
    "retur",
    "kontant",       # cash tendered — usually more than the total
    "modtaget",
    "rabat",         # discount
    "vat",
    "change",
)

# 1.234,56 / 1234,56 / 1234.56 / 1234 — with the separators Danish receipts
# actually use, including the thin spaces some tills print for thousands.
_AMOUNT = re.compile(r"(?<![\d,.])(\d{1,3}(?:[.\s ]\d{3})+|\d+)(?:([.,])(\d{1,2}))?(?![\d])")

_DATES = (
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), ("y", "m", "d")),
    (re.compile(r"\b(\d{1,2})[-./](\d{1,2})[-./](\d{4})\b"), ("d", "m", "y")),
    (re.compile(r"\b(\d{1,2})[-./](\d{1,2})[-./](\d{2})\b"), ("d", "m", "yy")),
)


def _to_amount(whole, separator, fraction):
    """One matched number as a float, read the Danish way.

    The awkward case is a lone dot. "1.234" is a thousand-and-something on a
    Danish receipt and "12.50" is twelve fifty on an English one, and the only
    thing telling them apart is how many digits follow — three means it was
    grouping, two means it was a decimal point.
    """
    # No guards on the shape here: _AMOUNT only ever hands this digits and the
    # separators stripped below, and a fraction of at most two digits. Checks
    # for cases the regex cannot produce would be untestable branches pretending
    # to be safety.
    value = float(re.sub(r"[.\s\u00a0]", "", whole))
    if separator and fraction:
        value += int(fraction) / (10 ** len(fraction))
    return value


# Numbers that are plainly not money. Stripped before the scan rather than
# filtered after, because "02-09-2026" and "14:32" each contain several perfectly
# well-formed amounts and the year in particular looks like a plausible sum.
_NOT_MONEY = re.compile(
    r"\b\d{1,2}[-./]\d{1,2}[-./]\d{2,4}\b"   # a date
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}[:.]\d{2}(?::\d{2})?\b"      # a time
    r"|\b\d+\s*(?:kg|g|l|ml|stk|kr/kg|%)\b"   # a quantity or a rate
)


def amounts_in(line, decimals_only=False):
    """Every number on one line that could be money, largest first.

    `decimals_only` keeps just the ones written with øre. A till receipt prints
    two decimals on every price, so when picking between eleven numbers that is
    the strongest signal available that a given one is a price at all — but it
    is not imposed on a marked total line, where a shop that rounds to whole
    kroner would otherwise yield nothing.
    """
    cleaned = _NOT_MONEY.sub(" ", line)
    found = []
    for match in _AMOUNT.finditer(cleaned):
        # A number a letter runs straight into is a quantity or a product code,
        # not a price: "20kg", "5x".
        tail = cleaned[match.end():match.end() + 1]
        if tail.isalpha() or tail == "%":
            continue
        has_decimals = bool(match.group(2) and match.group(3))
        if decimals_only and not has_decimals:
            continue
        value = _to_amount(match.group(1), match.group(2), match.group(3))
        # 0 is never a payment, and a five-figure sum on a feed receipt is an
        # OCR artefact far more often than it is a real number.
        if value is not None and 0 < value < 1_000_000:
            found.append(round(value, 2))
    return sorted(found, reverse=True)


def _score(line):
    """How much this line looks like the one naming the total."""
    lowered = line.lower()
    if any(word in lowered for word in DISQUALIFYING):
        return None
    best = 0
    for marker, weight in TOTAL_MARKERS:
        if marker in lowered:
            best = max(best, weight)
    return best


def find_total(text):
    """The amount most likely to be what was paid, or None.

    Marked lines win. Among them the last one wins, because a receipt that says
    "total" twice means the second one — and among unmarked lines the largest
    wins, because on a till receipt the total is nearly always the biggest
    number that is not the cash tendered.
    """
    best = None
    for index, line in enumerate(text.splitlines()):
        score = _score(line)
        if score is None:
            continue
        for amount in amounts_in(line):
            # A marked line's own amount, or on an unmarked line only as a
            # fallback ranked by size.
            rank = (score, index if score else 0, amount if not score else 0)
            if best is None or rank > best[0]:
                best = (rank, amount)
    return best[1] if best else None


def candidates(text, limit=6):
    """Every plausible amount, best guess first.

    Offered because the best guess is wrong often enough to need an alternative
    that is one tap away rather than a retake of the photograph.
    """
    seen = []
    total = find_total(text)
    if total is not None:
        seen.append(total)
    for line in text.splitlines():
        if _score(line) is None:
            continue
        # Only priced-looking numbers here. The best guess above is allowed to
        # come from a whole-kroner total; the alternatives offered beside it
        # should not include the shop's phone number.
        for amount in amounts_in(line, decimals_only=True):
            if amount not in seen:
                seen.append(amount)
    # Largest first after the pick, since the runner-up is usually the total
    # when the pick was wrong.
    head, tail = seen[:1], sorted(seen[1:], reverse=True)
    return (head + tail)[:limit]


def find_date(text, today=None):
    """The receipt's date, or None.

    Only dates that could be a purchase: a date in the future is a misread, and
    so is one from before there were chickens. Two-digit years are read as
    2000s, which is right for every receipt anybody will photograph.
    """
    today = today or datetime.date.today()
    earliest = datetime.date(today.year - 10, 1, 1)
    for pattern, order in _DATES:
        for match in pattern.finditer(text):
            parts = dict(zip(order, match.groups()))
            year = int(parts.get("y") or 0) or 2000 + int(parts["yy"])
            try:
                found = datetime.date(year, int(parts["m"]), int(parts["d"]))
            except ValueError:
                continue
            if earliest <= found <= today:
                return found.isoformat()
    return None


def find_vendor(text, limit=40):
    """A guess at who was paid: the first line with letters in it.

    Till receipts put the shop's name at the top in the largest type, which is
    also the part OCR reads most reliably. A guess, and offered as one.
    """
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if len(cleaned) >= 3 and re.search(r"[A-Za-zÆØÅæøå]{3}", cleaned):
            return cleaned[:limit]
    return None


def read(text, today=None):
    """Everything worth offering from one receipt's text."""
    return {
        "amount": find_total(text),
        "amounts": candidates(text),
        "date": find_date(text, today=today),
        "vendor": find_vendor(text),
        "text": text.strip(),
    }
