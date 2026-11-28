"""Procurement: the comparison rule, the invoice match, the owner's award, and the tools."""

import asyncio
import datetime as dt
from collections.abc import Iterator
from dataclasses import dataclass, field

import httpx
import pytest
from fastapi.testclient import TestClient

from ena.api import create_app
from ena.cache import CachedReply, cacheable
from ena.connectors.jcc import JccClient, JccSettings
from ena.gate import Gate
from ena.mcp_server import TOOLS, EnaTools, Ledger, ReplayDay, ToolError, call_tool
from ena.models import (
    ApprovalRequest,
    ItemLine,
    PurchaseOrder,
    Quotation,
    QuoteComparison,
    QuoteLine,
    QuoteRequest,
    Receipt,
    SupplierInvoice,
)
from ena.procurement import Procurement, ProcurementError
from ena.rules.procurement import compare_quotes, match_invoice
from ena.seed import FLOUR_ORDER_ID, LEAN_WEEK, WEDDING_REQUEST_ID, SeedMonth, synthetic_month
from ena.store import Store

MONTH = synthetic_month()
DAIRY, FLOUR = MONTH.invoices
NOW = dt.datetime(2026, 11, 16, 9, 0, tzinfo=dt.UTC)
KYRENIA, PAPHOS, LIMASSOL = "syn-quote-0004", "syn-quote-0005", "syn-quote-0006"


# The rule


def _request(**changes: object) -> QuoteRequest:
    base = QuoteRequest(
        id="rfq-1",
        title="Flour",
        items=(ItemLine(description="Bread flour", quantity=100, unit="kg"),),
        suppliers=("A", "B"),
        needed_by=dt.date(2026, 12, 1),
        reply_by=dt.date(2026, 11, 20),
        sent_on=dt.date(2026, 11, 16),
        simulated=True,
    )
    return base.model_copy(update=changes)


def _quote(
    quote_id: str,
    price: int,
    *,
    quantity: int = 100,
    description: str = "Bread flour",
    unit: str = "kg",
    delivery: dt.date = dt.date(2026, 11, 28),
    valid_until: dt.date = dt.date(2026, 12, 31),
) -> Quotation:
    line = QuoteLine(
        description=description,
        quantity=quantity,
        unit=unit,
        unit_price_cents=price,
        total_cents=quantity * price,
    )
    return Quotation(
        id=quote_id,
        request_id="rfq-1",
        supplier_name=f"Supplier {quote_id}",
        lines=(line,),
        total_cents=line.total_cents,
        delivery_date=delivery,
        valid_until=valid_until,
        received_on=dt.date(2026, 11, 18),
    )


TODAY = dt.date(2026, 11, 20)


def test_the_cheapest_complete_on_time_valid_quote_is_recommended() -> None:
    quotes = [_quote("q-dear", 300), _quote("q-cheap", 250), _quote("q-mid", 280)]

    comparison = compare_quotes(_request(), quotes, TODAY)

    assert [r.quotation.id for r in comparison.quotes] == ["q-cheap", "q-mid", "q-dear"]
    assert [r.rank for r in comparison.quotes] == [1, 2, 3]
    assert comparison.recommended == "q-cheap"
    assert "€250.00" in comparison.reason
    assert "€30.00 less than Supplier q-mid" in comparison.reason


@pytest.mark.parametrize(
    ("quote", "issue"),
    [
        (_quote("q-late", 100, delivery=dt.date(2026, 12, 2)), "delivers 2026-12-02"),
        (_quote("q-expired", 100, valid_until=dt.date(2026, 11, 19)), "expired on 2026-11-19"),
        (_quote("q-short", 100, quantity=60), "60 kg quoted, 100 kg asked"),
        (_quote("q-other", 100, description="Rye flour"), "no price for Bread flour"),
        (_quote("q-unit", 100, unit="bag"), "quoted per bag, asked per kg"),
    ],
)
def test_an_ineligible_quote_ranks_after_a_dearer_eligible_one(
    quote: Quotation, issue: str
) -> None:
    comparison = compare_quotes(_request(), [quote, _quote("q-ok", 900)], TODAY)

    assert comparison.recommended == "q-ok"
    last = comparison.quotes[-1]
    assert last.quotation.id == quote.id
    assert any(issue in text for text in last.issues)


