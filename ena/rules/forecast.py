"""The 8-week cash forecast (docs/build-plan.md §7).

A pure function, no I/O, integer cents throughout. The forecast starts on a Monday and has
8 weekly rows; week `i` covers its Monday to its Sunday (a date due on a Sunday belongs to
the week of the Monday before). Each row is:

    closing = opening + in - out, and the next week opens at this week's closing

- in = expected card sales + the known inflows dated that week
- out = the unpaid invoices due that week + the known outflows dated that week (the rent,
  and anything else the caller knows of) + the loan instalment, when `loan.next_due_date`
  falls that week

The history window is the 4 full weeks before the start: from the Monday 28 days before
it to the Sunday before it, inclusive.

- Expected card sales = the card sales dated in the history window, summed, divided by 4
  and floored (`//`)
- The buffer ("one week of average outflows") = the money out of the account in the
  history window (the sum of the negative transactions' absolute amounts), divided by 4
  and floored
- The threshold = the loan's instalment + the buffer
- The lean week = the first week whose closing is below the threshold (strictly: a closing
  equal to it is not lean)

Interpretations:

- Rent and other known flows are never guessed: the caller passes them as `ScheduledFlow`s
  (positive for money in, negative for money out). Flows dated outside the 8 weeks are
  ignored
- Only `loan.next_due_date`'s instalment is counted (the loan's schedule is not in the
  model); a later instalment the caller knows of is a `ScheduledFlow`
- An invoice is unpaid unless its status is `paid`. An unpaid invoice due before the start
  is overdue and counted in the first week; one due after the 8 weeks is ignored
- Card sales and transactions are not filtered by status: the caller passes what it wants
  counted

Raises `ValueError` when the start is not a Monday, when the loan's next due date is before
the start, or when the history does not reach back to the window's first day (neither a
card sale nor a transaction dated on or before it): fewer than 4 weeks of history fails
loudly rather than averaging a shorter period.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from ena.models import (
    CardSale,
    Cents,
    Forecast,
    ForecastWeek,
    InvoiceStatus,
    IsoDate,
    Loan,
    NonNegativeCents,
    SupplierInvoice,
    Transaction,
)

WEEKS = 8
"""How many weekly rows the forecast has."""

HISTORY_WEEKS = 4
"""How many full weeks before the start the averages read."""

_WEEK = dt.timedelta(weeks=1)


class _Result(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ScheduledFlow(_Result):
    """A known dated amount: positive for money in, negative for money out."""

    date: IsoDate
    amount_cents: Cents
    label: Annotated[str, StringConstraints(min_length=1)]

    @model_validator(mode="after")
    def _not_zero(self) -> Self:
        if self.amount_cents == 0:
            raise ValueError(f"scheduled flow {self.label!r} has a zero amount")
        return self


class CashForecast(_Result):
    """The forecast and the numbers it was built from, for the reserve and loan rules."""

    forecast: Forecast
    instalment_cents: NonNegativeCents
    instalment_due: IsoDate
    expected_card_sales_cents: NonNegativeCents
    """Per week: the history window's card sales // 4."""
    buffer_cents: NonNegativeCents
    """One week of average outflows: the history window's money out // 4."""
    threshold_cents: NonNegativeCents
    """The instalment plus the buffer; a week closing below it is lean."""

    @model_validator(mode="after")
    def _threshold_adds_up(self) -> Self:
        if self.threshold_cents != self.instalment_cents + self.buffer_cents:
            raise ValueError("threshold_cents != instalment_cents + buffer_cents")
        if not self.forecast.weeks:
            raise ValueError("the forecast has no weeks")
        first_below = next(
            (w.week_start for w in self.forecast.weeks if w.closing_cents < self.threshold_cents),
            None,
        )
        if self.forecast.lean_week != first_below:
            raise ValueError(
                f"lean_week {self.forecast.lean_week} is not the first week closing below"
                f" the threshold ({first_below})"
            )
        return self

    @property
    def start(self) -> dt.date:
        return self.forecast.weeks[0].week_start

    @property
    def lean(self) -> ForecastWeek | None:
        """The lean week's row, or None when no week is lean."""
        for week in self.forecast.weeks:
            if week.week_start == self.forecast.lean_week:
                return week
        return None


