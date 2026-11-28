"""Ena's MCP server: the tools the agent may call (docs/build-plan.md §5).

The tools are the only way the agent touches data or money. Their rules:

- Money tools take ids, never amounts: `pay_invoice` takes an invoice id and the amount
  comes from the invoice. The only money tool, `pay_invoice`, calls the gate and nothing
  else; no tool changes the limit, approves or declines (that is the owner's tap, in
  `ena/api.py`)
- Every figure is computed by code (the rules, the gate) and comes back in the result,
  in cents and as a euro string to restate, so the agent never does arithmetic (Q-2)
- Results carry no token or secret, and no supplier IBAN: invoice text is data, and the
  payee always comes from the invoice the gate pays, never from the agent (§2, Q-3)

`Ledger` reads the synthetic month (`ena/seed.py`) as of a day, plus the store: a record
dated after today has not happened yet, a card sale booked from JCC counts, an
invoice's status comes from its gate receipt or pending approval, and a payment the gate
made is a statement line, as the bank posts it, unless the statement already pays the
invoice (so the books match it, #59). `ReplayDay` is a
replay `Handler` that moves that day forward as the month plays (`ena/replay.py`).

`EnaTools` holds the tools as plain methods, so they are tested without an agent, and
records what each chat turn produced: the cards the owner sees (approvals, receipts, the
forecast, offers) and the receipts the gate returned for a payment asked this turn, which
alone let a reply say "paid" (`ena/agent.py`); a receipt `list_receipts` only read does
not. `build_server` puts them on an in-process MCP server for the Claude Agent SDK.

Not here yet: `get_event_pattern` and `propose_event_advance` (the rule is in
`ena/rules/events.py`, but the synthetic month has no events) and
`create_pay_link` (the JCC connector has no register call).
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, ToolAnnotations, create_sdk_mcp_server
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from ena.gate import Clock, Gate, PaymentResult
from ena.models import (
    Actor,
    ApprovalRequest,
    ApprovalStatus,
    Card,
    CardSale,
    Forecast,
    InvoiceStatus,
    IsoDate,
    Loan,
    Offer,
    Receipt,
    SupplierInvoice,
    Transaction,
)
from ena.replay import TZ, DayStarted, ReplayEvent
from ena.rules.books import Books, match_books, match_invoices
from ena.rules.forecast import CashForecast, ScheduledFlow, euros, forecast_cash
from ena.rules.loan import loan_offer
from ena.rules.reserve import reserve_offer
from ena.seed import AMOUNTS, LOAN_DAY, RENT_DAY, SeedMonth
from ena.store import Store

SERVER_NAME = "ena"
"""The MCP server's name; the agent sees each tool as `mcp__ena__<tool>`."""

MAX_RECEIPTS = 50
"""The most receipts `list_receipts` returns at once."""

_WEEK = dt.timedelta(weeks=1)
_FORECAST_WEEKS = 8


class ToolError(ValueError):
    """A tool call the tools refuse: an unknown id, a malformed date, a bad limit."""


Today = Callable[[], dt.date]
"""Returns the business's current day."""


# Ledger: the month as of today


class ReplayDay:
    """Today's date, moved forward by the replay: `observe` is a replay `Handler`."""

    def __init__(self, day: dt.date) -> None:
        self._day = day

    def __call__(self) -> dt.date:
        return self._day

    def observe(self, event: ReplayEvent) -> None:
        if isinstance(event, DayStarted):
            if event.day < self._day:
                raise ValueError(f"the replay went back from {self._day} to {event.day}")
            self._day = event.day


