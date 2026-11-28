"""An accelerated clock that plays the synthetic month as a time-ordered stream of events.

`month_events` turns a `SeedMonth` into its events: a day starts, a statement line posts,
an invoice arrives, a card sale comes in. `replay` hands them to a `Handler`, the entry
point live events will use too, paced so the whole month takes `duration` of real time
(a minute by default, F10). The clock and its sleep are injected: a test passes a fake
clock, or a zero duration, and the month plays instantly.

Times of day are fixed, so the stream is the same on every run: a day starts at 00:00,
statement lines post at 06:00, invoices arrive at 09:00, and a day's card sales come in
from 08:00, two hours apart. Times are Cyprus time in November (EET, UTC+2).
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict

from ena.models import CardSale, SupplierInvoice, Transaction
from ena.seed import SeedMonth

TZ = dt.timezone(dt.timedelta(hours=2), "EET")
DEFAULT_DURATION = dt.timedelta(minutes=1)

DAY_STARTS = dt.time(0, 0)
STATEMENT_POSTS = dt.time(6, 0)
INVOICES_ARRIVE = dt.time(9, 0)
FIRST_CARD_SALE = dt.time(8, 0)
CARD_SALE_SPACING = dt.timedelta(hours=2)


class _Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    at: AwareDatetime


class DayStarted(_Event):
    kind: Literal["day_started"] = "day_started"
    day: dt.date


class TransactionPosted(_Event):
    kind: Literal["transaction_posted"] = "transaction_posted"
    transaction: Transaction


class InvoiceArrived(_Event):
    kind: Literal["invoice_arrived"] = "invoice_arrived"
    invoice: SupplierInvoice


class CardSaleReceived(_Event):
    kind: Literal["card_sale_received"] = "card_sale_received"
    sale: CardSale


ReplayEvent = DayStarted | TransactionPosted | InvoiceArrived | CardSaleReceived


class Handler(Protocol):
    """Takes one event; live events (#15) come through the same entry point."""

    def __call__(self, event: ReplayEvent) -> None: ...


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    """The real clock."""

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


SYSTEM_CLOCK = SystemClock()


def _at(day: dt.date, clock_time: dt.time) -> dt.datetime:
    return dt.datetime.combine(day, clock_time, tzinfo=TZ)


def _slot(event: ReplayEvent) -> tuple[dt.date, int, str]:
    """The event's day, its kind's rank within a moment, and its record's id."""
    match event:
        case DayStarted():
            return event.day, 0, ""
        case TransactionPosted():
            return event.transaction.date, 1, event.transaction.id
        case InvoiceArrived():
            return event.invoice.issue_date, 2, event.invoice.id
        case CardSaleReceived():
            return event.sale.date, 3, event.sale.id


def month_events(month: SeedMonth) -> tuple[ReplayEvent, ...]:
    """The month's events in time order, from `first_day` to `last_day`."""

    def in_month(day: dt.date) -> bool:
        return month.first_day <= day <= month.last_day

    events: list[ReplayEvent] = []
    day = month.first_day
    while day <= month.last_day:
        events.append(DayStarted(at=_at(day, DAY_STARTS), day=day))
        day += dt.timedelta(days=1)
    events += [
        TransactionPosted(at=_at(tx.date, STATEMENT_POSTS), transaction=tx)
        for tx in month.transactions
        if in_month(tx.date)
    ]
    events += [
        InvoiceArrived(at=_at(invoice.issue_date, INVOICES_ARRIVE), invoice=invoice)
        for invoice in month.invoices
        if in_month(invoice.issue_date)
    ]
    sales_by_day: dict[dt.date, list[CardSale]] = {}
    for sale in month.card_sales:
        if in_month(sale.date):
            sales_by_day.setdefault(sale.date, []).append(sale)
    for sale_day, sales in sales_by_day.items():
        first = _at(sale_day, FIRST_CARD_SALE)
        for n, sale in enumerate(sorted(sales, key=lambda s: s.id)):
            events.append(CardSaleReceived(at=first + n * CARD_SALE_SPACING, sale=sale))
    for event in events:
        day, _, record_id = _slot(event)
        if event.at.date() != day:
            raise ValueError(f"{event.kind} {record_id} spills past its day")
    events.sort(key=lambda event: (event.at, *_slot(event)[1:]))
    return tuple(events)


def replay(
    month: SeedMonth,
    handler: Handler,
    *,
    clock: Clock = SYSTEM_CLOCK,
    duration: dt.timedelta = DEFAULT_DURATION,
) -> tuple[ReplayEvent, ...]:
    """Play the month through `handler`, compressed into `duration` of real time.

    Each event waits until its share of `duration` has passed, so the month's span
    (from the start of `first_day` to the end of `last_day`) maps onto `duration`. A
    zero duration plays the month without sleeping. An error in the handler stops the
    replay. Returns the events in the order they were played.
    """
    if duration < dt.timedelta(0):
        raise ValueError(f"duration must not be negative, got {duration}")
    events = month_events(month)
    start = _at(month.first_day, DAY_STARTS)
    span = _at(month.last_day + dt.timedelta(days=1), DAY_STARTS) - start
    scale = duration / span
    began = clock.monotonic()
    for event in events:
        wait = began + ((event.at - start) * scale).total_seconds() - clock.monotonic()
        if wait > 0:
            clock.sleep(wait)
        handler(event)
    return events