def forecast_cash(
    start: dt.date,
    opening_cents: int,
    *,
    loan: Loan,
    card_sales: Sequence[CardSale],
    transactions: Sequence[Transaction],
    invoices: Sequence[SupplierInvoice],
    scheduled: Sequence[ScheduledFlow],
) -> CashForecast:
    """Forecast 8 weeks of cash from the Monday `start`, opening at `opening_cents`."""
    if start.weekday() != 0:
        raise ValueError(f"the forecast starts on a Monday, got {start:%A} {start}")
    if loan.next_due_date < start:
        raise ValueError(f"the loan's next due date {loan.next_due_date} is before {start}")
    history_start = start - HISTORY_WEEKS * _WEEK
    earliest = min(
        [sale.date for sale in card_sales] + [tx.date for tx in transactions], default=None
    )
    if earliest is None or earliest > history_start:
        raise ValueError(
            f"the history must reach back to {history_start} ({HISTORY_WEEKS} full weeks"
            f" before {start}); the earliest record is {earliest}"
        )

    def in_history(date: dt.date) -> bool:
        return history_start <= date < start

    sales_total = sum(sale.amount_cents for sale in card_sales if in_history(sale.date))
    out_total = sum(
        -tx.amount_cents for tx in transactions if tx.amount_cents < 0 and in_history(tx.date)
    )
    expected = sales_total // HISTORY_WEEKS
    buffer = out_total // HISTORY_WEEKS
    threshold = loan.instalment_cents + buffer

    unpaid = [invoice for invoice in invoices if invoice.status is not InvoiceStatus.PAID]
    weeks: list[ForecastWeek] = []
    opening = opening_cents
    for n in range(WEEKS):
        week_start = start + n * _WEEK
        week_end = week_start + _WEEK
        in_cents = expected + sum(
            flow.amount_cents
            for flow in scheduled
            if flow.amount_cents > 0 and week_start <= flow.date < week_end
        )
        out_cents = sum(
            invoice.amount_cents
            for invoice in unpaid
            if week_start <= max(invoice.due_date, start) < week_end
        )
        out_cents += sum(
            -flow.amount_cents
            for flow in scheduled
            if flow.amount_cents < 0 and week_start <= flow.date < week_end
        )
        if week_start <= loan.next_due_date < week_end:
            out_cents += loan.instalment_cents
        closing = opening + in_cents - out_cents
        weeks.append(
            ForecastWeek(
                week_start=week_start,
                opening_cents=opening,
                in_cents=in_cents,
                out_cents=out_cents,
                closing_cents=closing,
            )
        )
        opening = closing

    lean = next((week for week in weeks if week.closing_cents < threshold), None)
    return CashForecast(
        forecast=Forecast(
            weeks=tuple(weeks),
            lean_week=None if lean is None else lean.week_start,
            reason=_reason(weeks, lean, loan, buffer, threshold),
        ),
        instalment_cents=loan.instalment_cents,
        instalment_due=loan.next_due_date,
        expected_card_sales_cents=expected,
        buffer_cents=buffer,
        threshold_cents=threshold,
    )


def euros(cents: int) -> str:
    """Cents as euros for a sentence: `€1,234.05`, `-€12.00`."""
    sign = "-" if cents < 0 else ""
    whole, part = divmod(abs(cents), 100)
    return f"{sign}€{whole:,}.{part:02d}"


def _day(date: dt.date) -> str:
    return f"{date.day} {date:%b %Y}"


def _reason(
    weeks: Sequence[ForecastWeek],
    lean: ForecastWeek | None,
    loan: Loan,
    buffer: int,
    threshold: int,
) -> str:
    bar = (
        f"{euros(threshold)}: the {euros(loan.instalment_cents)} instalment due"
        f" {_day(loan.next_due_date)} plus a {euros(buffer)} buffer"
        f" (one week of average outflows)"
    )
    if lean is not None:
        return (
            f"The week of {_day(lean.week_start)} closes at {euros(lean.closing_cents)},"
            f" below {bar}."
        )
    lowest = min(weeks, key=lambda week: (week.closing_cents, week.week_start))
    return (
        f"No week closes below {bar}; the lowest closing is {euros(lowest.closing_cents)}"
        f" in the week of {_day(lowest.week_start)}."
    )