def test_with_no_eligible_quote_nothing_is_recommended() -> None:
    late = _quote("q-late", 100, delivery=dt.date(2026, 12, 9))

    comparison = compare_quotes(_request(), [late], TODAY)

    assert comparison.recommended is None
    assert "None of the 1 quotations" in comparison.reason


def test_no_quotes_yet_says_when_replies_are_due() -> None:
    comparison = compare_quotes(_request(), [], TODAY)

    assert comparison.quotes == ()
    assert comparison.reason == "No quotations yet; replies are asked by 2026-11-20"


def test_a_quote_for_another_request_or_twice_is_refused() -> None:
    other = _quote("q-1", 100).model_copy(update={"request_id": "rfq-2"})
    with pytest.raises(ValueError, match="answers rfq-2"):
        compare_quotes(_request(), [other], TODAY)
    with pytest.raises(ValueError, match="given twice"):
        compare_quotes(_request(), [_quote("q-1", 100), _quote("q-1", 100)], TODAY)


def test_a_quote_whose_totals_dont_add_up_is_refused() -> None:
    with pytest.raises(ValueError, match="total_cents"):
        QuoteLine(description="x", quantity=2, unit="kg", unit_price_cents=100, total_cents=150)
    with pytest.raises(ValueError, match="lines' sum"):
        Quotation.model_validate({**_quote("q-1", 100).model_dump(), "total_cents": 1})


def test_a_request_names_each_item_once_and_dates_in_order() -> None:
    item = ItemLine(description="Flour", quantity=1, unit="kg")
    with pytest.raises(ValueError, match="each item once"):
        QuoteRequest.model_validate({**_request().model_dump(), "items": [item, item]})
    with pytest.raises(ValueError, match="sent_on"):
        QuoteRequest.model_validate({**_request().model_dump(), "reply_by": "2026-12-05"})


# The invoice match


def test_the_flour_invoice_matches_its_order() -> None:
    (order,) = MONTH.purchase_orders

    match = match_invoice(FLOUR, order)

    assert FLOUR.purchase_order_id == FLOUR_ORDER_ID == order.id
    assert match.matched
    assert match.issues == ()


def test_an_invoice_for_another_amount_or_supplier_does_not_match() -> None:
    (order,) = MONTH.purchase_orders
    wrong = FLOUR.model_copy(update={"amount_cents": 130_000, "supplier_name": "Someone else"})

    match = match_invoice(wrong, order)

    assert not match.matched
    assert any("bills €1,300.00, but the order is for €1,240.00" in i for i in match.issues)
    assert any("from Someone else" in i for i in match.issues)


# The seed's story


def test_on_the_lean_monday_limassol_is_recommended_over_the_cheaper_late_paphos() -> None:
    s = setup(LEAN_WEEK)

    comparison = s.procurement.compare(WEDDING_REQUEST_ID)

    assert [r.quotation.id for r in comparison.quotes] == [LIMASSOL, KYRENIA, PAPHOS]
    assert comparison.recommended == LIMASSOL
    assert comparison.quotes[0].quotation.total_cents == 60_500
    assert comparison.quotes[2].quotation.total_cents == 59_350
    assert comparison.quotes[2].issues == ("delivers 2026-12-16, needed by 2026-12-14",)
    assert "€17.00 less than Kyrenia Dairy" in comparison.reason
    assert comparison.awarded is None


def test_quotes_and_requests_appear_only_once_they_have_arrived() -> None:
    s = setup(dt.date(2026, 11, 11))

    assert [q.id for q in s.ledger.quotations(WEDDING_REQUEST_ID)] == [KYRENIA]
    assert {r.id for r in setup(dt.date(2026, 11, 8)).ledger.quote_requests()} == {"syn-rfq-0001"}


