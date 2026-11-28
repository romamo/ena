"""The synthetic month of the owner journey (docs/plan.md), built from a fixed random seed.

A bakery on a Bank of Cyprus account, November 2026, as `ena/models.py` entities:

- Sunday 1 November (day 1): the owner sets up Ena with a €500 limit
- Monday 2 November: a €180 dairy invoice, under the limit
- Wednesday 4 November: a €1,240 flour invoice, above the limit, due 18 November
- Monday to Saturday: card sales through JCC, each settled to the account the next day
- 20 November: the rent; 25 November: the loan instalment, both standing orders
- Monday 30 November (day 30): the month ends

The four weeks before the month (from 4 October) hold card sales, their settlements,
October's rent and October's instalment: the history Ena reads at set up, and the last
four weeks of card sales the forecast averages.

Every record is synthetic: the business has `synthetic=True`, every id starts with `syn-`,
and every name (business, suppliers, counterparties) ends with `(synthetic)`.

The lean week is week 3, Monday 16 to Sunday 22 November (`LEAN_WEEK`). Run on its
Monday, §7's forecast (docs/build-plan.md) closes that week at €349.69, below the
€400 instalment due on 25 November even before any buffer. Why: the opening balance is
what is left after the dairy and the flour invoices (both due by 18 November, so it
makes no difference whether Ena has paid them yet), one week of expected card sales
(€720.50, the average of the four weeks before) comes in, and the €1,800 rent goes out
on 20 November. The instalment falls after the lean week, which is what the reserve
rule looks for. With both invoices paid on arrival the balance stays above zero all
month; its lowest point is €90.88, after the rent.

The bakery also caters weddings and christenings, paid as a deposit at booking and a
final payment after the event (`events`): 12 completed and 1 cancelled since January
2025, the history the event advance reads, and one booked wedding on Saturday 19
December 2026 (`BOOKED_EVENT_ID`): €8,000, a €2,000 deposit and €4,800 of costs, the
numbers of the build plan's worked example (docs/build-plan.md §7). The spare cash is not
the example's €1,000: it comes from the forecast, which leaves none in the lean week, so
the advance offered on the lean Monday is €2,800 for 16 days, not €1,800. The events'
money is not on the statement: the month's cash story above stays as it is.

The amounts are placeholders until the interviews: they all sit in `AMOUNTS`.
"""

from __future__ import annotations

import datetime as dt
import random
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ena.models import (
    Account,
    Business,
    CardSale,
    Event,
    EventStatus,
    InvoiceStatus,
    Loan,
    SupplierInvoice,
    Transaction,
)

SEED = 20261127
"""The fixed random seed: the same month on every run."""

ID_PREFIX = "syn-"
"""Every synthetic record's id starts with it."""

SYNTHETIC = "(synthetic)"
"""Every synthetic name ends with it."""

FIRST_DAY = dt.date(2026, 11, 1)
LAST_DAY = dt.date(2026, 11, 30)
HISTORY_START = FIRST_DAY - dt.timedelta(weeks=4)
"""Sunday 4 October: four weeks of history before the month."""

LEAN_WEEK = dt.date(2026, 11, 16)
"""The Monday of the lean week (week 3)."""

SETTLEMENT_DELAY = dt.timedelta(days=1)
"""JCC settles a card sale to the account the next day (ASSUMED, docs/build-plan.md §7)."""

RENT_DAY = 20
LOAN_DAY = 25

BOOKED_EVENT_ID = "syn-event-0014"
"""The booked wedding the event advance is offered for."""