class Ledger:
    """The synthetic month as the business knows it on `today()`, plus the store."""

    def __init__(self, month: SeedMonth, store: Store, today: Today) -> None:
        self._month = month
        self._store = store
        self._today = today

    @property
    def month(self) -> SeedMonth:
        return self._month

    def today(self) -> dt.date:
        return self._today()

    def balance_cents(self) -> int:
        """The opening balance, plus the statement lines to today, less the gate's payments."""
        return self._balance_before(self.today() + dt.timedelta(days=1))

    def transactions(self, first: dt.date, last: dt.date) -> list[Transaction]:
        """The statement lines dated `first` to `last`, both included, up to today: the
        month's, and one for each payment the gate made that the statement doesn't hold."""
        if first > last:
            raise ToolError(f"from {first} is after to {last}")
        end = min(last, self.today())
        lines = [*self._month.transactions, *(line for line, _ in self._gate_payments())]
        return sorted(
            (tx for tx in lines if first <= tx.date <= end), key=lambda tx: (tx.date, tx.id)
        )

    def invoices(self) -> list[SupplierInvoice]:
        """The invoices that have arrived by today, each with its status from the store."""
        return [
            self._with_status(invoice)
            for invoice in self._month.invoices
            if invoice.issue_date <= self.today()
        ]

    def invoice(self, invoice_id: str) -> SupplierInvoice:
        for invoice in self.invoices():
            if invoice.id == invoice_id:
                return invoice
        raise ToolError(f"no invoice {invoice_id!r} has arrived")

    def state(self) -> str:
        """A digest of what the store has changed in the month: the receipts, the invoices'
        statuses (pending approvals among them) and the card sales booked from JCC."""
        parts = [f"receipt {r.id} {r.result}" for r in self._store.receipts()]
        parts += [f"invoice {i.id} {i.status}" for i in self.invoices()]
        parts += [f"sale {s.jcc_order_id}" for s in self._store.card_sales()]
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]

    def card_sales(self, before: dt.date) -> list[CardSale]:
        """The seed's card sales and those booked from JCC, dated before `before`."""
        sales = {sale.jcc_order_id: sale for sale in self._store.card_sales()}
        sales |= {sale.jcc_order_id: sale for sale in self._month.card_sales}
        return sorted(
            (sale for sale in sales.values() if sale.date < before), key=lambda s: (s.date, s.id)
        )

    def cash_forecast(self) -> CashForecast:
        """§7's forecast from the Monday of this week (Monday to Sunday).

        The opening is the balance before that Monday less every payment the gate made,
        since a paid invoice is no longer counted as money going out. The rent is a
        known flow on day `RENT_DAY` of each month; the instalment is the loan's, rolled
        forward a month at a time to the first due date on or after the start.
        """
        today = self.today()
        start = today - dt.timedelta(days=today.weekday())
        end = start + _FORECAST_WEEKS * _WEEK
        try:
            return forecast_cash(
                start,
                self._balance_before(start),
                loan=self._loan_from(start),
                card_sales=self.card_sales(start),
                transactions=[tx for tx in self._month.transactions if tx.date < start],
                invoices=self.invoices(),
                scheduled=_rent(start, end),
            )
        except ValueError as error:
            raise ToolError(f"no forecast on {today}: {error}") from error

    def _balance_before(self, day: dt.date) -> int:
        month = self._month
        posted = sum(
            tx.amount_cents for tx in month.transactions if month.first_day <= tx.date < day
        )
        return month.account.balance_cents + posted - self._paid_out_cents()

    def _paid_out_cents(self) -> int:
        return sum(receipt.amount_cents for _, receipt in self._gate_payments())

    def _gate_payments(self) -> list[tuple[Transaction, Receipt]]:
        """Each invoice the gate paid that no line of the month's statement pays yet, with
        the line the bank posts for it: on the receipt's day (the business's time zone),
        to the supplier, with the invoice number in the reference.

        The statement is matched against all the month's invoices at once, one to one, so
        a bank line that pays one invoice never hides the gate's payment of another from
        the same supplier."""
        invoices = {invoice.id: invoice for invoice in self._month.invoices}
        on_statement = {
            match.invoice.id
            for match in match_invoices(self._month.invoices, self._month.transactions).matches
        }
        payments = []
        for receipt in self._store.receipts():
            invoice = invoices.get(receipt.ref)
            if invoice is None or receipt.result != PaymentResult.PAID:
                continue
            if invoice.id in on_statement:
                continue  # the bank's own line is on the statement: never counted twice
            line = Transaction(
                id=f"gate-{receipt.id}",
                account_id=self._month.account.id,
                date=receipt.at.astimezone(TZ).date(),
                amount_cents=-receipt.amount_cents,
                counterparty=invoice.supplier_name,
                reference=f"Invoice {invoice.jinius_id}",
            )
            payments.append((line, receipt))
        return payments

    def _with_status(self, invoice: SupplierInvoice) -> SupplierInvoice:
        receipt = self._store.receipt_for(invoice.id)
        if receipt is not None:
            status = {
                PaymentResult.PAID: InvoiceStatus.PAID,
                PaymentResult.DECLINED: InvoiceStatus.DECLINED,
            }.get(PaymentResult(receipt.result), invoice.status)
        elif self._store.pending_approval_for(invoice.id) is not None:
            status = InvoiceStatus.AWAITING_APPROVAL
        else:
            status = invoice.status
        return invoice.model_copy(update={"status": status})

    def _loan_from(self, start: dt.date) -> Loan:
        loan = self._month.loan
        due = loan.next_due_date
        while due < start:
            year, month = divmod(due.month, 12)
            due = dt.date(due.year + year, month + 1, LOAN_DAY)
        return loan.model_copy(update={"next_due_date": due})