# The award


@dataclass
class FakeBank:
    calls: list[tuple[str, int, str]] = field(default_factory=list)

    def pay(self, action: str, amount_cents: int, ref: str) -> str:
        self.calls.append((action, amount_cents, ref))
        return "CPLT"


@dataclass
class Setup:
    day: ReplayDay
    store: Store
    bank: FakeBank
    ledger: Ledger
    procurement: Procurement
    tools: EnaTools


def setup(day: dt.date, month: SeedMonth = MONTH) -> Setup:
    store = Store(":memory:")
    bank = FakeBank()
    gate = Gate(month.business, store, bank, lambda: NOW)
    today = ReplayDay(day)
    ledger = Ledger(month, store, today)
    return Setup(
        today,
        store,
        bank,
        ledger,
        Procurement(ledger, store, lambda: NOW),
        EnaTools(ledger, store, gate, lambda: NOW),
    )


def test_the_owner_awards_a_quote_and_gets_the_order_and_a_receipt() -> None:
    s = setup(LEAN_WEEK)

    order = s.procurement.award(LIMASSOL)

    assert order.id == f"po-{LIMASSOL}"
    assert order.total_cents == 60_500
    assert order.deliver_by == dt.date(2026, 12, 11)
    assert order.simulated
    receipt = s.store.receipt_for(order.id)
    assert receipt is not None
    assert receipt.result == "ordered"
    assert receipt.actor == "owner"
    assert "(Ena recommended it)" in receipt.explanation
    assert "Jinius has no API" in receipt.explanation
    comparison = s.procurement.compare(WEDDING_REQUEST_ID)
    assert comparison.awarded == LIMASSOL
    assert comparison.order_id == order.id
    assert s.bank.calls == []  # an order moves no money


def test_the_owner_may_award_a_quote_ena_did_not_recommend() -> None:
    s = setup(LEAN_WEEK)

    s.procurement.award(KYRENIA)

    receipt = s.store.receipt_for(f"po-{KYRENIA}")
    assert receipt is not None
    assert "recommended" not in receipt.explanation


def test_a_request_is_ordered_once() -> None:
    s = setup(LEAN_WEEK)
    s.procurement.award(LIMASSOL)

    with pytest.raises(ProcurementError, match="already ordered"):
        s.procurement.award(KYRENIA)
    with pytest.raises(ProcurementError, match="already ordered"):
        s.procurement.award(LIMASSOL)
    assert len(s.store.purchase_orders()) == 1


def test_a_request_ordered_before_ena_cannot_be_ordered_again() -> None:
    s = setup(LEAN_WEEK)

    with pytest.raises(ProcurementError, match="already ordered from Mesaoria Mills"):
        s.procurement.award("syn-quote-0002")


def test_an_expired_quote_cannot_be_awarded() -> None:
    late_month = MONTH.model_copy(
        update={
            "quotations": tuple(
                q.model_copy(update={"valid_until": dt.date(2026, 11, 20)})
                if q.id == LIMASSOL
                else q
                for q in MONTH.quotations
            )
        }
    )
    s = setup(dt.date(2026, 11, 21), late_month)
    with pytest.raises(ProcurementError, match="expired on 2026-11-20"):
        s.procurement.award(LIMASSOL)
    assert s.store.purchase_orders() == []


def test_an_unknown_or_not_yet_arrived_quote_cannot_be_awarded() -> None:
    with pytest.raises(ProcurementError, match="no quotation"):
        setup(LEAN_WEEK).procurement.award("syn-quote-9999")
    with pytest.raises(ProcurementError, match="no quotation"):
        setup(dt.date(2026, 11, 12)).procurement.award(LIMASSOL)


# The tools