class Placeholders(BaseModel):
    """The month's amounts in euro cents, placeholders until the interviews."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    auto_pay_limit_cents: Annotated[int, Field(strict=True, gt=0)]
    opening_balance_cents: Annotated[int, Field(strict=True)]
    """The balance at the start of 1 November."""
    dairy_invoice_cents: Annotated[int, Field(strict=True, gt=0)]
    flour_invoice_cents: Annotated[int, Field(strict=True, gt=0)]
    rent_cents: Annotated[int, Field(strict=True, gt=0)]
    loan_instalment_cents: Annotated[int, Field(strict=True, gt=0)]
    card_sales_per_day: Annotated[int, Field(strict=True, gt=0)]
    card_sale_min_cents: Annotated[int, Field(strict=True, gt=0)]
    card_sale_max_cents: Annotated[int, Field(strict=True, gt=0)]

    @model_validator(mode="after")
    def _journey_holds(self) -> Self:
        if self.dairy_invoice_cents > self.auto_pay_limit_cents:
            raise ValueError("the dairy invoice must be within the limit")
        if self.flour_invoice_cents <= self.auto_pay_limit_cents:
            raise ValueError("the flour invoice must be above the limit")
        if self.card_sale_min_cents > self.card_sale_max_cents:
            raise ValueError("card_sale_min_cents is above card_sale_max_cents")
        return self


AMOUNTS = Placeholders(
    auto_pay_limit_cents=50_000,
    opening_balance_cents=130_000,
    dairy_invoice_cents=18_000,
    flour_invoice_cents=124_000,
    rent_cents=180_000,
    loan_instalment_cents=40_000,
    card_sales_per_day=3,
    card_sale_min_cents=3_500,
    card_sale_max_cents=4_500,
)

_BUSINESS_ID = "syn-biz-1"
_ACCOUNT_ID = "syn-acc-1"
_LOAN_ID = "syn-loan-1"
_ACCOUNT_IBAN = "CY79002001280000999900000001"
_DAIRY = ("Kyrenia Dairy (synthetic)", "CY52002001280000999900000002")
_FLOUR = ("Mesaoria Mills (synthetic)", "CY25002001280000999900000003")
_JCC = "JCC Payment Systems (synthetic)"
_LANDLORD = "Landlord (synthetic)"
_LENDER = "Bank of Cyprus loan (synthetic)"


class SeedMonth(BaseModel):
    """The synthetic month: what the store loads and the replay plays.

    `account.balance_cents` is the balance at the start of `first_day`. `transactions`
    and `card_sales` include the history before `first_day`; the replay plays only the
    records dated from `first_day` to `last_day`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    business: Business
    account: Account
    loan: Loan
    transactions: tuple[Transaction, ...]
    invoices: tuple[SupplierInvoice, ...]
    card_sales: tuple[CardSale, ...]
    events: tuple[Event, ...]
    first_day: dt.date
    last_day: dt.date

    @model_validator(mode="after")
    def _all_synthetic(self) -> Self:
        if not self.business.synthetic:
            raise ValueError("the seed business must be synthetic")
        if self.first_day > self.last_day:
            raise ValueError(f"first_day {self.first_day} is after last_day {self.last_day}")
        ids = [self.business.id, self.account.id, self.loan.id]
        ids += [record.id for record in self.transactions]
        ids += [record.id for record in self.invoices]
        ids += [record.id for record in self.card_sales]
        ids += [invoice.jinius_id for invoice in self.invoices]
        ids += [sale.jcc_order_id for sale in self.card_sales]
        ids += [event.id for event in self.events]
        unmarked = [record_id for record_id in ids if not record_id.startswith(ID_PREFIX)]
        if unmarked:
            raise ValueError(f"ids without the {ID_PREFIX!r} prefix: {unmarked}")
        if len(set(ids)) != len(ids):
            raise ValueError("the seed has duplicate ids")
        names = [self.business.name]
        names += [invoice.supplier_name for invoice in self.invoices]
        names += [record.counterparty for record in self.transactions]
        names += [event.customer for event in self.events]
        unnamed = [name for name in names if not name.endswith(SYNTHETIC)]
        if unnamed:
            raise ValueError(f"names without {SYNTHETIC!r}: {unnamed}")
        return self


def _days(first: dt.date, last: dt.date) -> list[dt.date]:
    return [first + dt.timedelta(days=n) for n in range((last - first).days + 1)]


def _card_sales(rng: random.Random) -> list[CardSale]:
    sales = []
    for day in _days(HISTORY_START, LAST_DAY):
        if day.weekday() == 6:  # closed on Sundays
            continue
        for n in range(1, AMOUNTS.card_sales_per_day + 1):
            stamp = f"{day:%Y%m%d}-{n}"
            amount = rng.randint(AMOUNTS.card_sale_min_cents, AMOUNTS.card_sale_max_cents)
            sales.append(
                CardSale(
                    id=f"syn-sale-{stamp}",
                    amount_cents=amount,
                    date=day,
                    jcc_order_id=f"syn-jcc-{stamp}",
                    status="paid",
                )
            )
    return sales


def _transactions(sales: list[CardSale]) -> list[Transaction]:
    lines: list[tuple[dt.date, int, str, str]] = []
    for sale in sales:
        settled = sale.date + SETTLEMENT_DELAY
        if settled <= LAST_DAY:
            lines.append((settled, sale.amount_cents, _JCC, f"JCC {sale.jcc_order_id}"))
    for month in (HISTORY_START.replace(day=1), FIRST_DAY):
        lines.append(
            (month.replace(day=RENT_DAY), -AMOUNTS.rent_cents, _LANDLORD, f"Rent {month:%Y-%m}")
        )
        lines.append(
            (
                month.replace(day=LOAN_DAY),
                -AMOUNTS.loan_instalment_cents,
                _LENDER,
                f"Instalment {_LOAN_ID} {month:%Y-%m}",
            )
        )
    lines.sort(key=lambda line: line[0])
    return [
        Transaction(
            id=f"syn-tx-{n:04d}",
            account_id=_ACCOUNT_ID,
            date=date,
            amount_cents=amount,
            counterparty=counterparty,
            reference=reference,
        )
        for n, (date, amount, counterparty, reference) in enumerate(lines, start=1)
    ]


