"""Books matching (docs/build-plan.md §7): the statement against invoices and card sales.

Pure functions, no I/O. A transaction with a negative amount is a payment, one with a positive
amount is a deposit.

A payment matches a supplier invoice when all of these hold:

- its absolute amount equals the invoice's `amount_cents`
- its reference contains the invoice number and it is dated from the invoice's
  `issue_date` to 7 days after its `due_date`, or its counterparty matches the supplier
  and it is dated at most 7 days before or after the `due_date`

An invoice paid on arrival (the gate pays within the limit the day it comes, and the
owner approves the rest) goes out before the 7 days ahead of its due date; its invoice
number in the reference is what ties it to the invoice, so the window opens on the
issue date for it. A payment by counterparty alone keeps the 7 days either side.

A deposit matches a card sale when its reference contains the sale's `jcc_order_id`, or when
its amount equals the sale's and it is dated 0 to 3 days after the sale (settlement delay).

Interpretations:

- The invoice number is `SupplierInvoice.jinius_id`, the only invoice number the model holds
- An invoice number or an order id is "in" a reference when it appears there as a whole token:
  case-insensitively, and not inside a longer run of ASCII letters and digits (`INV-1` is not
  in `INV-10`)
- The counterparty matches the supplier when both are equal after case-folding and collapsing
  runs of whitespace; an empty name never matches
- A deposit is never dated before its sale
- Statuses are not filtered: the caller passes the invoices and sales it wants matched

Matching is one-to-one and deterministic. The strong pass (reference, order id) runs before
the weak pass (counterparty, amount and date), so a strong match is never lost to a weak one.
Within a pass, invoices (by due date, then id) and sales (by date, then id) each take the
first free transaction (by date, then id) that matches.

A zero-amount transaction, or two items with the same id within one input, raise `ValueError`.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable, Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from ena.models import CardSale, SupplierInvoice, Transaction

INVOICE_WINDOW = dt.timedelta(days=7)
"""How far a payment may be from the invoice's due date, inclusive: either side by
counterparty, after it by reference."""

SETTLEMENT_WINDOW = dt.timedelta(days=3)
"""How long after a card sale its deposit may arrive, inclusive."""


class MatchBasis(StrEnum):
    REFERENCE = "reference"
    """The payment's reference holds the invoice number."""
    COUNTERPARTY = "counterparty"
    """The payment's counterparty is the supplier."""
    ORDER_ID = "order_id"
    """The deposit's reference holds the JCC order id."""
    AMOUNT_DATE = "amount_date"
    """The deposit has the sale's amount and arrived within the settlement window."""


