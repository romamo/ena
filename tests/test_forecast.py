import datetime as dt

import pytest
from pydantic import ValidationError

from ena.models import CardSale, InvoiceStatus, Loan, SupplierInvoice, Transaction
from ena.rules.forecast import CashForecast, ScheduledFlow, euros, forecast_cash
from ena.seed import LEAN_WEEK, synthetic_month

START = dt.date(2026, 11, 2)
"""A Monday; the history window is Mon 5 Oct to Sun 1 Nov."""

HISTORY_START = dt.date(2026, 10, 5)
IBAN = "CY17002001280000001200527600"


def week(n: int) -> dt.date:
    """The Monday of forecast week `n` (0-based)."""
    return START + dt.timedelta(weeks=n)


def sale(id_: str, amount_cents: int, date: dt.date) -> CardSale:
    return CardSale(
        id=id_, amount_cents=amount_cents, date=date, jcc_order_id=f"o-{id_}", status="paid"
    )


def tx(id_: str, amount_cents: int, date: dt.date) -> Transaction:
    return Transaction(
        id=id_,
        account_id="acc-1",
        date=date,
        amount_cents=amount_cents,
        counterparty="",
        reference="",
    )


def invoice(
    id_: str, amount_cents: int, due: dt.date, status: InvoiceStatus = InvoiceStatus.NEW
) -> SupplierInvoice:
    return SupplierInvoice(
        id=id_,
        supplier_name="Flour Mills Ltd",
        supplier_iban=IBAN,
        amount_cents=amount_cents,
        issue_date=due - dt.timedelta(days=14),
        due_date=due,
        status=status,
        jinius_id=f"J-{id_}",
    )


# Four history weeks: €400 of card sales and €200 out each week.
SALES = tuple(sale(f"s{n}", 40_000, HISTORY_START + dt.timedelta(weeks=n)) for n in range(4))
TXS = tuple(tx(f"t{n}", -20_000, HISTORY_START + dt.timedelta(weeks=n, days=2)) for n in range(4))
LOAN = Loan(id="loan-1", instalment_cents=30_000, next_due_date=week(5) + dt.timedelta(days=2))
"""€300 due Wednesday of week 5: the threshold is €300 + €200 = €500."""


def run(
    opening_cents: int,
    *,
    loan: Loan = LOAN,
    start: dt.date = START,
    card_sales: tuple[CardSale, ...] = SALES,
    transactions: tuple[Transaction, ...] = TXS,
    invoices: tuple[SupplierInvoice, ...] = (),
    scheduled: tuple[ScheduledFlow, ...] = (),
) -> CashForecast:
    return forecast_cash(
        start,
        opening_cents,
        loan=loan,
        card_sales=card_sales,
        transactions=transactions,
        invoices=invoices,
        scheduled=scheduled,
    )


def test_averages_and_threshold() -> None:
    cash = run(100_000)
    assert cash.expected_card_sales_cents == 40_000
    assert cash.buffer_cents == 20_000
    assert cash.threshold_cents == 50_000
    assert cash.instalment_cents == 30_000
    assert cash.instalment_due == dt.date(2026, 12, 9)


def test_eight_weekly_rows_chain_and_add_up() -> None:
    cash = run(
        100_000,
        invoices=(invoice("i1", 25_000, week(1) + dt.timedelta(days=3)),),
        scheduled=(
            ScheduledFlow(date=week(2), amount_cents=-180_000, label="rent"),
            ScheduledFlow(date=week(3) + dt.timedelta(days=1), amount_cents=7_000, label="grant"),
        ),
    )
    rows = [
        (w.week_start, w.opening_cents, w.in_cents, w.out_cents, w.closing_cents)
        for w in cash.forecast.weeks
    ]
    assert rows == [
        (week(0), 100_000, 40_000, 0, 140_000),
        (week(1), 140_000, 40_000, 25_000, 155_000),
        (week(2), 155_000, 40_000, 180_000, 15_000),
        (week(3), 15_000, 47_000, 0, 62_000),
        (week(4), 62_000, 40_000, 0, 102_000),
        (week(5), 102_000, 40_000, 30_000, 112_000),
        (week(6), 112_000, 40_000, 0, 152_000),
        (week(7), 152_000, 40_000, 0, 192_000),
    ]
    assert cash.forecast.lean_week == week(2)
    assert cash.lean is not None
    assert cash.lean.closing_cents == 15_000


def test_closing_exactly_at_the_threshold_is_not_lean() -> None:
    # Week 0 closes at opening + 40_000; the threshold is 50_000.
    cash = run(10_000)
    assert cash.forecast.weeks[0].closing_cents == 50_000
    assert cash.forecast.lean_week is None
    assert cash.lean is None
    assert cash.forecast.reason == (
        "No week closes below €500.00: the €300.00 instalment due 9 Dec 2026 plus a €200.00"
        " buffer (one week of average outflows); the lowest closing is €500.00 in the week"
        " of 2 Nov 2026."
    )


def test_closing_one_cent_below_the_threshold_is_lean() -> None:
    cash = run(9_999)
    assert cash.forecast.weeks[0].closing_cents == 49_999
    assert cash.forecast.lean_week == week(0)
    assert cash.forecast.reason == (
        "The week of 2 Nov 2026 closes at €499.99, below €500.00: the €300.00 instalment due"
        " 9 Dec 2026 plus a €200.00 buffer (one week of average outflows)."
    )


def test_lean_week_is_the_first_one_below() -> None:
    cash = run(
        100_000,
        scheduled=(
            ScheduledFlow(date=week(3), amount_cents=-220_000, label="rent"),
            ScheduledFlow(date=week(5), amount_cents=-220_000, label="rent"),
        ),
    )
    closings = [w.closing_cents for w in cash.forecast.weeks]
    assert closings == [140_000, 180_000, 220_000, 40_000, 80_000, -130_000, -90_000, -50_000]
    assert cash.forecast.lean_week == week(3)


