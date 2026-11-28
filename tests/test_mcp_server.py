"""The MCP tools on the synthetic month, with a fake bank and no network."""

import asyncio
import datetime as dt
import json
from dataclasses import dataclass, field

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent

from ena.gate import Gate
from ena.mcp_server import (
    SERVER_NAME,
    TOOLS,
    EnaTools,
    Ledger,
    ReplayDay,
    ToolError,
    allowed_tools,
    build_server,
    call_tool,
)
from ena.models import ApprovalRequest, Forecast, InvoiceStatus, Offer, OfferKind, Receipt
from ena.replay import TZ, DayStarted, replay
from ena.seed import BOOKED_EVENT_ID, LEAN_WEEK, synthetic_month
from ena.store import Store

MONTH = synthetic_month()
DAIRY, FLOUR = MONTH.invoices
SUPPLIERS = {DAIRY.supplier_name, FLOUR.supplier_name}
NOW = dt.datetime(2026, 11, 16, 9, 0, tzinfo=dt.UTC)


@dataclass
class FakeBank:
    status: str = "CPLT"
    calls: list[tuple[str, int, str]] = field(default_factory=list)

    def pay(self, action: str, amount_cents: int, ref: str) -> str:
        self.calls.append((action, amount_cents, ref))
        return self.status


@dataclass
class Setup:
    day: ReplayDay
    store: Store
    bank: FakeBank
    gate: Gate
    tools: EnaTools


def setup(day: dt.date) -> Setup:
    store = Store(":memory:")
    bank = FakeBank()
    gate = Gate(MONTH.business, store, bank, lambda: NOW)
    today = ReplayDay(day)
    tools = EnaTools(Ledger(MONTH, store, today), store, gate, lambda: NOW)
    return Setup(today, store, bank, gate, tools)


def balance_on(day: dt.date) -> int:
    posted = sum(t.amount_cents for t in MONTH.transactions if MONTH.first_day <= t.date <= day)
    return MONTH.account.balance_cents + posted


def test_the_balance_is_the_statement_to_today_less_the_gates_payments() -> None:
    s = setup(DAIRY.issue_date)
    assert s.tools.get_balance().balance_cents == balance_on(DAIRY.issue_date)
    s.tools.pay_invoice(DAIRY.id)
    result = s.tools.get_balance()
    assert result.balance_cents == balance_on(DAIRY.issue_date) - DAIRY.amount_cents
    assert result.as_of == DAIRY.issue_date


def test_an_invoice_within_the_limit_is_paid_through_the_gate() -> None:
    s = setup(DAIRY.issue_date)
    s.tools.start_turn()
    result = s.tools.pay_invoice(DAIRY.id)
    assert result.status == "paid"
    assert result.amount == "€180.00"
    assert s.bank.calls == [(f"Pay {DAIRY.supplier_name} invoice {DAIRY.id}", 18_000, DAIRY.id)]
    turn = s.tools.finish_turn()
    assert isinstance(turn.cards[0], Receipt)
    assert turn.receipts == [result.receipt]
    assert s.tools.list_unpaid_invoices().invoices == ()


def test_a_repeated_pay_returns_the_same_receipt_and_pays_once() -> None:
    s = setup(DAIRY.issue_date)
    first = s.tools.pay_invoice(DAIRY.id)
    assert s.tools.pay_invoice(DAIRY.id) == first
    assert len(s.bank.calls) == 1


def test_an_invoice_above_the_limit_waits_for_the_owner() -> None:
    s = setup(FLOUR.issue_date)
    s.tools.start_turn()
    result = s.tools.pay_invoice(FLOUR.id)
    assert result.status == "awaiting_approval"
    assert result.receipt is None
    assert isinstance(result.approval, ApprovalRequest)
    assert s.bank.calls == []
    assert s.tools.finish_turn().receipts == []
    unpaid = {i.id: i.status for i in s.tools.list_unpaid_invoices().invoices}
    assert unpaid == {DAIRY.id: InvoiceStatus.NEW, FLOUR.id: InvoiceStatus.AWAITING_APPROVAL}


@pytest.mark.parametrize("invoice_id", ["syn-inv-9999", FLOUR.id])
def test_an_unknown_or_future_invoice_is_refused(invoice_id: str) -> None:
    s = setup(DAIRY.issue_date)  # the flour invoice arrives two days later
    with pytest.raises(ToolError, match="has arrived"):
        s.tools.pay_invoice(invoice_id)
    assert s.bank.calls == []


