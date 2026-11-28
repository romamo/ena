import datetime as dt

import pytest
from pydantic import ValidationError

from ena.models import Event, EventStatus, OfferKind
from ena.rules.events import (
    AdvanceRefusal,
    EventPattern,
    advance_eligibility,
    event_advance,
    event_pattern,
)

TODAY = dt.date(2026, 11, 28)
WEDDING_DAY = dt.date(2026, 12, 19)


def past(n: int, *, costs_before: int = 7, final_after: int = 9) -> Event:
    """A completed €8,000 wedding: 25% deposit, 60% costs."""
    day = dt.date(2026, 1, 3) + dt.timedelta(weeks=2 * n)
    return Event(
        id=f"past-{n}",
        customer=f"Wedding {n}",
        event_date=day,
        price_cents=800_000,
        deposit_cents=200_000,
        costs_cents=480_000,
        final_payment_cents=600_000,
        status=EventStatus.COMPLETED,
        costs_paid_date=day - dt.timedelta(days=costs_before),
        final_paid_date=day + dt.timedelta(days=final_after),
    )


def cancelled(n: int) -> Event:
    return Event(
        id=f"cancelled-{n}",
        customer=f"Cancelled {n}",
        event_date=dt.date(2026, 6, 6),
        price_cents=500_000,
        deposit_cents=100_000,
        costs_cents=0,
        final_payment_cents=400_000,
        status=EventStatus.CANCELLED,
    )


WEDDING = Event(
    id="georgiou",
    customer="Georgiou wedding",
    event_date=WEDDING_DAY,
    price_cents=800_000,
    deposit_cents=200_000,
    costs_cents=480_000,
    final_payment_cents=600_000,
    status=EventStatus.BOOKED,
)

HISTORY = [past(n, costs_before=3 + n % 5) for n in range(10)]
"""Ten weddings whose costs were paid 3 to 7 days before, the final payment 9 days after."""


def test_the_worked_example_offers_1800_for_16_days() -> None:
    offer = event_advance(WEDDING, [*HISTORY, WEDDING], spare_cents=100_000, today=TODAY)
    assert not isinstance(offer, AdvanceRefusal)
    assert offer.kind is OfferKind.EVENT_ADVANCE
    assert offer.simulated is True
    assert offer.id == "advance-georgiou-180000"
    assert offer.amount_cents == 180_000
    assert offer.term_days == 16
    assert dict(offer.basis) == {
        "gap_cents": 180_000,
        "costs_cents": 480_000,
        "deposit_cents": 200_000,
        "spare_cents": 100_000,
        "price_cents": 800_000,
        "final_payment_cents": 600_000,
        "completed_events": 10,
        "cancelled_events": 0,
        "costs_days_before": 7,
        "final_days_after": 9,
        "days_to_costs": 14,
        "term_days": 16,
    }


def test_the_pattern_reads_shares_and_days_of_completed_events_only() -> None:
    pattern = event_pattern([*HISTORY, cancelled(0), WEDDING])
    assert pattern is not None
    assert pattern.completed == 10
    assert pattern.cancelled == 1
    assert pattern.deposit_share_bp == 2_500
    assert pattern.cost_share_bp == 6_000
    assert (pattern.costs_days_before_min, pattern.costs_days_before_max) == (3, 7)
    assert (pattern.final_days_after_min, pattern.final_days_after_max) == (9, 9)


def test_fewer_than_10_completed_events_have_no_pattern() -> None:
    assert event_pattern(HISTORY[:9]) is None
    refusal = event_advance(WEDDING, HISTORY[:9], spare_cents=0, today=TODAY)
    assert refusal == AdvanceRefusal(
        event_id="georgiou",
        reason="fewer than 10 completed events (9) to read a pattern",
    )


def test_one_cancellation_in_10_is_eligible_and_two_in_12_is_not() -> None:
    one_in_11 = [*HISTORY, cancelled(0)]
    assert not isinstance(
        event_advance(WEDDING, one_in_11, spare_cents=0, today=TODAY), AdvanceRefusal
    )
    two_in_12 = [*HISTORY, cancelled(0), cancelled(1)]
    refusal = event_advance(WEDDING, two_in_12, spare_cents=0, today=TODAY)
    assert refusal == AdvanceRefusal(
        event_id="georgiou", reason="2 of 12 events were cancelled, more than 1 in 10"
    )


def test_two_cancellations_in_20_are_eligible() -> None:
    history = [*HISTORY, *(past(n) for n in range(10, 18)), cancelled(0), cancelled(1)]
    assert not isinstance(
        event_advance(WEDDING, history, spare_cents=0, today=TODAY), AdvanceRefusal
    )