def _invoices() -> list[SupplierInvoice]:
    journey = [
        (_DAIRY, AMOUNTS.dairy_invoice_cents, dt.date(2026, 11, 2), dt.date(2026, 11, 9)),
        (_FLOUR, AMOUNTS.flour_invoice_cents, dt.date(2026, 11, 4), dt.date(2026, 11, 18)),
    ]
    return [
        SupplierInvoice(
            id=f"syn-inv-{n:04d}",
            supplier_name=name,
            supplier_iban=iban,
            amount_cents=amount,
            issue_date=issued,
            due_date=due,
            status=InvoiceStatus.NEW,
            jinius_id=f"syn-jinius-{n:04d}",
        )
        for n, ((name, iban), amount, issued, due) in enumerate(journey, start=1)
    ]


_PAST_EVENTS = (
    # (customer, event date, price, costs, days the costs were paid before, final paid after)
    ("Christofi wedding", dt.date(2025, 1, 25), 600_000, 360_000, 5, 7),
    ("Ioannou christening", dt.date(2025, 3, 15), 250_000, 150_000, 3, 5),
    ("Neophytou wedding", dt.date(2025, 4, 26), 900_000, 540_000, 7, 9),
    ("Papadopoulos christening", dt.date(2025, 5, 17), 300_000, 180_000, 4, 6),
    ("Charalambous wedding", dt.date(2025, 6, 14), 800_000, 480_000, 6, 8),
    ("Constantinou wedding", dt.date(2025, 7, 5), 700_000, 420_000, 5, 9),
    ("Savva christening", dt.date(2025, 9, 13), 280_000, 168_000, 3, 7),
    ("Antoniou wedding", dt.date(2025, 10, 11), 850_000, 510_000, 7, 8),
    ("Kyriacou wedding", dt.date(2026, 4, 25), 750_000, 450_000, 6, 9),
    ("Michael christening", dt.date(2026, 5, 23), 260_000, 156_000, 4, 5),
    ("Panayiotou wedding", dt.date(2026, 6, 20), 950_000, 570_000, 5, 9),
    ("Georgiades wedding", dt.date(2026, 9, 12), 820_000, 492_000, 7, 6),
)
"""Each takes a 25% deposit; costs are about 60% of the price."""


def _events() -> list[Event]:
    events = []
    for n, (customer, day, price, costs, before, after) in enumerate(_PAST_EVENTS, start=1):
        deposit = price // 4
        events.append(
            Event(
                id=f"syn-event-{n:04d}",
                customer=f"{customer} {SYNTHETIC}",
                event_date=day,
                price_cents=price,
                deposit_cents=deposit,
                costs_cents=costs,
                final_payment_cents=price - deposit,
                status=EventStatus.COMPLETED,
                costs_paid_date=day - dt.timedelta(days=before),
                final_paid_date=day + dt.timedelta(days=after),
            )
        )
    events.append(
        Event(
            id="syn-event-0013",
            customer=f"Demetriou wedding {SYNTHETIC}",
            event_date=dt.date(2026, 7, 18),
            price_cents=700_000,
            deposit_cents=175_000,
            costs_cents=0,
            final_payment_cents=525_000,
            status=EventStatus.CANCELLED,
        )
    )
    events.append(
        Event(
            id=BOOKED_EVENT_ID,
            customer=f"Georgiou wedding {SYNTHETIC}",
            event_date=dt.date(2026, 12, 19),
            price_cents=800_000,
            deposit_cents=200_000,
            costs_cents=480_000,
            final_payment_cents=600_000,
            status=EventStatus.BOOKED,
        )
    )
    return events


def synthetic_month() -> SeedMonth:
    """Build the synthetic month: the same records on every call."""
    rng = random.Random(SEED)  # noqa: S311 - synthetic data, not cryptography
    sales = _card_sales(rng)
    return SeedMonth(
        business=Business(
            id=_BUSINESS_ID,
            name="Andreou Bakery (synthetic)",
            sector="bakery",
            account_id=_ACCOUNT_ID,
            auto_pay_limit_cents=AMOUNTS.auto_pay_limit_cents,
            synthetic=True,
        ),
        account=Account(
            id=_ACCOUNT_ID,
            iban=_ACCOUNT_IBAN,
            balance_cents=AMOUNTS.opening_balance_cents,
            currency="EUR",
        ),
        loan=Loan(
            id=_LOAN_ID,
            instalment_cents=AMOUNTS.loan_instalment_cents,
            next_due_date=FIRST_DAY.replace(day=LOAN_DAY),
        ),
        transactions=tuple(_transactions(sales)),
        invoices=tuple(_invoices()),
        card_sales=tuple(sales),
        events=tuple(_events()),
        first_day=FIRST_DAY,
        last_day=LAST_DAY,
    )
