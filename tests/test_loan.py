import datetime as dt

import pytest

from ena.models import Forecast, ForecastWeek, OfferKind
from ena.rules.forecast import CashForecast
from ena.rules.loan import LOAN_CAP_CENTS, LOAN_TERM_DAYS, loan_offer

START = dt.date(2026, 11, 2)
INSTALMENT = 40_000
BUFFER = 55_000
THRESHOLD = INSTALMENT + BUFFER
"""The €400 instalment plus a €550 buffer."""

AFTER = dt.date(2026, 11, 8)
"""A due date before the lean week (Mon 9 Nov): no reserve, so the loan covers the gap."""

BEFORE = dt.date(2026, 11, 25)
"""A due date after the lean week: the reserve sets the €400 instalment aside."""


def lean_in_week_2(closing_cents: int, due: dt.date = AFTER) -> CashForecast:
    """Week 1 (Mon 2 Nov) closes high; week 2 (Mon 9 Nov) closes at `closing_cents`."""
    weeks = tuple(
        ForecastWeek(
            week_start=START + dt.timedelta(weeks=n),
            opening_cents=closing,
            in_cents=0,
            out_cents=0,
            closing_cents=closing,
        )
        for n, closing in enumerate([10_000_000, closing_cents])
    )
    lean = next((week.week_start for week in weeks if week.closing_cents < THRESHOLD), None)
    return CashForecast(
        forecast=Forecast(weeks=weeks, lean_week=lean, reason="test"),
        instalment_cents=INSTALMENT,
        instalment_due=due,
        expected_card_sales_cents=0,
        buffer_cents=BUFFER,
        threshold_cents=THRESHOLD,
    )


@pytest.mark.parametrize(
    ("gap_cents", "amount_cents"),
    [
        (1, 50_000),
        (50_000, 50_000),
        (50_001, 100_000),
        (100_000, 100_000),
        (1_999_999, LOAN_CAP_CENTS),
        (LOAN_CAP_CENTS, LOAN_CAP_CENTS),
        (2_000_001, LOAN_CAP_CENTS),
    ],
)
def test_the_gap_rounds_up_to_500_and_caps_at_20000(gap_cents: int, amount_cents: int) -> None:
    offer = loan_offer(lean_in_week_2(THRESHOLD - gap_cents))
    assert offer is not None
    assert offer.amount_cents == amount_cents
    assert offer.basis["gap_cents"] == gap_cents


def test_the_offer_is_simulated_and_explains_itself() -> None:
    offer = loan_offer(lean_in_week_2(THRESHOLD - 2_000_001))
    assert offer is not None
    assert offer.kind is OfferKind.LOAN
    assert offer.simulated is True
    assert offer.term_days == LOAN_TERM_DAYS == 730
    assert offer.id == "loan-20261109"
    assert dict(offer.basis) == {
        "gap_cents": 2_000_001,
        "rounded_gap_cents": 2_050_000,
        "cap_cents": 2_000_000,
        "step_cents": 50_000,
        "reserve_cents": 0,
        "lean_week_closing_cents": THRESHOLD - 2_000_001,
        "threshold_cents": THRESHOLD,
        "instalment_cents": 40_000,
        "buffer_cents": 55_000,
        "term_days": 730,
    }


def test_no_lean_week_offers_no_loan() -> None:
    assert loan_offer(lean_in_week_2(THRESHOLD)) is None


def test_a_gap_the_reserve_covers_offers_no_loan() -> None:
    # The reserve sets €400 aside: a gap up to €400 is covered.
    assert loan_offer(lean_in_week_2(THRESHOLD - 40_000, due=BEFORE)) is None


def test_a_gap_above_the_reserve_offers_the_whole_gap() -> None:
    offer = loan_offer(lean_in_week_2(THRESHOLD - 40_001, due=BEFORE))
    assert offer is not None
    assert offer.amount_cents == 50_000
    assert offer.basis["gap_cents"] == 40_001
    assert offer.basis["reserve_cents"] == 40_000


def test_the_seed_gap_offers_1000() -> None:
    # The seed's lean week from Mon 16 Nov closes at €349.69 against €950.00.
    offer = loan_offer(lean_in_week_2(34_969, due=BEFORE))
    assert offer is not None
    assert offer.basis["gap_cents"] == 60_031
    assert offer.amount_cents == 100_000