def test_money_tools_take_ids_and_no_tool_approves_or_changes_the_limit() -> None:
    names = {spec.name for spec in TOOLS}
    assert not {n for n in names if any(w in n for w in ("approve", "decline", "limit"))}
    pay = next(spec for spec in TOOLS if spec.name == "pay_invoice")
    assert set(pay.schema["properties"]) == {"invoice_id"}
    assert pay.schema["additionalProperties"] is False
    for spec in TOOLS:
        assert not {"amount", "amount_cents", "iban", "payee"} & set(spec.schema["properties"])
    assert allowed_tools() == [f"mcp__ena__{n}" for n in [spec.name for spec in TOOLS]]


def test_results_carry_no_iban() -> None:
    s = setup(FLOUR.issue_date)
    text = call_tool(s.tools, "list_unpaid_invoices", {})
    assert FLOUR.supplier_iban not in text
    assert DAIRY.supplier_iban not in text
    assert json.loads(text)["total"] == "€1,420.00"


def test_the_forecast_on_the_lean_monday() -> None:
    s = setup(LEAN_WEEK)
    s.tools.start_turn()
    result = s.tools.get_forecast()
    assert result.forecast.lean_week == LEAN_WEEK
    assert result.forecast.weeks[0].closing_cents == 34_969
    assert result.weeks[0].endswith("closes €349.69")
    assert result.instalment == "€400.00"
    assert result.instalment_due == dt.date(2026, 11, 25)
    assert s.tools.finish_turn().cards == [result.forecast]


def test_paying_the_invoices_does_not_change_the_closings() -> None:
    # Unpaid, both invoices go out in the first week; paid, they left the opening
    s = setup(LEAN_WEEK)
    before = s.tools.get_forecast().forecast
    s.tools.pay_invoice(DAIRY.id)
    approval = s.tools.pay_invoice(FLOUR.id).approval
    assert approval is not None
    s.gate.approve(approval.id)
    after = s.tools.get_forecast().forecast
    assert [w.closing_cents for w in after.weeks] == [w.closing_cents for w in before.weeks]
    assert after.lean_week == before.lean_week == LEAN_WEEK
    assert after.weeks[0].out_cents == before.weeks[0].out_cents - 142_000


def test_a_forecast_after_the_instalment_rolls_the_loan_forward() -> None:
    s = setup(MONTH.last_day)
    result = s.tools.get_forecast()
    assert result.instalment_due == dt.date(2026, 12, 25)
    assert isinstance(result.forecast, Forecast)


def test_a_forecast_without_four_weeks_of_history_is_refused() -> None:
    s = setup(MONTH.first_day)  # a Sunday: its week starts Monday 26 October
    with pytest.raises(ToolError, match="history"):
        s.tools.get_forecast()


def test_the_reserve_and_the_loan_offers_on_the_lean_monday() -> None:
    s = setup(LEAN_WEEK)
    s.tools.start_turn()
    reserve = s.tools.propose_reserve()
    assert reserve.offer is not None
    assert reserve.offer.kind is OfferKind.RESERVE
    assert reserve.amount == "€400.00"
    loan = s.tools.propose_loan()
    assert loan.offer is not None
    assert loan.offer.simulated is True
    assert loan.amount == "€1,000.00"
    assert "simulated" in loan.note
    cards = s.tools.finish_turn().cards
    assert [type(c) for c in cards] == [Offer, Offer]
    assert s.bank.calls == []


def test_the_event_pattern_reads_the_seeds_events() -> None:
    result = setup(LEAN_WEEK).tools.get_event_pattern()
    assert result.pattern is not None
    assert (result.pattern.completed, result.pattern.cancelled) == (12, 1)
    assert result.deposit_share == "25.00%"
    assert result.cost_share == "60.00%"
    assert result.costs_paid == "3 to 7 days before the event"
    assert result.final_payment_arrives == "5 to 9 days after the event"
    assert [e.id for e in result.booked] == [BOOKED_EVENT_ID]
    assert result.booked[0].final_payment == "€6,000.00"