def test_compare_quotes_shows_the_card_and_says_only_the_owner_awards() -> None:
    s = setup(LEAN_WEEK)
    s.tools.start_turn()

    result = s.tools.compare_quotes(WEDDING_REQUEST_ID)

    assert result.recommended == LIMASSOL
    assert [q.total for q in result.quotes] == ["€605.00", "€622.00", "€593.50"]
    assert [q.eligible for q in result.quotes] == [True, True, False]
    assert "Only the owner awards" in result.note
    (card,) = s.tools.finish_turn().cards
    assert isinstance(card, QuoteComparison)
    assert cacheable(CachedReply(text="x", cards=(card,)))


def test_an_unknown_request_is_a_tool_error() -> None:
    with pytest.raises(ToolError, match="no request for quotation"):
        setup(LEAN_WEEK).tools.compare_quotes("rfq-nope")


def test_list_quote_requests_counts_the_replies_and_names_the_order() -> None:
    result = setup(LEAN_WEEK).tools.list_quote_requests()

    by_id = {r.id: r for r in result.requests}
    assert by_id["syn-rfq-0001"].quotations_received == 3
    assert by_id["syn-rfq-0001"].order_id == FLOUR_ORDER_ID
    assert by_id[WEDDING_REQUEST_ID].quotations_received == 3
    assert by_id[WEDDING_REQUEST_ID].order_id is None


def test_request_quotes_drafts_a_request_once_with_a_receipt() -> None:
    s = setup(LEAN_WEEK)
    s.tools.start_turn()
    items = [{"description": "Eggs", "quantity": 30, "unit": "dozen"}]

    first = s.tools.request_quotes("Eggs for December", items, ["Farm A", "Farm B"], "2026-12-01")
    again = s.tools.request_quotes("Eggs for December", items, ["Farm A", "Farm B"], "2026-12-01")

    assert first.request.id == again.request.id
    assert first.request.reply_by == dt.date(2026, 11, 19)
    assert first.receipt.result == "drafted"
    assert first.receipt.amount_cents == 0
    assert "outside Jinius (simulated)" in first.receipt.explanation
    assert len(s.store.quote_requests()) == 1
    assert len([r for r in s.store.receipts() if r.ref == first.request.id]) == 1
    cards = s.tools.finish_turn().cards
    assert all(isinstance(card, Receipt) for card in cards)
    listed = {r.id: r for r in s.tools.list_quote_requests().requests}
    assert listed[first.request.id].quotations_received == 0
    comparison = s.tools.compare_quotes(first.request.id)
    assert comparison.recommended is None


@pytest.mark.parametrize(
    ("items", "suppliers", "needed_by", "message"),
    [
        ([{"description": "Eggs", "quantity": 1, "unit": "dozen"}], ["A"], "2026-11-16", "after"),
        ([{"description": "Eggs", "quantity": 0, "unit": "dozen"}], ["A"], "2026-12-01", "item"),
        ([{"description": "Eggs", "quantity": 1}], ["A"], "2026-12-01", "item"),
        ([{"description": "Eggs", "quantity": 1, "unit": "dozen"}], [], "2026-12-01", "1 to 5"),
        ([{"description": "Eggs", "quantity": 1, "unit": "dozen"}], ["A"], "1 Dec", "YYYY-MM-DD"),
    ],
)
def test_a_malformed_request_is_refused_and_keeps_nothing(
    items: list[dict[str, object]], suppliers: list[str], needed_by: str, message: str
) -> None:
    s = setup(LEAN_WEEK)

    with pytest.raises(ToolError, match=message):
        s.tools.request_quotes("Eggs", items, suppliers, needed_by)
    assert s.store.quote_requests() == []
    assert s.store.receipts() == []


def test_a_matching_invoice_goes_to_the_gate_and_says_so() -> None:
    s = setup(FLOUR.issue_date)
    s.tools.start_turn()

    result = s.tools.pay_invoice(FLOUR.id)

    assert result.status == "awaiting_approval"  # €1,240 is above the €500 limit
    assert result.order_check == f"matches the purchase order {FLOUR_ORDER_ID}"
    assert isinstance(s.tools.finish_turn().cards[0], ApprovalRequest)


