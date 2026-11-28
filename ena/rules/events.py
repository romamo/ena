"""The event pattern and the short-term event advance, simulated (docs/build-plan.md §7).

Pure functions, no I/O, integer cents and days throughout. For a deposit-based business (a
caterer): the customer pays a deposit when booking, the event's costs fall due before the
event, and the final payment arrives after it. The advance bridges that gap.

The pattern (`event_pattern`) reads the completed and cancelled events; booked ones are
not history yet. With fewer than 10 completed events (`MIN_COMPLETED`) there is no
pattern and it returns None. Otherwise, over the completed events:

- deposit and cost shares of the price, in basis points (1/100 of a percent): the summed
  deposits (or costs) over the summed prices, floored
- days before the event the costs were paid, and days after it the final payment arrived:
  each as the fewest and the most seen (negative when it happened on the other side)
- the cancellations, counted against all completed and cancelled events

The advance (`event_advance`) for a booked event:

    gap = the event's costs - its deposit - the spare cash the caller passes

It is offered when the pattern exists, cancellations are at most 1 in 10
(`MAX_CANCELLED_PER_10`), the costs are neither paid nor due yet, the gap is above zero
and the final payment can repay it. Its amount is the gap. It runs from the expected costs date (the
event date minus the most days before) to the expected final-payment date (the event date
plus the most days after), at least 1 day, and is repaid from the final payment.
Otherwise it returns a refusal with the reason. `simulated=True`: there is no lending API,
so it is an offer to show the owner, never a promise.

The loan never changes (§3 rule 4): the advance is a new, separate offer.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ena.models import Event, EventStatus, Id, NonNegativeCents, Offer, OfferKind

MIN_COMPLETED = 10
"""The fewest completed events the pattern reads."""

MAX_CANCELLED_PER_10 = 1
"""The most cancellations per 10 completed or cancelled events an eligible business has."""

BASIS_POINTS = 10_000
"""One whole in basis points."""

_Count = Annotated[int, Field(strict=True, ge=0)]
_Days = Annotated[int, Field(strict=True)]
_Share = Annotated[int, Field(strict=True, ge=0)]


class _Result(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EventPattern(_Result):
    """How the business's past events went: the numbers the advance reads."""

    completed: _Count
    cancelled: _Count
    deposit_share_bp: _Share
    cost_share_bp: _Share
    costs_days_before_min: _Days
    costs_days_before_max: _Days
    final_days_after_min: _Days
    final_days_after_max: _Days

    @property
    def cancellations_ok(self) -> bool:
        """At most 1 cancellation in 10 completed or cancelled events."""
        return self.cancelled * 10 <= MAX_CANCELLED_PER_10 * (self.completed + self.cancelled)


class AdvanceRefusal(_Result):
    """Why a booked event gets no advance."""

    event_id: Id
    reason: Annotated[str, StringConstraints(min_length=1)]


def event_pattern(events: Sequence[Event]) -> EventPattern | None:
    """The pattern of the completed events, or None with fewer than 10 of them."""
    completed = [event for event in events if event.status is EventStatus.COMPLETED]
    if len(completed) < MIN_COMPLETED:
        return None
    cancelled = sum(1 for event in events if event.status is EventStatus.CANCELLED)
    price = sum(event.price_cents for event in completed)
    if price == 0:
        raise ValueError("the completed events' prices sum to zero")
    before = [_days_between(event.costs_paid_date, event.event_date) for event in completed]
    after = [_days_between(event.event_date, event.final_paid_date) for event in completed]
    return EventPattern(
        completed=len(completed),
        cancelled=cancelled,
        deposit_share_bp=sum(event.deposit_cents for event in completed) * BASIS_POINTS // price,
        cost_share_bp=sum(event.costs_cents for event in completed) * BASIS_POINTS // price,
        costs_days_before_min=min(before),
        costs_days_before_max=max(before),
        final_days_after_min=min(after),
        final_days_after_max=max(after),
    )


def event_advance(
    event: Event,
    events: Sequence[Event],
    *,
    spare_cents: NonNegativeCents,
    today: dt.date,
) -> Offer | AdvanceRefusal:
    """Offer a simulated advance for the booked event's costs gap, or say why not."""
    if spare_cents < 0:
        raise ValueError(f"spare_cents is never negative, got {spare_cents}")
    if event.status is not EventStatus.BOOKED:
        return _refuse(event, f"the event is {event.status}, not booked")
    if event.costs_paid_date is not None:
        return _refuse(event, f"the costs were already paid on {event.costs_paid_date}")
    pattern = event_pattern(events)
    if pattern is None:
        completed = sum(1 for e in events if e.status is EventStatus.COMPLETED)
        return _refuse(
            event, f"fewer than {MIN_COMPLETED} completed events ({completed}) to read a pattern"
        )
    if not pattern.cancellations_ok:
        total = pattern.completed + pattern.cancelled
        return _refuse(
            event,
            f"{pattern.cancelled} of {total} events were cancelled, more than"
            f" {MAX_CANCELLED_PER_10} in 10",
        )
    costs_date = event.event_date - dt.timedelta(days=pattern.costs_days_before_max)
    if costs_date < today:
        return _refuse(event, f"the costs were due on {costs_date}, before today {today}")
    gap = event.costs_cents - event.deposit_cents - spare_cents
    if gap <= 0:
        return _refuse(event, "the deposit and the spare cash cover the costs")
    if gap > event.final_payment_cents:
        return _refuse(event, "the final payment can't repay an advance for the whole gap")
    final_date = event.event_date + dt.timedelta(days=pattern.final_days_after_max)
    term_days = max((final_date - costs_date).days, 1)
    return Offer(
        id=f"advance-{event.id}",
        kind=OfferKind.EVENT_ADVANCE,
        amount_cents=gap,
        term_days=term_days,
        basis=frozendict(
            gap_cents=gap,
            costs_cents=event.costs_cents,
            deposit_cents=event.deposit_cents,
            spare_cents=spare_cents,
            price_cents=event.price_cents,
            final_payment_cents=event.final_payment_cents,
            completed_events=pattern.completed,
            cancelled_events=pattern.cancelled,
            costs_days_before=pattern.costs_days_before_max,
            final_days_after=pattern.final_days_after_max,
            days_to_costs=(costs_date - today).days,
            term_days=term_days,
        ),
        simulated=True,
    )


def _days_between(start: dt.date | None, end: dt.date | None) -> int:
    if start is None or end is None:
        raise ValueError("a completed event has costs_paid_date and final_paid_date")
    return (end - start).days


def _refuse(event: Event, reason: str) -> AdvanceRefusal:
    return AdvanceRefusal(event_id=event.id, reason=reason)