def test_the_event_advance_on_the_lean_monday_has_no_spare_cash() -> None:
    s = setup(LEAN_WEEK)
    s.tools.start_turn()
    result = s.tools.propose_event_advance(BOOKED_EVENT_ID)
    assert result.offer is not None
    assert result.offer.kind is OfferKind.EVENT_ADVANCE
    assert result.offer.simulated is True
    assert result.amount == "€2,800.00"
    assert result.offer.term_days == 16
    assert result.offer.basis["spare_cents"] == 0
    assert "simulated" in result.note
    assert s.tools.finish_turn().cards == [result.offer]
    assert s.bank.calls == []


def test_spare_cash_is_the_lowest_closing_above_the_threshold_until_the_costs() -> None:
    # On Monday 30 November the costs fall due 12 December (7 days before the 19th): the
    # weeks of 30 November and 7 December count, the week of 14 December doesn't
    s = setup(MONTH.last_day)
    weeks = s.tools.get_forecast().forecast.weeks
    threshold = 40_000 + 55_000
    lowest = min(weeks[0].closing_cents, weeks[1].closing_cents)
    assert weeks[2].closing_cents < lowest  # the week after the costs is ignored
    result = s.tools.propose_event_advance(BOOKED_EVENT_ID)
    assert result.offer is not None
    assert result.offer.basis["spare_cents"] == lowest - threshold == 42_646
    assert result.amount == "€2,373.54"


def test_the_event_advance_leaves_one_receipt() -> None:
    s = setup(LEAN_WEEK)
    s.tools.propose_event_advance(BOOKED_EVENT_ID)
    s.tools.propose_event_advance(BOOKED_EVENT_ID)
    receipts = s.store.receipts()
    assert len(receipts) == 1
    assert receipts[0].ref == f"advance-{BOOKED_EVENT_ID}-280000"
    assert receipts[0].result == "offered"


def test_a_completed_event_gets_no_advance_and_no_receipt() -> None:
    s = setup(LEAN_WEEK)
    result = s.tools.propose_event_advance("syn-event-0001")
    assert result.offer is None
    assert result.note == "No advance: the event is completed, not booked"
    assert s.store.receipts() == []


def test_an_advance_at_another_amount_gets_its_own_receipt() -> None:
    # 2 November leaves spare cash before the costs (€2,134.53); the lean Monday none
    s = setup(dt.date(2026, 11, 2))
    first = s.tools.propose_event_advance(BOOKED_EVENT_ID)
    s.day.observe(DayStarted(at=NOW, day=LEAN_WEEK))
    second = s.tools.propose_event_advance(BOOKED_EVENT_ID)
    assert first.offer is not None and second.offer is not None
    assert first.offer.id != second.offer.id
    receipts = s.store.receipts()
    assert [(r.ref, r.amount_cents) for r in receipts] == [
        (first.offer.id, first.offer.amount_cents),
        (second.offer.id, 280_000),
    ]
    assert first.offer.amount_cents != 280_000


def test_on_the_first_day_a_refusal_needs_no_forecast() -> None:
    # 1 November: the forecast has no history yet, but a completed event is refused anyway
    s = setup(MONTH.first_day)
    result = s.tools.propose_event_advance("syn-event-0001")
    assert result.note == "No advance: the event is completed, not booked"
    with pytest.raises(ToolError, match="no forecast"):
        s.tools.propose_event_advance(BOOKED_EVENT_ID)


def test_an_unknown_event_is_refused() -> None:
    with pytest.raises(ToolError, match="no event"):
        setup(LEAN_WEEK).tools.propose_event_advance("syn-event-9999")


def test_the_loan_offer_leaves_one_receipt() -> None:
    s = setup(LEAN_WEEK)
    s.tools.propose_loan()
    s.tools.propose_loan()
    receipts = s.store.receipts()
    assert len(receipts) == 1
    assert receipts[0].ref == "loan-20261116-100000"
    assert receipts[0].result == "offered"
    assert receipts[0].amount_cents == 100_000


def test_list_receipts_is_newest_first_and_bounded() -> None:
    s = setup(FLOUR.issue_date)
    s.tools.pay_invoice(DAIRY.id)
    s.gate.decline(s.tools.pay_invoice(FLOUR.id).approval.id)  # type: ignore[union-attr]
    s.tools.start_turn()
    receipts = s.tools.list_receipts(5).receipts
    assert [r.ref for r in receipts] == [FLOUR.id, DAIRY.id]
    turn = s.tools.finish_turn()
    assert turn.receipts == []  # read, not written this turn
    assert turn.read == list(receipts)
    assert {i.status for i in s.tools.list_unpaid_invoices().invoices} == {InvoiceStatus.DECLINED}
    for bad in (0, 51):
        with pytest.raises(ToolError, match="limit"):
            s.tools.list_receipts(bad)


