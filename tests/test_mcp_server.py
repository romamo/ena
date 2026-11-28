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
from ena.replay import replay
from ena.seed import LEAN_WEEK, synthetic_month
from ena.store import Store

MONTH = synthetic_month()
DAIRY, FLOUR = MONTH.invoices
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


def test_the_loan_offer_leaves_one_receipt() -> None:
    s = setup(LEAN_WEEK)
    s.tools.propose_loan()
    s.tools.propose_loan()
    receipts = s.store.receipts()
    assert len(receipts) == 1
    assert receipts[0].ref == "loan-20261116"
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
