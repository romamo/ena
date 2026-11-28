import datetime as dt

import pytest

from ena.models import CardSale, SupplierInvoice, Transaction
from ena.replay import (
    DEFAULT_DURATION,
    CardSaleReceived,
    DayStarted,
    InvoiceArrived,
    ReplayEvent,
    TransactionPosted,
    month_events,
    replay,
)
from ena.seed import AMOUNTS, ID_PREFIX, LEAN_WEEK, SYNTHETIC, SeedMonth, synthetic_month

MONTH = synthetic_month()


class FakeClock:
    """A clock whose sleep only moves its own time forward."""

    def __init__(self) -> None:
        self.now = 1_000.0
        self.slept = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept += seconds
        self.now += seconds


class Recorder:
    def __init__(self) -> None:
        self.events: list[ReplayEvent] = []

    def __call__(self, event: ReplayEvent) -> None:
        self.events.append(event)


def _in_month(day: dt.date) -> bool:
    return MONTH.first_day <= day <= MONTH.last_day


def test_replay_plays_the_whole_month_the_same_twice() -> None:
    runs = []
    for _ in range(2):
        clock, recorder = FakeClock(), Recorder()
        played = replay(synthetic_month(), recorder, clock=clock, duration=dt.timedelta(0))
        assert clock.slept == 0
        assert list(played) == recorder.events
        runs.append(played)
    assert runs[0] == runs[1]

    played = runs[0]
    assert [event.at for event in played] == sorted(event.at for event in played)
    assert [event.day for event in played if isinstance(event, DayStarted)] == [
        MONTH.first_day + dt.timedelta(days=n) for n in range(30)
    ]
    posted = [event.transaction for event in played if isinstance(event, TransactionPosted)]
    assert posted == [tx for tx in MONTH.transactions if _in_month(tx.date)]
    arrived = [event.invoice for event in played if isinstance(event, InvoiceArrived)]
    assert arrived == list(MONTH.invoices)
    sold = [event.sale for event in played if isinstance(event, CardSaleReceived)]
    assert sold == [sale for sale in MONTH.card_sales if _in_month(sale.date)]


def test_replay_takes_about_a_minute_by_default() -> None:
    clock = FakeClock()
    replay(MONTH, Recorder(), clock=clock)
    assert DEFAULT_DURATION == dt.timedelta(minutes=1)
    assert 55 < clock.slept <= 60


def test_replay_stops_on_a_handler_error() -> None:
    def failing(event: ReplayEvent) -> None:
        if isinstance(event, InvoiceArrived):
            raise RuntimeError(event.invoice.id)

    with pytest.raises(RuntimeError, match="syn-inv-0001"):
        replay(MONTH, failing, clock=FakeClock(), duration=dt.timedelta(0))


def test_replay_refuses_a_negative_duration() -> None:
    with pytest.raises(ValueError, match="negative"):
        replay(MONTH, Recorder(), clock=FakeClock(), duration=dt.timedelta(seconds=-1))


def test_seed_is_the_same_on_every_build() -> None:
    assert synthetic_month() == MONTH
    assert month_events(synthetic_month()) == month_events(MONTH)


def test_seed_holds_the_owner_journey() -> None:
    business = MONTH.business
    assert business.synthetic
    assert business.auto_pay_limit_cents == 50_000
    assert business.account_id == MONTH.account.id
    assert MONTH.first_day == dt.date(2026, 11, 1)
    assert MONTH.last_day == dt.date(2026, 11, 30)

    dairy, flour = MONTH.invoices
    assert (dairy.amount_cents, dairy.issue_date.strftime("%A")) == (18_000, "Monday")
    assert dairy.amount_cents <= business.auto_pay_limit_cents
    assert (flour.amount_cents, flour.issue_date.strftime("%A")) == (124_000, "Wednesday")
    assert flour.amount_cents > business.auto_pay_limit_cents

    month_sales = [sale for sale in MONTH.card_sales if _in_month(sale.date)]
    assert len(month_sales) == 25 * AMOUNTS.card_sales_per_day  # 30 days less 5 Sundays
    settled = {tx.reference for tx in MONTH.transactions if tx.amount_cents > 0}
    assert all(f"JCC {sale.jcc_order_id}" in settled for sale in month_sales[:-3])

    november = [tx for tx in MONTH.transactions if _in_month(tx.date)]
    rent = [tx for tx in november if tx.reference.startswith("Rent")]
    assert [(tx.date, tx.amount_cents) for tx in rent] == [(dt.date(2026, 11, 20), -180_000)]
    instalments = [tx for tx in november if tx.reference.startswith("Instalment")]
    assert [(tx.date, tx.amount_cents) for tx in instalments] == [
        (MONTH.loan.next_due_date, -MONTH.loan.instalment_cents)
    ]
    assert MONTH.loan.instalment_cents == 40_000


def test_every_seed_record_is_marked_synthetic() -> None:
    records: list[Transaction | SupplierInvoice | CardSale] = [
        *MONTH.transactions,
        *MONTH.invoices,
        *MONTH.card_sales,
    ]
    ids = [MONTH.business.id, MONTH.account.id, MONTH.loan.id, *(r.id for r in records)]
    assert all(record_id.startswith(ID_PREFIX) for record_id in ids)
    assert MONTH.business.name.endswith(SYNTHETIC)
    assert all(invoice.supplier_name.endswith(SYNTHETIC) for invoice in MONTH.invoices)
    assert all(tx.counterparty.endswith(SYNTHETIC) for tx in MONTH.transactions)

    data = MONTH.model_dump()
    data["business"] = {**data["business"], "synthetic": False}
    with pytest.raises(ValueError, match="synthetic"):
        SeedMonth.model_validate(data)


def test_week_3_is_lean_under_the_forecast_formula() -> None:
    # §7 run on the lean week's Monday: closing = opening + expected card sales
    # - invoices due that week - rent - instalment due that week.
    week_end = LEAN_WEEK + dt.timedelta(days=6)
    assert LEAN_WEEK == MONTH.first_day + dt.timedelta(days=15)
    statement = sum(
        tx.amount_cents for tx in MONTH.transactions if MONTH.first_day <= tx.date < LEAN_WEEK
    )
    known = [invoice for invoice in MONTH.invoices if invoice.issue_date < LEAN_WEEK]
    # Paid already or due in the week: the cash is gone by the week's end either way.
    assert all(invoice.due_date <= week_end for invoice in known)
    invoices = sum(invoice.amount_cents for invoice in known)
    last_4_weeks = [
        sale.amount_cents
        for sale in MONTH.card_sales
        if LEAN_WEEK - dt.timedelta(weeks=4) <= sale.date < LEAN_WEEK
    ]
    expected_sales = sum(last_4_weeks) // 4
    rent = -sum(
        tx.amount_cents
        for tx in MONTH.transactions
        if tx.reference.startswith("Rent") and LEAN_WEEK <= tx.date <= week_end
    )
    assert MONTH.loan.next_due_date > week_end  # the instalment comes after the lean week

    closing = MONTH.account.balance_cents + statement - invoices + expected_sales - rent
    assert 0 < closing < MONTH.loan.instalment_cents  # lean before any buffer