def test_transactions_and_books_stop_at_today() -> None:
    s = setup(dt.date(2026, 11, 10))
    result = s.tools.list_transactions("2026-11-01", "2026-11-30")
    assert result.transactions
    assert max(tx.date for tx in result.transactions) <= dt.date(2026, 11, 10)
    books = s.tools.get_books("2026-11-01", "2026-11-10")
    assert books.matched_card_sales > 0
    assert {i.id for i in books.unmatched_invoices} == {DAIRY.id, FLOUR.id}
    with pytest.raises(ToolError, match="YYYY-MM-DD"):
        s.tools.list_transactions("1 Nov", "2026-11-30")
    with pytest.raises(ToolError, match="after"):
        s.tools.list_transactions("2026-11-30", "2026-11-01")


@dataclass
class Calendar:
    """The business's day and the gate's clock together, as the replay moves them."""

    day: dt.date

    def today(self) -> dt.date:
        return self.day

    def now(self) -> dt.datetime:
        return dt.datetime.combine(self.day, dt.time(9, 0), tzinfo=TZ)


def pay_on_arrival() -> tuple[Calendar, EnaTools, dict[dt.date, int]]:
    """Play the month, paying each invoice through the gate the day it arrives (the owner
    approves the flour); return the calendar, the tools and each day's balance."""
    calendar = Calendar(MONTH.first_day)
    store = Store(":memory:")
    gate = Gate(MONTH.business, store, FakeBank(), calendar.now)
    tools = EnaTools(Ledger(MONTH, store, calendar.today), store, gate, calendar.now)
    balances = {}
    day = MONTH.first_day
    while day <= MONTH.last_day:
        calendar.day = day
        for invoice in tools.list_unpaid_invoices().invoices:
            approval = tools.pay_invoice(invoice.id).approval
            if approval is not None:
                gate.approve(approval.id)
        balances[day] = tools.get_balance().balance_cents
        day += dt.timedelta(days=1)
    return calendar, tools, balances


def test_the_books_match_the_invoices_the_gate_paid() -> None:
    calendar, tools, _ = pay_on_arrival()
    assert calendar.today() == MONTH.last_day
    books = tools.get_books("2026-11-01", "2026-11-30")
    assert books.unmatched_invoices == ()
    assert books.matched_invoices == (
        f"{DAIRY.id} {DAIRY.supplier_name} €180.00 paid {DAIRY.issue_date} (reference)",
        f"{FLOUR.id} {FLOUR.supplier_name} €1,240.00 paid {FLOUR.issue_date} (reference)",
    )
    assert {tx.amount_cents for tx in books.unmatched_payments}.isdisjoint({-18_000, -124_000})


def test_paying_on_arrival_keeps_the_seeds_balance() -> None:
    _, tools, balances = pay_on_arrival()
    lowest = min(balances, key=lambda day: balances[day])
    assert (lowest, balances[lowest]) == (dt.date(2026, 11, 20), 9_088)
    assert balances[MONTH.last_day] == balance_on(MONTH.last_day) - 142_000
    paid = tools.list_transactions("2026-11-01", "2026-11-30").transactions
    assert [(tx.date, tx.amount_cents) for tx in paid if tx.counterparty in SUPPLIERS] == [
        (DAIRY.issue_date, -18_000),
        (FLOUR.issue_date, -124_000),
    ]


def test_a_declined_invoice_stays_unmatched() -> None:
    s = setup(MONTH.last_day)
    s.gate.decline(s.tools.pay_invoice(FLOUR.id).approval.id)  # type: ignore[union-attr]
    books = s.tools.get_books("2026-11-01", "2026-11-30")
    assert {i.id: i.status for i in books.unmatched_invoices} == {
        DAIRY.id: InvoiceStatus.NEW,
        FLOUR.id: InvoiceStatus.DECLINED,
    }
    assert books.matched_invoices == ()


