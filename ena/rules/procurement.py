"""Procurement rules (docs/jinius-notes.md §9): compare quotations, match an invoice to its order.

Pure functions, no I/O.

A quotation is **eligible** when it is all three of:

- **complete**: it has a line for every item the request names (descriptions compared
  case-insensitively, with runs of whitespace collapsed), in the same unit, with at least
  the quantity asked
- **on time**: its delivery date is on or before the request's `needed_by`
- **valid**: its `valid_until` is today or later

Eligible quotations rank first, cheapest total first, then the earlier delivery, then the
earlier reply, then the id; the others follow in the same order. The recommendation is the
first eligible quotation, or none. The rule only ranks: the owner awards (ena/procurement.py).

An invoice **matches** its purchase order when it comes from the order's supplier (names
compared the same way) and its amount equals the order's total to the cent. Anything else
is a mismatch, and the invoice is held instead of paid (ena/mcp_server.py, `pay_invoice`).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from ena.models import (
    PurchaseOrder,
    Quotation,
    QuoteComparison,
    QuoteRequest,
    RankedQuote,
    SupplierInvoice,
)
from ena.rules.forecast import euros

_SortKey = tuple[bool, int, dt.date, dt.date, str]
"""Ineligible last, then the total, the delivery date, the reply date and the id."""


def same_name(left: str, right: str) -> bool:
    """Equal after case-folding and collapsing whitespace; an empty name never matches."""
    a, b = " ".join(left.split()).casefold(), " ".join(right.split()).casefold()
    return bool(a) and a == b


def _complete(request: QuoteRequest, quote: Quotation) -> tuple[bool, list[str]]:
    issues = []
    for item in request.items:
        line = next((ln for ln in quote.lines if same_name(ln.description, item.description)), None)
        if line is None:
            issues.append(f"no price for {item.description}")
        elif not same_name(line.unit, item.unit):
            issues.append(f"{item.description} quoted per {line.unit}, asked per {item.unit}")
        elif line.quantity < item.quantity:
            issues.append(
                f"{item.description}: {line.quantity} {line.unit} quoted,"
                f" {item.quantity} {item.unit} asked"
            )
    return not issues, issues


def compare_quotes(
    request: QuoteRequest, quotes: Sequence[Quotation], today: dt.date
) -> QuoteComparison:
    """Rank a request's quotations and recommend the cheapest eligible one."""
    ranked: list[tuple[_SortKey, Quotation, bool, bool, bool, list[str]]] = []
    seen: set[str] = set()
    for quote in quotes:
        if quote.request_id != request.id:
            raise ValueError(f"quotation {quote.id} answers {quote.request_id}, not {request.id}")
        if quote.id in seen:
            raise ValueError(f"quotation {quote.id} is given twice")
        seen.add(quote.id)
        complete, issues = _complete(request, quote)
        on_time = quote.delivery_date <= request.needed_by
        valid = quote.valid_until >= today
        if not on_time:
            issues.append(f"delivers {quote.delivery_date}, needed by {request.needed_by}")
        if not valid:
            issues.append(f"expired on {quote.valid_until}")
        eligible = complete and on_time and valid
        key = (
            not eligible,
            quote.total_cents,
            quote.delivery_date,
            quote.received_on,
            quote.id,
        )
        ranked.append((key, quote, complete, on_time, valid, issues))
    ranked.sort(key=lambda entry: entry[0])
    results = tuple(
        RankedQuote(
            quotation=quote,
            rank=n,
            complete=complete,
            on_time=on_time,
            valid=valid,
            issues=tuple(issues),
        )
        for n, (_, quote, complete, on_time, valid, issues) in enumerate(ranked, start=1)
    )
    eligibles = [r for r in results if r.complete and r.on_time and r.valid]
    return QuoteComparison(
        request=request,
        quotes=results,
        recommended=eligibles[0].quotation.id if eligibles else None,
        awarded=None,
        order_id=None,
        reason=_reason(request, results, eligibles),
    )


def _reason(
    request: QuoteRequest, results: Sequence[RankedQuote], eligible: Sequence[RankedQuote]
) -> str:
    if not results:
        return f"No quotations yet; replies are asked by {request.reply_by}"
    if not eligible:
        return (
            f"None of the {len(results)} quotations covers every item by {request.needed_by}"
            " and still holds"
        )
    best = eligible[0].quotation
    reason = (
        f"{best.supplier_name} is the cheapest of {len(eligible)} complete, on-time quotations"
        f" at {euros(best.total_cents)}, delivering {best.delivery_date}"
    )
    if len(eligible) > 1:
        runner_up = eligible[1].quotation
        saving = runner_up.total_cents - best.total_cents
        reason += f", {euros(saving)} less than {runner_up.supplier_name}"
    return reason


class InvoiceMatch(BaseModel):
    """Whether an invoice bills its purchase order, and why not."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    order_id: str
    matched: bool
    issues: tuple[str, ...]


def match_invoice(invoice: SupplierInvoice, order: PurchaseOrder) -> InvoiceMatch:
    """Check an invoice against the purchase order it names."""
    if invoice.purchase_order_id != order.id:
        raise ValueError(f"invoice {invoice.id} names {invoice.purchase_order_id}, not {order.id}")
    issues = []
    if not same_name(invoice.supplier_name, order.supplier_name):
        issues.append(f"from {invoice.supplier_name}, but the order is to {order.supplier_name}")
    if invoice.amount_cents != order.total_cents:
        issues.append(
            f"bills {euros(invoice.amount_cents)}, but the order is for {euros(order.total_cents)}"
        )
    return InvoiceMatch(order_id=order.id, matched=not issues, issues=tuple(issues))
