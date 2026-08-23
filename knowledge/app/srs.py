"""Spaced repetition for the flashcards that come with each subtopic.

SM-2, the SuperMemo algorithm Anki's scheduler descends from, in its plain
form: each card carries an ease factor and an interval, a review multiplies
the interval by the ease, and a failed review sends the card back to the
start. Chosen over anything cleverer because it needs no history beyond the
three numbers stored on the card itself — which matters here, since this
add-on has to work with whatever is in its own SQLite file and nothing else.

Grades are the four buttons a reviewer actually sees, mapped onto SM-2's 0-5
quality scale: "again" is a lapse, the other three are recall of increasing
comfort.
"""

# Reviewer-facing grade -> SM-2 quality. Nothing below "again" is offered:
# SM-2's q=0/q=1 exist to distinguish degrees of blackout, which a person
# cannot report honestly and which change nothing here — both reset the card.
GRADES = {
    "again": 2,
    "hard": 3,
    "good": 4,
    "easy": 5,
}

DEFAULT_EASE = 2.5
MIN_EASE = 1.3
# A card seen once a year is a card that is no longer being learned; past
# this the schedule stops stretching and the card simply stays annual.
MAX_INTERVAL_DAYS = 365


class UnknownGrade(ValueError):
    pass


def schedule(grade, repetitions=0, interval_days=0, ease=DEFAULT_EASE):
    """Next scheduling state for a card just reviewed with `grade`.

    Returns `(repetitions, interval_days, ease)`. An interval of 0 means
    "due again today" — a lapsed card should come back within the same
    session rather than tomorrow, which is the one place this departs from
    textbook SM-2 (it has no concept of a session).
    """
    if grade not in GRADES:
        raise UnknownGrade(f"unknown grade {grade!r}; expected one of {sorted(GRADES)}")
    quality = GRADES[grade]

    # SM-2's ease adjustment, verbatim. "good" is deliberately neutral (the
    # term evaluates to exactly 0 at q=4), so a card recalled comfortably
    # every time keeps the ease it earned rather than drifting upward.
    ease = ease + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    ease = max(MIN_EASE, round(ease, 4))

    if quality < 3:
        # A lapse. The interval is thrown away rather than shortened: the
        # card demonstrably was not known, so its old interval was evidence
        # about a card that no longer exists.
        return 0, 0, ease

    repetitions += 1
    if repetitions == 1:
        interval_days = 1
    elif repetitions == 2:
        interval_days = 6
    else:
        interval_days = round(max(1, interval_days) * ease)
    if grade == "hard":
        # Recalled, but not comfortably — SM-2 lets the ease alone carry
        # this, which takes several reviews to bite. Shortening the step too
        # keeps a shaky card from jumping straight to a six-day gap.
        interval_days = max(1, round(interval_days * 0.7))
    return repetitions, min(MAX_INTERVAL_DAYS, interval_days), ease


def due_date(today, interval_days):
    """The date a card reviewed today next comes up, as an ISO date string.

    `today` is a `datetime.date`. Kept here next to `schedule` so the two
    halves of "when is this card next seen" cannot drift apart.
    """
    from datetime import timedelta

    return (today + timedelta(days=max(0, int(interval_days)))).isoformat()