def test_a_gate_payment_already_on_the_statement_is_counted_once() -> None:
    bank_line = type(MONTH.transactions[0]).model_validate(
        {
            "id": "syn-tx-bank-dairy",
            "account_id": MONTH.account.id,
            "date": DAIRY.due_date,
            "amount_cents": -DAIRY.amount_cents,
            "counterparty": DAIRY.supplier_name,
            "reference": f"SEPA {DAIRY.jinius_id}",
        }
    )
    month = MONTH.model_copy(update={"transactions": (*MONTH.transactions, bank_line)})
    store = Store(":memory:")
    gate = Gate(MONTH.business, store, FakeBank(), lambda: NOW)
    tools = EnaTools(Ledger(month, store, lambda: MONTH.last_day), store, gate, lambda: NOW)
    before = tools.get_balance().balance_cents
    tools.pay_invoice(DAIRY.id)
    assert tools.get_balance().balance_cents == before
    lines = tools.list_transactions("2026-11-01", "2026-11-30").transactions
    assert [tx.id for tx in lines if tx.counterparty == DAIRY.supplier_name] == [bank_line.id]
    books = tools.get_books("2026-11-01", "2026-11-30")
    assert books.matched_invoices[0].startswith(f"{DAIRY.id} ")
    assert not {tx.amount_cents for tx in books.unmatched_payments} & {-DAIRY.amount_cents}


def test_a_bank_line_for_one_invoice_leaves_the_suppliers_other_invoice_to_the_gate() -> None:
    second = DAIRY.model_copy(
        update={
            "id": "syn-inv-0003",
            "jinius_id": "syn-jinius-0003",
            "issue_date": dt.date(2026, 11, 5),
            "due_date": dt.date(2026, 11, 12),
        }
    )
    bank_line = type(MONTH.transactions[0]).model_validate(
        {
            "id": "syn-tx-bank-dairy",
            "account_id": MONTH.account.id,
            "date": DAIRY.due_date,
            "amount_cents": -DAIRY.amount_cents,
            "counterparty": DAIRY.supplier_name,
            "reference": f"SEPA {DAIRY.jinius_id}",
        }
    )
    month = MONTH.model_copy(
        update={
            "transactions": (*MONTH.transactions, bank_line),
            "invoices": (*MONTH.invoices, second),
        }
    )
    store = Store(":memory:")
    gate = Gate(MONTH.business, store, FakeBank(), lambda: NOW)
    tools = EnaTools(Ledger(month, store, lambda: MONTH.last_day), store, gate, lambda: NOW)
    before = tools.get_balance().balance_cents
    tools.pay_invoice(second.id)
    assert tools.get_balance().balance_cents == before - second.amount_cents
    lines = tools.list_transactions("2026-11-01", "2026-11-30").transactions
    dairy = [tx.reference for tx in lines if tx.counterparty == DAIRY.supplier_name]
    assert dairy == [bank_line.reference, f"Invoice {second.jinius_id}"]
    books = tools.get_books("2026-11-01", "2026-11-30")
    assert [m.split()[0] for m in books.matched_invoices] == [DAIRY.id, second.id]


def test_the_replay_moves_the_day() -> None:
    day = ReplayDay(MONTH.first_day)
    replay(MONTH, day.observe, duration=dt.timedelta(0))
    assert day() == MONTH.last_day


def test_call_tool_and_the_server() -> None:
    s = setup(DAIRY.issue_date)
    assert json.loads(call_tool(s.tools, "pay_invoice", {"invoice_id": DAIRY.id}))["status"] == (
        "paid"
    )
    with pytest.raises(ToolError, match="no tool"):
        call_tool(s.tools, "approve", {})
    server = build_server(s.tools)
    assert server["type"] == "sdk"
    assert server["name"] == SERVER_NAME


def test_the_server_lists_and_calls_the_tools_over_mcp() -> None:
    s = setup(DAIRY.issue_date)
    server = build_server(s.tools)["instance"]

    async def session() -> tuple[list[str], str, bool]:
        async with create_connected_server_and_client_session(server) as client:
            listed = [t.name for t in (await client.list_tools()).tools]
            paid = await client.call_tool("pay_invoice", {"invoice_id": DAIRY.id})
            bad = await client.call_tool("pay_invoice", {"invoice_id": DAIRY.id, "amount": 1})
            block = paid.content[0]
            assert isinstance(block, TextContent)
            return listed, block.text, bad.isError

    listed, text, refused = asyncio.run(session())
    assert listed == [spec.name for spec in TOOLS]
    assert json.loads(text)["status"] == "paid"
    assert refused is True
    assert len(s.bank.calls) == 1