@pytest.mark.parametrize(("spare_cents", "amount_cents"), [(0, 280_000), (279_999, 1)])
def test_the_advance_is_the_gap(spare_cents: int, amount_cents: int) -> None:
    offer = event_advance(WEDDING, HISTORY, spare_cents=spare_cents, today=TODAY)
    assert not isinstance(offer, AdvanceRefusal)
    assert offer.amount_cents == amount_cents


def test_no_gap_no_advance() -> None:
    refusal = event_advance(WEDDING, HISTORY, spare_cents=280_000, today=TODAY)
    assert refusal == AdvanceRefusal(
        event_id="georgiou", reason="the deposit and the spare cash cover the costs"
    )


def test_a_gap_above_the_final_payment_is_refused() -> None:
    costly = WEDDING.model_copy(update={"costs_cents": 800_001})
    refusal = event_advance(costly, HISTORY, spare_cents=0, today=TODAY)
    assert refusal == AdvanceRefusal(
        event_id="georgiou",
        reason="the final payment can't repay an advance for the whole gap",
    )


def test_costs_already_due_are_refused() -> None:
    late = WEDDING_DAY - dt.timedelta(days=6)
    refusal = event_advance(WEDDING, HISTORY, spare_cents=0, today=late)
    assert refusal == AdvanceRefusal(
        event_id="georgiou", reason="the costs were due on 2026-12-12, before today 2026-12-13"
    )
    on_the_day = event_advance(WEDDING, HISTORY, spare_cents=0, today=late - dt.timedelta(1))
    assert not isinstance(on_the_day, AdvanceRefusal)
    assert on_the_day.basis["days_to_costs"] == 0


def test_costs_already_paid_are_refused() -> None:
    paid = WEDDING.model_copy(update={"costs_paid_date": TODAY - dt.timedelta(days=1)})
    refusal = event_advance(paid, HISTORY, spare_cents=0, today=TODAY)
    assert refusal == AdvanceRefusal(
        event_id="georgiou", reason="the costs were already paid on 2026-11-27"
    )


def test_only_a_booked_event_gets_an_advance() -> None:
    refusal = event_advance(HISTORY[0], HISTORY, spare_cents=0, today=TODAY)
    assert refusal == AdvanceRefusal(event_id="past-0", reason="the event is completed, not booked")


def test_a_term_never_drops_below_one_day() -> None:
    history = [past(n, costs_before=0, final_after=0) for n in range(10)]
    offer = event_advance(WEDDING, history, spare_cents=0, today=TODAY)
    assert not isinstance(offer, AdvanceRefusal)
    assert offer.term_days == 1


def test_negative_spare_cash_fails() -> None:
    with pytest.raises(ValueError, match="spare_cents"):
        event_advance(WEDDING, HISTORY, spare_cents=-1, today=TODAY)


def test_an_event_whose_payments_dont_add_up_is_refused() -> None:
    with pytest.raises(ValidationError, match="!= price_cents"):
        Event.model_validate({**WEDDING.model_dump(), "final_payment_cents": 1})


def test_a_completed_event_needs_both_dates() -> None:
    with pytest.raises(ValidationError, match="costs_paid_date and final_paid_date"):
        Event.model_validate({**past(0).model_dump(), "final_paid_date": None})


def test_a_booked_event_has_no_final_paid_date() -> None:
    with pytest.raises(ValidationError, match="booked event has no final_paid_date"):
        Event.model_validate({**WEDDING.model_dump(), "final_paid_date": TODAY})


def test_eligibility_needs_no_spare_cash_and_names_the_costs_date() -> None:
    pattern = advance_eligibility(WEDDING, HISTORY, today=TODAY)
    assert isinstance(pattern, EventPattern)
    assert pattern.costs_date(WEDDING) == dt.date(2026, 12, 12)
    refusal = advance_eligibility(HISTORY[0], HISTORY, today=TODAY)
    assert refusal == AdvanceRefusal(event_id="past-0", reason="the event is completed, not booked")


def test_an_advance_of_another_amount_is_another_offer() -> None:
    low = event_advance(WEDDING, HISTORY, spare_cents=100_000, today=TODAY)
    high = event_advance(WEDDING, HISTORY, spare_cents=0, today=TODAY)
    assert not isinstance(low, AdvanceRefusal) and not isinstance(high, AdvanceRefusal)
    assert (low.id, high.id) == ("advance-georgiou-180000", "advance-georgiou-280000")
