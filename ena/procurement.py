"""Procurement: requests for quotation, the comparison, the owner's award and the order.

The loop, Ena's side of it (docs/jinius-notes.md §9 says why it lives here):

1. **Request for quotation.** Ena drafts one when the owner asks (`request_quotes`): the
   items, the suppliers, when it is needed by and when replies are asked by. Jinius has no
   API for this (its Tenders take a document in the portal), so the request is drafted
   here and sent outside Jinius: the request is `simulated` and its receipt says so
2. **Quotations.** Suppliers' replies are records of the month (`ena/seed.py`); a reply to
   a request Ena drafted arrives outside the demo, so it has none until then
3. **Comparison.** `compare` ranks the replies with `ena/rules/procurement.py` and
   recommends the cheapest complete, on-time, valid one. It reads only
4. **Award.** Only the owner awards, with a tap on the comparison card (`ena/api.py`),
   never through a tool: `award` issues the purchase order and writes the owner's
   receipt, in one transaction. A request is ordered once; a quotation that no longer
   holds is refused. Jinius has no purchase orders either, so the order is `simulated`:
   it goes to the supplier outside Jinius
5. **Invoice match.** The supplier's Jinius invoice names the order (`purchase_order_id`).
   `check_invoice` matches it (supplier and amount); `pay_invoice` holds a mismatch
   instead of asking the gate to pay it (`ena/mcp_server.py`). A matched invoice goes
   through the gate like any other: within the limit Ena pays, above it the owner approves

The amounts are the quotations'; no figure is computed outside the rules and the models.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Sequence
from typing import Protocol

from ena.gate import Clock
from ena.models import (
    Actor,
    ItemLine,
    PurchaseOrder,
    Quotation,
    QuoteComparison,
    QuoteRequest,
    Receipt,
    SupplierInvoice,
)
from ena.rules.forecast import euros
from ena.rules.procurement import InvoiceMatch, compare_quotes, match_invoice
from ena.store import Store, StoreError

DRAFTED = "drafted"
ORDERED = "ordered"

REPLY_DAYS = dt.timedelta(days=3)
"""How long suppliers are given to reply, unless the items are needed sooner."""

MAX_ITEMS = 10
MAX_SUPPLIERS = 5

NOT_ON_JINIUS = (
    "Jinius has no API for requests for quotation or purchase orders, so this one is sent"
    " outside Jinius (simulated)"
)


class ProcurementError(ValueError):
    """A request the procurement refuses: an unknown id, a request already ordered, a
    quotation that no longer holds, or a malformed request for quotation."""


class ProcurementData(Protocol):
    """What procurement reads: the month's records and the store's, as of today
    (`ena.mcp_server.Ledger`)."""

    def today(self) -> dt.date: ...

    def quote_requests(self) -> list[QuoteRequest]: ...

    def quotations(self, request_id: str) -> list[Quotation]: ...

    def purchase_orders(self) -> list[PurchaseOrder]: ...


class Procurement:
    def __init__(self, data: ProcurementData, store: Store, clock: Clock) -> None:
        self._data = data
        self._store = store
        self._clock = clock

    # Reads

    def request(self, request_id: str) -> QuoteRequest:
        for request in self._data.quote_requests():
            if request.id == request_id:
                return request
        raise ProcurementError(f"no request for quotation {request_id!r}")

    def order(self, order_id: str) -> PurchaseOrder | None:
        return next((o for o in self._data.purchase_orders() if o.id == order_id), None)

    def order_for(self, request_id: str) -> PurchaseOrder | None:
        return next((o for o in self._data.purchase_orders() if o.request_id == request_id), None)

    def compare(self, request_id: str) -> QuoteComparison:
        """The request's quotations ranked, with the order if one was issued."""
        request = self.request(request_id)
        comparison = compare_quotes(request, self._data.quotations(request.id), self._data.today())
        order = self.order_for(request.id)
        if order is None:
            return comparison
        return comparison.model_copy(
            update={
                "awarded": order.quotation_id,
                "order_id": order.id,
                "reason": f"Ordered from {order.supplier_name} ({order.id},"
                f" {euros(order.total_cents)}). {comparison.reason}",
            }
        )

    def check_invoice(self, invoice: SupplierInvoice) -> InvoiceMatch | None:
        """How an invoice that names a purchase order matches it; None when it names none."""
        if invoice.purchase_order_id is None:
            return None
        order = self.order(invoice.purchase_order_id)
        if order is None:
            return InvoiceMatch(
                order_id=invoice.purchase_order_id,
                matched=False,
                issues=(
                    f"names the order {invoice.purchase_order_id}, which Ena has no record of",
                ),
            )
        return match_invoice(invoice, order)

    # Writes

    def request_quotes(
        self,
        title: str,
        items: Sequence[ItemLine],
        suppliers: Sequence[str],
        needed_by: dt.date,
    ) -> tuple[QuoteRequest, Receipt]:
        """Draft a request for quotation, with Ena's receipt; the same request asked again
        on the same day gets the same answer back."""
        today = self._data.today()
        if needed_by <= today:
            raise ProcurementError(f"needed_by {needed_by} must be after today, {today}")
        if not 1 <= len(items) <= MAX_ITEMS:
            raise ProcurementError(f"a request names 1 to {MAX_ITEMS} items, got {len(items)}")
        if not 1 <= len(suppliers) <= MAX_SUPPLIERS:
            raise ProcurementError(
                f"a request goes to 1 to {MAX_SUPPLIERS} suppliers, got {len(suppliers)}"
            )
        key = "\n".join(
            [title, str(needed_by), today.isoformat(), *suppliers]
            + [f"{i.description}|{i.quantity}|{i.unit}" for i in items]
        )
        request_id = f"rfq-{hashlib.sha256(key.encode()).hexdigest()[:8]}"
        known = next((r for r in self._store.quote_requests() if r.id == request_id), None)
        if known is not None:
            receipt = self._store.receipt_for(request_id)
            if receipt is None:
                raise RuntimeError(f"request {request_id} is kept without its receipt")
            return known, receipt
        try:
            request = QuoteRequest(
                id=request_id,
                title=title,
                items=tuple(items),
                suppliers=tuple(suppliers),
                needed_by=needed_by,
                reply_by=min(today + REPLY_DAYS, needed_by),
                sent_on=today,
                simulated=True,
            )
        except ValueError as error:
            raise ProcurementError(f"not a valid request for quotation: {error}") from error
        receipt = Receipt(
            id=f"receipt-{request.id}",
            at=self._now(),
            actor=Actor.ENA,
            action=f"Draft a request for quotation: {request.title}",
            amount_cents=0,
            ref=request.id,
            result=DRAFTED,
            explanation=(
                f"To {', '.join(request.suppliers)}: {len(request.items)} items needed by"
                f" {request.needed_by}, replies asked by {request.reply_by}. {NOT_ON_JINIUS}"
            ),
        )
        self._store.add_quote_request(request, receipt)
        return request, receipt

    def award(self, quotation_id: str) -> PurchaseOrder:
        """The owner awards a quotation: issue its purchase order and the owner's receipt."""
        quote, request = self._quotation(quotation_id)
        ordered = self.order_for(request.id)
        if ordered is not None:
            raise ProcurementError(
                f"{request.title} is already ordered from {ordered.supplier_name} ({ordered.id})"
            )
        today = self._data.today()
        if quote.valid_until < today:
            raise ProcurementError(
                f"the quotation from {quote.supplier_name} expired on {quote.valid_until}:"
                " ask the supplier for a new one"
            )
        comparison = compare_quotes(request, self._data.quotations(request.id), today)
        order = PurchaseOrder(
            id=f"po-{quote.id}",
            request_id=request.id,
            quotation_id=quote.id,
            supplier_name=quote.supplier_name,
            lines=quote.lines,
            total_cents=quote.total_cents,
            deliver_by=quote.delivery_date,
            issued_at=self._now(),
            actor=Actor.OWNER,
            simulated=True,
        )
        recommended = comparison.recommended == quote.id
        receipt = Receipt(
            id=f"receipt-{order.id}",
            at=order.issued_at,
            actor=Actor.OWNER,
            action=f"Order from {quote.supplier_name}: {request.title}",
            amount_cents=order.total_cents,
            ref=order.id,
            result=ORDERED,
            explanation=(
                f"Awarded by the owner{' (Ena recommended it)' if recommended else ''}:"
                f" {euros(order.total_cents)}, delivered by {order.deliver_by}. {NOT_ON_JINIUS};"
                f" the supplier's Jinius invoice names {order.id}, and Ena pays it only if it"
                " matches"
            ),
        )
        try:
            self._store.add_purchase_order(order, receipt)
        except StoreError as error:
            raise ProcurementError(str(error)) from error
        return order

    def _quotation(self, quotation_id: str) -> tuple[Quotation, QuoteRequest]:
        for request in self._data.quote_requests():
            for quote in self._data.quotations(request.id):
                if quote.id == quotation_id:
                    return quote, request
        raise ProcurementError(f"no quotation {quotation_id!r} has arrived")

    def _now(self) -> dt.datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError(f"the clock must return a timezone-aware datetime: {now!r}")
        return now