def _rent(start: dt.date, end: dt.date) -> list[ScheduledFlow]:
    flows = []
    month = start.replace(day=1)
    while month < end:
        day = month.replace(day=RENT_DAY)
        if start <= day < end:
            flows.append(ScheduledFlow(date=day, amount_cents=-AMOUNTS.rent_cents, label="rent"))
        year, index = divmod(month.month, 12)
        month = dt.date(month.year + year, index + 1, 1)
    return flows


# Tool results


class _Result(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class BalanceResult(_Result):
    as_of: dt.date
    balance_cents: int
    balance: str
    currency: str


class TransactionView(_Result):
    id: str
    date: dt.date
    amount_cents: int
    amount: str
    """Positive for money in, negative for money out."""
    counterparty: str
    reference: str


class TransactionsResult(_Result):
    from_date: dt.date
    to_date: dt.date
    transactions: tuple[TransactionView, ...]
    money_in: str
    money_out: str


class InvoiceView(_Result):
    """An invoice without its IBAN: the payee is the gate's business, not the agent's."""

    id: str
    supplier_name: str
    amount_cents: int
    amount: str
    issue_date: dt.date
    due_date: dt.date
    status: InvoiceStatus


class InvoicesResult(_Result):
    as_of: dt.date
    invoices: tuple[InvoiceView, ...]
    total: str


class BooksResult(_Result):
    from_date: dt.date
    to_date: dt.date
    matched_invoices: tuple[str, ...]
    """One line per invoice matched to its payment."""
    matched_card_sales: int
    unmatched_invoices: tuple[InvoiceView, ...]
    unmatched_card_sales: tuple[str, ...]
    unmatched_payments: tuple[TransactionView, ...]
    unmatched_deposits: tuple[TransactionView, ...]


class ForecastResult(_Result):
    forecast: Forecast
    weeks: tuple[str, ...]
    """One line per week, in euros."""
    instalment: str
    instalment_due: dt.date
    expected_card_sales_per_week: str
    buffer: str
    threshold: str


class ReceiptsResult(_Result):
    receipts: tuple[Receipt, ...]
    """Newest first."""


PayStatus = Literal["paid", "failed", "declined", "awaiting_approval"]


class PayResult(_Result):
    invoice_id: str
    status: PayStatus
    amount: str
    receipt: Receipt | None
    approval: ApprovalRequest | None
    note: str


class OfferResult(_Result):
    offer: Offer | None
    amount: str | None
    note: str


# The tools


@dataclass
class Turn:
    """What the tools produced during one chat turn."""

    cards: list[Card] = field(default_factory=list)
    """For the owner: approval requests, receipts, the forecast, offers."""
    receipts: list[Receipt] = field(default_factory=list)
    """The receipts the gate returned for a payment asked this turn (`pay_invoice`), which
    alone confirm a "paid" claim; receipts a tool only read (`list_receipts`) are not here."""
    read: list[Receipt] = field(default_factory=list)
    """The receipts `list_receipts` read this turn: a paid one backs only a sentence that
    cites its id, a payment made earlier (`ena/agent.py`)."""


class EnaTools:
    def __init__(self, ledger: Ledger, store: Store, gate: Gate, clock: Clock) -> None:
        self._ledger = ledger
        self._store = store
        self._gate = gate
        self._clock = clock
        self._turn = Turn()

    def start_turn(self) -> None:
        self._turn = Turn()

    def current_turn(self) -> Turn:
        return self._turn

    def finish_turn(self) -> Turn:
        turn, self._turn = self._turn, Turn()
        return turn

    def state(self) -> str:
        """The ledger's state digest, part of the reply cache's key (`ena/cache.py`)."""
        return self._ledger.state()

    # Reads

    def get_balance(self) -> BalanceResult:
        cents = self._ledger.balance_cents()
        return BalanceResult(
            as_of=self._ledger.today(),
            balance_cents=cents,
            balance=euros(cents),
            currency=self._ledger.month.account.currency,
        )

    def list_transactions(self, from_date: str, to_date: str) -> TransactionsResult:
        first, last = _date(from_date, "from"), _date(to_date, "to")
        lines = self._ledger.transactions(first, last)
        return TransactionsResult(
            from_date=first,
            to_date=last,
            transactions=tuple(_transaction(tx) for tx in lines),
            money_in=euros(sum(tx.amount_cents for tx in lines if tx.amount_cents > 0)),
            money_out=euros(-sum(tx.amount_cents for tx in lines if tx.amount_cents < 0)),
        )

    def list_unpaid_invoices(self) -> InvoicesResult:
        unpaid = [i for i in self._ledger.invoices() if i.status is not InvoiceStatus.PAID]
        return InvoicesResult(
            as_of=self._ledger.today(),
            invoices=tuple(_invoice(i) for i in unpaid),
            total=euros(sum(i.amount_cents for i in unpaid)),
        )

    def get_books(self, from_date: str, to_date: str) -> BooksResult:
        first, last = _date(from_date, "from"), _date(to_date, "to")
        lines = self._ledger.transactions(first, last)
        sales = [s for s in self._ledger.card_sales(last + dt.timedelta(days=1)) if s.date >= first]
        books: Books = match_books(self._ledger.invoices(), sales, lines)
        return BooksResult(
            from_date=first,
            to_date=last,
            matched_invoices=tuple(
                f"{m.invoice.id} {m.invoice.supplier_name} {euros(m.invoice.amount_cents)}"
                f" paid {m.payment.date} ({m.basis})"
                for m in books.invoice_matches
            ),
            matched_card_sales=len(books.sale_matches),
            unmatched_invoices=tuple(_invoice(i) for i in books.unmatched_invoices),
            unmatched_card_sales=tuple(
                f"{s.id} {s.date} {euros(s.amount_cents)}" for s in books.unmatched_sales
            ),
            unmatched_payments=tuple(_transaction(tx) for tx in books.unmatched_payments),
            unmatched_deposits=tuple(_transaction(tx) for tx in books.unmatched_deposits),
        )

    def get_forecast(self) -> ForecastResult:
        cash = self._ledger.cash_forecast()
        self._turn.cards.append(cash.forecast)
        return ForecastResult(
            forecast=cash.forecast,
            weeks=tuple(
                f"week of {w.week_start}: opens {euros(w.opening_cents)}, in {euros(w.in_cents)},"
                f" out {euros(w.out_cents)}, closes {euros(w.closing_cents)}"
                for w in cash.forecast.weeks
            ),
            instalment=euros(cash.instalment_cents),
            instalment_due=cash.instalment_due,
            expected_card_sales_per_week=euros(cash.expected_card_sales_cents),
            buffer=euros(cash.buffer_cents),
            threshold=euros(cash.threshold_cents),
        )

    def list_receipts(self, limit: int) -> ReceiptsResult:
        """The latest receipts, read only: they are not this turn's (`Turn.receipts`)."""
        if type(limit) is not int or not 1 <= limit <= MAX_RECEIPTS:
            raise ToolError(f"limit must be an integer from 1 to {MAX_RECEIPTS}, got {limit!r}")
        receipts = tuple(reversed(self._store.receipts()[-limit:]))
        self._turn.read.extend(receipts)
        return ReceiptsResult(receipts=receipts)

    # Money and offers

    def pay_invoice(self, invoice_id: str) -> PayResult:
        """Ask the gate to pay an invoice: paid within the limit, an approval above it."""
        invoice = self._ledger.invoice(invoice_id)
        outcome = self._gate.request_payment(
            f"Pay {invoice.supplier_name} invoice {invoice.id}", invoice.amount_cents, invoice.id
        )
        self._turn.cards.append(outcome)
        amount = euros(invoice.amount_cents)
        if isinstance(outcome, ApprovalRequest):
            if outcome.status is not ApprovalStatus.PENDING:
                raise RuntimeError(f"the gate returned a decided approval {outcome.id}")
            return PayResult(
                invoice_id=invoice.id,
                status="awaiting_approval",
                amount=amount,
                receipt=None,
                approval=outcome,
                note=f"Not paid: {outcome.reason}; it waits for the owner's approval",
            )
        self._turn.receipts.append(outcome)
        statuses: dict[PaymentResult, PayStatus] = {
            PaymentResult.PAID: "paid",
            PaymentResult.FAILED: "failed",
            PaymentResult.DECLINED: "declined",
        }
        status = statuses[PaymentResult(outcome.result)]
        return PayResult(
            invoice_id=invoice.id,
            status=status,
            amount=amount,
            receipt=outcome,
            approval=None,
            note=outcome.explanation,
        )

    def propose_reserve(self) -> OfferResult:
        """The reserve rule's offer; it moves no money (the owner decides first)."""
        offer = reserve_offer(self._ledger.cash_forecast())
        if offer is None:
            return OfferResult(offer=None, amount=None, note="No lean week before the instalment")
        self._turn.cards.append(offer)
        return OfferResult(
            offer=offer,
            amount=euros(offer.amount_cents),
            note=(
                f"Set aside the {euros(offer.amount_cents)} instalment now, for"
                f" {offer.term_days} days; nothing has moved yet"
            ),
        )

    def propose_loan(self) -> OfferResult:
        """The loan rule's simulated offer, with a receipt that Ena offered it."""
        offer = loan_offer(self._ledger.cash_forecast())
        if offer is None:
            return OfferResult(offer=None, amount=None, note="No gap the reserve doesn't cover")
        self._turn.cards.append(offer)
        if self._store.receipt_for(offer.id) is None:
            self._store.add_receipt(
                Receipt(
                    id=f"receipt-{offer.id}",
                    at=self._clock(),
                    actor=Actor.ENA,
                    action="Offer a Digital Business Loan (simulated)",
                    amount_cents=offer.amount_cents,
                    ref=offer.id,
                    result="offered",
                    explanation=(
                        f"Offer, simulated: {euros(offer.amount_cents)} for"
                        f" {offer.term_days} days; not an application and not a promise"
                    ),
                )
            )
        return OfferResult(
            offer=offer,
            amount=euros(offer.amount_cents),
            note="Offer, simulated: a pre-filled Digital Business Loan, not a promise",
        )


def _date(value: str, name: str) -> dt.date:
    try:
        return TypeAdapter(IsoDate).validate_python(value)
    except ValidationError as error:
        raise ToolError(f"{name} must be a YYYY-MM-DD date, got {value!r}") from error


def _transaction(tx: Transaction) -> TransactionView:
    return TransactionView(
        id=tx.id,
        date=tx.date,
        amount_cents=tx.amount_cents,
        amount=euros(tx.amount_cents),
        counterparty=tx.counterparty,
        reference=tx.reference,
    )


def _invoice(invoice: SupplierInvoice) -> InvoiceView:
    return InvoiceView(
        id=invoice.id,
        supplier_name=invoice.supplier_name,
        amount_cents=invoice.amount_cents,
        amount=euros(invoice.amount_cents),
        issue_date=invoice.issue_date,
        due_date=invoice.due_date,
        status=invoice.status,
    )


# The MCP server

_NO_ARGS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}
_PERIOD: dict[str, Any] = {
    "type": "object",
    "properties": {
        "from": {"type": "string", "description": "First day, YYYY-MM-DD"},
        "to": {"type": "string", "description": "Last day, YYYY-MM-DD"},
    },
    "required": ["from", "to"],
    "additionalProperties": False,
}
_READ = ToolAnnotations(read_only_hint=True)
_MONEY = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True)

Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]
    annotations: ToolAnnotations
    call: Callable[[EnaTools, dict[str, Any]], _Result]


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "get_balance",
        "The account balance today.",
        _NO_ARGS,
        _READ,
        lambda t, _: t.get_balance(),
    ),
    ToolSpec(
        "list_transactions",
        "Statement lines from one day to another (both included), up to today.",
        _PERIOD,
        _READ,
        lambda t, a: t.list_transactions(a["from"], a["to"]),
    ),
    ToolSpec(
        "list_unpaid_invoices",
        "Supplier invoices that have arrived and are not paid, with their status.",
        _NO_ARGS,
        _READ,
        lambda t, _: t.list_unpaid_invoices(),
    ),
    ToolSpec(
        "get_books",
        "Books for a period: payments matched to invoices, deposits to card sales, and"
        " what is unmatched.",
        _PERIOD,
        _READ,
        lambda t, a: t.get_books(a["from"], a["to"]),
    ),
    ToolSpec(
        "get_forecast",
        "The 8-week cash forecast from this week's Monday, with the lean week and why.",
        _NO_ARGS,
        _READ,
        lambda t, _: t.get_forecast(),
    ),
    ToolSpec(
        "list_receipts",
        "The latest receipts of what Ena and the owner did, newest first.",
        {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": MAX_RECEIPTS}},
            "required": ["limit"],
            "additionalProperties": False,
        },
        _READ,
        lambda t, a: t.list_receipts(a["limit"]),
    ),
    ToolSpec(
        "pay_invoice",
        "Ask the gate to pay a supplier invoice by its id. The gate pays within the owner's"
        " limit and otherwise creates an approval request for the owner; the amount and"
        " the payee come from the invoice.",
        {
            "type": "object",
            "properties": {"invoice_id": {"type": "string", "minLength": 1}},
            "required": ["invoice_id"],
            "additionalProperties": False,
        },
        _MONEY,
        lambda t, a: t.pay_invoice(a["invoice_id"]),
    ),
    ToolSpec(
        "propose_reserve",
        "The reserve offer for the next loan instalment when a lean week comes first, or"
        " none. Moves no money.",
        _NO_ARGS,
        _READ,
        lambda t, _: t.propose_reserve(),
    ),
    ToolSpec(
        "propose_loan",
        "A simulated Digital Business Loan offer for the gap the reserve can't cover, or none.",
        _NO_ARGS,
        _MONEY,
        lambda t, _: t.propose_loan(),
    ),
)


def allowed_tools() -> list[str]:
    """The tools' names as the agent sees them."""
    return [f"mcp__{SERVER_NAME}__{spec.name}" for spec in TOOLS]


def call_tool(tools: EnaTools, name: str, arguments: dict[str, Any]) -> str:
    """Run one tool and return its result as JSON text (what the agent reads)."""
    spec = next((spec for spec in TOOLS if spec.name == name), None)
    if spec is None:
        raise ToolError(f"no tool {name!r}")
    return spec.call(tools, arguments).model_dump_json()


def build_server(tools: EnaTools) -> McpSdkServerConfig:
    """The tools on an in-process MCP server, for the Claude Agent SDK."""

    def handler(name: str) -> Handler:
        async def run(arguments: dict[str, Any]) -> dict[str, Any]:
            text = call_tool(tools, name, arguments)
            return {"content": [{"type": "text", "text": text}]}

        return run

    sdk_tools: list[SdkMcpTool[Any]] = [
        SdkMcpTool(
            name=spec.name,
            description=spec.description,
            input_schema=spec.schema,
            handler=handler(spec.name),
            annotations=spec.annotations,
        )
        for spec in TOOLS
    ]
    return create_sdk_mcp_server(SERVER_NAME, tools=sdk_tools)