class _Result(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class InvoiceMatch(_Result):
    invoice: SupplierInvoice
    payment: Transaction
    basis: MatchBasis


class SaleMatch(_Result):
    sale: CardSale
    deposit: Transaction
    basis: MatchBasis


class InvoiceMatching(_Result):
    matches: tuple[InvoiceMatch, ...]
    """By invoice due date, then id."""
    unmatched_invoices: tuple[SupplierInvoice, ...]
    """By due date, then id."""
    unmatched_payments: tuple[Transaction, ...]
    """By date, then id."""


class SaleMatching(_Result):
    matches: tuple[SaleMatch, ...]
    """By sale date, then id."""
    unmatched_sales: tuple[CardSale, ...]
    """By date, then id."""
    unmatched_deposits: tuple[Transaction, ...]
    """By date, then id."""


class Books(_Result):
    invoice_matches: tuple[InvoiceMatch, ...]
    sale_matches: tuple[SaleMatch, ...]
    unmatched_invoices: tuple[SupplierInvoice, ...]
    unmatched_sales: tuple[CardSale, ...]
    unmatched_payments: tuple[Transaction, ...]
    unmatched_deposits: tuple[Transaction, ...]


def match_books(
    invoices: Sequence[SupplierInvoice],
    sales: Sequence[CardSale],
    transactions: Sequence[Transaction],
) -> Books:
    """Match payments to invoices and deposits to card sales; return the rest as unmatched."""
    by_invoice = match_invoices(invoices, transactions)
    by_sale = match_card_sales(sales, transactions)
    return Books(
        invoice_matches=by_invoice.matches,
        sale_matches=by_sale.matches,
        unmatched_invoices=by_invoice.unmatched_invoices,
        unmatched_sales=by_sale.unmatched_sales,
        unmatched_payments=by_invoice.unmatched_payments,
        unmatched_deposits=by_sale.unmatched_deposits,
    )


def match_invoices(
    invoices: Sequence[SupplierInvoice], transactions: Sequence[Transaction]
) -> InvoiceMatching:
    """Match the payments among `transactions` to `invoices`, one to one."""
    _check_unique_ids("invoice", [invoice.id for invoice in invoices])
    payments = [tx for tx in _sorted_transactions(transactions) if tx.amount_cents < 0]
    ordered = sorted(invoices, key=lambda invoice: (invoice.due_date, invoice.id))

    def reference(invoice: SupplierInvoice, tx: Transaction) -> bool:
        on_time = invoice.issue_date <= tx.date <= invoice.due_date + INVOICE_WINDOW
        return _pays_amount(invoice, tx) and on_time and _has_token(tx.reference, invoice.jinius_id)

    def counterparty(invoice: SupplierInvoice, tx: Transaction) -> bool:
        near_due = abs(tx.date - invoice.due_date) <= INVOICE_WINDOW
        return (
            _pays_amount(invoice, tx)
            and near_due
            and _same_name(tx.counterparty, invoice.supplier_name)
        )

    pairs = _match_passes(
        ordered,
        payments,
        [(MatchBasis.REFERENCE, reference), (MatchBasis.COUNTERPARTY, counterparty)],
    )
    used = {tx.id for tx, _ in pairs.values()}
    return InvoiceMatching(
        matches=tuple(
            InvoiceMatch(invoice=invoice, payment=pairs[invoice.id][0], basis=pairs[invoice.id][1])
            for invoice in ordered
            if invoice.id in pairs
        ),
        unmatched_invoices=tuple(invoice for invoice in ordered if invoice.id not in pairs),
        unmatched_payments=tuple(tx for tx in payments if tx.id not in used),
    )


def match_card_sales(
    sales: Sequence[CardSale], transactions: Sequence[Transaction]
) -> SaleMatching:
    """Match the deposits among `transactions` to card `sales`, one to one."""
    _check_unique_ids("card sale", [sale.id for sale in sales])
    deposits = [tx for tx in _sorted_transactions(transactions) if tx.amount_cents > 0]
    ordered = sorted(sales, key=lambda sale: (sale.date, sale.id))

    def order_id(sale: CardSale, tx: Transaction) -> bool:
        return _has_token(tx.reference, sale.jcc_order_id)

    def amount_date(sale: CardSale, tx: Transaction) -> bool:
        delay = tx.date - sale.date
        return tx.amount_cents == sale.amount_cents and dt.timedelta(0) <= delay <= (
            SETTLEMENT_WINDOW
        )

    pairs = _match_passes(
        ordered,
        deposits,
        [(MatchBasis.ORDER_ID, order_id), (MatchBasis.AMOUNT_DATE, amount_date)],
    )
    used = {tx.id for tx, _ in pairs.values()}
    return SaleMatching(
        matches=tuple(
            SaleMatch(sale=sale, deposit=pairs[sale.id][0], basis=pairs[sale.id][1])
            for sale in ordered
            if sale.id in pairs
        ),
        unmatched_sales=tuple(sale for sale in ordered if sale.id not in pairs),
        unmatched_deposits=tuple(tx for tx in deposits if tx.id not in used),
    )


def _match_passes[T: SupplierInvoice | CardSale](
    items: Sequence[T],
    transactions: Sequence[Transaction],
    passes: Sequence[tuple[MatchBasis, Callable[[T, Transaction], bool]]],
) -> dict[str, tuple[Transaction, MatchBasis]]:
    """Run each pass in turn; each still unmatched item takes the first free transaction."""
    pairs: dict[str, tuple[Transaction, MatchBasis]] = {}
    used: set[str] = set()
    for basis, fits in passes:
        for item in items:
            if item.id in pairs:
                continue
            for tx in transactions:
                if tx.id not in used and fits(item, tx):
                    pairs[item.id] = (tx, basis)
                    used.add(tx.id)
                    break
    return pairs


def _pays_amount(invoice: SupplierInvoice, tx: Transaction) -> bool:
    """Whether a payment's amount is the invoice's."""
    return -tx.amount_cents == invoice.amount_cents


def _sorted_transactions(transactions: Sequence[Transaction]) -> list[Transaction]:
    _check_unique_ids("transaction", [tx.id for tx in transactions])
    for tx in transactions:
        if tx.amount_cents == 0:
            raise ValueError(f"transaction {tx.id!r} has a zero amount: neither in nor out")
    return sorted(transactions, key=lambda tx: (tx.date, tx.id))


def _check_unique_ids(kind: str, ids: Sequence[str]) -> None:
    seen: set[str] = set()
    for id_ in ids:
        if id_ in seen:
            raise ValueError(f"two {kind}s share the id {id_!r}")
        seen.add(id_)


def _has_token(text: str, token: str) -> bool:
    """Whether `token` is in `text`, case-insensitively, not inside a longer alphanumeric run."""
    pattern = rf"(?<![0-9A-Za-z]){re.escape(token)}(?![0-9A-Za-z])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _same_name(left: str, right: str) -> bool:
    """Whether two names are equal after case-folding and collapsing whitespace."""
    a = " ".join(left.split()).casefold()
    b = " ".join(right.split()).casefold()
    return a != "" and a == b
