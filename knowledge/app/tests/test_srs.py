from datetime import date

import pytest

import srs


def test_first_good_review_is_due_tomorrow():
    repetitions, interval, ease = srs.schedule("good")
    assert (repetitions, interval) == (1, 1)
    assert ease == srs.DEFAULT_EASE  # q=4 is deliberately ease-neutral


def test_second_good_review_jumps_to_six_days():
    _, interval, _ = srs.schedule("good", repetitions=1, interval_days=1)
    assert interval == 6


def test_third_review_multiplies_by_ease():
    repetitions, interval, ease = srs.schedule("good", repetitions=2, interval_days=6, ease=2.5)
    assert repetitions == 3
    assert interval == 15  # 6 * 2.5


def test_again_resets_the_card_and_lowers_ease():
    repetitions, interval, ease = srs.schedule("again", repetitions=5, interval_days=40, ease=2.5)
    assert (repetitions, interval) == (0, 0)
    assert ease < 2.5


def test_ease_never_falls_below_the_floor():
    ease = 2.5
    for _ in range(20):
        _, _, ease = srs.schedule("again", ease=ease)
    assert ease == srs.MIN_EASE


def test_easy_raises_ease():
    _, _, ease = srs.schedule("easy", repetitions=2, interval_days=6, ease=2.5)
    assert ease > 2.5


def test_hard_advances_but_shortens_the_step():
    _, good_interval, _ = srs.schedule("good", repetitions=1, interval_days=1)
    _, hard_interval, _ = srs.schedule("hard", repetitions=1, interval_days=1)
    assert 0 < hard_interval < good_interval


def test_interval_is_capped():
    _, interval, _ = srs.schedule("easy", repetitions=9, interval_days=10000, ease=2.5)
    assert interval == srs.MAX_INTERVAL_DAYS


def test_unknown_grade_is_rejected_rather_than_guessed():
    with pytest.raises(srs.UnknownGrade):
        srs.schedule("sort-of")


def test_due_date_counts_forward_from_today():
    assert srs.due_date(date(2026, 8, 23), 6) == "2026-08-29"


def test_a_lapsed_card_is_due_the_same_day():
    assert srs.due_date(date(2026, 8, 23), 0) == "2026-08-23"