def test_a_sunday_due_date_belongs_to_the_week_before() -> None:
    sunday = week(1) + dt.timedelta(days=6)
    cash = run(
        100_000,
        invoices=(
            invoice("sun", 1_000, sunday),
            invoice("mon", 2_000, sunday + dt.timedelta(days=1)),
        ),
        scheduled=(ScheduledFlow(date=sunday, amount_cents=-4_000, label="rent"),),
        loan=Loan(id="loan-1", instalment_cents=30_000, next_due_date=sunday),
    )
    outs = [w.out_cents for w in cash.forecast.weeks]
    assert outs[:3] == [0, 1_000 + 4_000 + 30_000, 2_000]


def test_invoices_paid_overdue_and_beyond_the_horizon() -> None:
    cash = run(
        100_000,
        invoices=(
            invoice("overdue", 1_000, START - dt.timedelta(days=10)),
            invoice("paid", 2_000, week(0), InvoiceStatus.PAID),
            invoice("scheduled", 4_000, week(0) + dt.timedelta(days=6), InvoiceStatus.SCHEDULED),
            invoice("beyond", 8_000, week(8)),
        ),
        scheduled=(
            ScheduledFlow(date=START - dt.timedelta(days=1), amount_cents=-16_000, label="past"),
            ScheduledFlow(date=week(8), amount_cents=-32_000, label="beyond"),
        ),
    )
    outs = [w.out_cents for w in cash.forecast.weeks]
    assert outs == [5_000, 0, 0, 0, 0, 30_000, 0, 0]


def test_the_instalment_counts_once_in_its_week() -> None:
    cash = run(100_000, loan=Loan(id="l", instalment_cents=12_345, next_due_date=START))
    outs = [w.out_cents for w in cash.forecast.weeks]
    assert outs == [12_345, 0, 0, 0, 0, 0, 0, 0]


def test_averages_are_floored_and_read_only_the_window() -> None:
    sales = (
        *SALES,
        sale("odd", 3, HISTORY_START + dt.timedelta(days=27)),  # Sunday 1 Nov: in
        sale("early", 99_999, HISTORY_START - dt.timedelta(days=1)),  # before: out
        sale("late", 99_999, START),  # the start itself: out
    )
    txs = (
        *TXS,
        tx("odd-out", -7, HISTORY_START),
        tx("deposit", 50_000, HISTORY_START),  # money in: not an outflow
        tx("late-out", -99_999, START),
    )
    cash = run(100_000, card_sales=sales, transactions=txs)
    assert cash.expected_card_sales_cents == 40_000  # 160_003 // 4
    assert cash.buffer_cents == 20_001  # 80_007 // 4


def test_history_reaching_the_window_exactly_is_enough() -> None:
    cash = run(100_000, card_sales=SALES[:1], transactions=())
    assert cash.expected_card_sales_cents == 10_000
    assert cash.buffer_cents == 0


def test_fewer_than_four_history_weeks_fail() -> None:
    with pytest.raises(ValueError, match="history must reach back to 2026-10-05"):
        run(100_000, card_sales=SALES[1:], transactions=TXS[1:])
    with pytest.raises(ValueError, match="history must reach back"):
        run(100_000, card_sales=(), transactions=())


def test_start_must_be_a_monday() -> None:
    with pytest.raises(ValueError, match="starts on a Monday"):
        run(100_000, start=START + dt.timedelta(days=1))


def test_loan_due_before_the_start_fails() -> None:
    stale = Loan(id="l", instalment_cents=30_000, next_due_date=START - dt.timedelta(days=1))
    with pytest.raises(ValueError, match="before 2026-11-02"):
        run(100_000, loan=stale)


def test_a_scheduled_flow_is_never_zero() -> None:
    with pytest.raises(ValidationError, match="zero amount"):
        ScheduledFlow(date=START, amount_cents=0, label="nothing")
    with pytest.raises(ValidationError):
        ScheduledFlow(date=START, amount_cents=1.5, label="float")


def test_euros() -> None:
    assert euros(0) == "€0.00"
    assert euros(5) == "€0.05"
    assert euros(123_456_789) == "€1,234,567.89"
    assert euros(-1_200) == "-€12.00"


@pytest.mark.parametrize(
    ("start", "closing_cents"),
    [(dt.date(2026, 11, 2), 37_547), (dt.date(2026, 11, 16), 34_969)],
)
def test_the_seed_month_is_lean_in_week_3(start: dt.date, closing_cents: int) -> None:
    month = synthetic_month()
    opening = month.account.balance_cents + sum(
        t.amount_cents for t in month.transactions if month.first_day <= t.date < start
    )
    cash = forecast_cash(
        start,
        opening,
        loan=month.loan,
        card_sales=[s for s in month.card_sales if s.date < start],
        transactions=[t for t in month.transactions if t.date < start],
        invoices=month.invoices,
        scheduled=[
            ScheduledFlow(date=dt.date(2026, 11, 20), amount_cents=-180_000, label="rent"),
            ScheduledFlow(date=dt.date(2026, 12, 20), amount_cents=-180_000, label="rent"),
        ],
    )
    assert cash.forecast.lean_week == LEAN_WEEK == dt.date(2026, 11, 16)
    assert cash.lean is not None
    assert cash.lean.closing_cents == closing_cents
    assert cash.buffer_cents == 55_000  # October's rent and instalment over 4 weeks
    assert cash.threshold_cents == 95_000