def test_an_invoice_without_an_order_is_paid_as_before() -> None:
    result = setup(DAIRY.issue_date).tools.pay_invoice(DAIRY.id)

    assert result.status == "paid"
    assert result.order_check is None


def _month_with_flour(**changes: object) -> SeedMonth:
    flour = FLOUR.model_copy(update=changes)
    return MONTH.model_copy(update={"invoices": (DAIRY, flour)})


@pytest.mark.parametrize(
    ("changes", "issue"),
    [
        ({"amount_cents": 130_000}, "bills €1,300.00, but the order is for €1,240.00"),
        ({"purchase_order_id": "syn-po-0099"}, "which Ena has no record of"),
    ],
)
def test_a_mismatched_invoice_is_held_and_never_reaches_the_gate(
    changes: dict[str, object], issue: str
) -> None:
    s = setup(FLOUR.issue_date, _month_with_flour(**changes))
    s.tools.start_turn()

    result = s.tools.pay_invoice(FLOUR.id)

    assert result.status == "held"
    assert issue in result.note
    assert result.receipt is None
    assert result.approval is None
    assert s.bank.calls == []
    assert s.store.pending_approvals() == []
    assert s.tools.finish_turn().cards == []


def test_the_procurement_tools_are_on_the_server() -> None:
    names = {spec.name for spec in TOOLS}

    assert {"list_quote_requests", "compare_quotes", "request_quotes", "list_purchase_orders"} <= (
        names
    )
    assert not any("award" in name or name.startswith("issue") for name in names)


def test_list_purchase_orders_shows_the_owners_orders() -> None:
    s = setup(LEAN_WEEK)
    s.procurement.award(LIMASSOL)

    text = asyncio.run(call_tool(s.tools, "list_purchase_orders", {}))

    assert FLOUR_ORDER_ID in text
    assert f"po-{LIMASSOL}" in text
    assert "€605.00" in text


def test_an_order_changes_the_ledger_state() -> None:
    s = setup(LEAN_WEEK)
    before = s.ledger.state()

    s.procurement.award(LIMASSOL)

    assert s.ledger.state() != before


# The owner's tap


@pytest.fixture
def http() -> Iterator[tuple[TestClient, Store]]:
    s = setup(LEAN_WEEK)
    settings = JccSettings(
        api_user="ena-api", api_password="pw", callback_secret="cb-secret", _env_file=None
    )
    jcc = JccClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(_no_network)))
    app = create_app(s.store, jcc, settings.callback_secret, procurement=s.procurement)
    with TestClient(app) as client:
        yield client, s.store
    s.store.close()


def _no_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected call to {request.url}")


def test_the_award_tap_answers_with_the_order_card(http: tuple[TestClient, Store]) -> None:
    client, store = http

    response = client.post(f"/quotes/{LIMASSOL}/award")

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "card"
    assert body["card_kind"] == "order"
    order = PurchaseOrder.model_validate(body["card"])
    assert order.total_cents == 60_500
    assert store.purchase_orders() == [order]


def test_a_second_award_tap_is_refused_with_409(http: tuple[TestClient, Store]) -> None:
    client, store = http
    client.post(f"/quotes/{LIMASSOL}/award")

    response = client.post(f"/quotes/{KYRENIA}/award")

    assert response.status_code == 409
    assert "already ordered" in response.json()["detail"]
    assert len(store.purchase_orders()) == 1


def test_an_order_card_is_never_cached() -> None:
    order = setup(LEAN_WEEK).procurement.award(LIMASSOL)

    assert not cacheable(CachedReply(text="Ordered", cards=(order,)))


def test_supplier_invoices_keep_working_without_an_order() -> None:
    invoice = SupplierInvoice.model_validate(
        {k: v for k, v in DAIRY.model_dump().items() if k != "purchase_order_id"}
    )

    assert invoice.purchase_order_id is None
