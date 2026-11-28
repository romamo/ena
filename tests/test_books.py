import datetime as dt

import pytest

from ena.models import CardSale, InvoiceStatus, SupplierInvoice, Transaction
from ena.rules.books import (
    Books,
    InvoiceMatch,
    MatchBasis,
    SaleMatch,
    match_books,
    match_card_sales,
    match_invoices,
)

IBAN = "CY17002001280000001200527600"
DUE = dt.date(2026, 11, 20)


def invoice(
    id_: str,
    amount_cents: int = 12_000,
    *,
    due: dt.date = DUE,
    supplier: str = "Flour Mills Ltd",
    jinius_id: str | None = None,
) -> SupplierInvoice:
    return SupplierInvoice(
        id=id_,
        supplier_name=supplier,
        supplier_iban=IBAN,
        amount_cents=amount_cents,
        issue_date=due - dt.timedelta(days=30),
        due_date=due,
        status=InvoiceStatus.NEW,
        jinius_id=jinius_id or f"J-{id_}",
    )


def tx(
    id_: str,
    amount_cents: int,
    date: dt.date,
    *,
    counterparty: str = "",
    reference: str = "",
) -> Transaction:
    return Transaction(
        id=id_,
        account_id="351012345671",
        date=date,
        amount_cents=amount_cents,
        counterparty=counterparty,
        reference=reference,
    )


def sale(id_: str, amount_cents: int, date: dt.date, order_id: str) -> CardSale:
    return CardSale(
        id=id_, amount_cents=amount_cents, date=date, jcc_order_id=order_id, status="deposited"
    )


def days(n: int) -> dt.date:
    return DUE + dt.timedelta(days=n)


# Invoices


def test_reference_match_on_amount_and_due_date() -> None:
    inv = invoice("inv-1", jinius_id="INV-2026-017")
    pay = tx("t1", -12_000, DUE, counterparty="Someone Else", reference="Payment inv-2026-017")
    result = match_invoices([inv], [pay])
    assert result.matches == (InvoiceMatch(invoice=inv, payment=pay, basis=MatchBasis.REFERENCE),)
    assert result.unmatched_invoices == ()
    assert result.unmatched_payments == ()


def test_counterparty_match_is_case_and_whitespace_insensitive() -> None:
    inv = invoice("inv-1", supplier="Flour Mills Ltd")
    pay = tx("t1", -12_000, DUE, counterparty="  FLOUR   mills ltd ")
    result = match_invoices([inv], [pay])
    assert result.matches == (
        InvoiceMatch(invoice=inv, payment=pay, basis=MatchBasis.COUNTERPARTY),
    )


def test_neither_reference_nor_counterparty_does_not_match() -> None:
    inv = invoice("inv-1", supplier="Flour Mills Ltd")
    pay = tx("t1", -12_000, DUE, counterparty="Flour Mills", reference="November")
    result = match_invoices([inv], [pay])
    assert result.matches == ()
    assert result.unmatched_invoices == (inv,)
    assert result.unmatched_payments == (pay,)


def test_invoice_number_must_be_a_whole_token() -> None:
    inv = invoice("inv-1", jinius_id="INV-1", supplier="Flour Mills Ltd")
    pay = tx("t1", -12_000, DUE, counterparty="Other", reference="INV-10")
    assert match_invoices([inv], [pay]).matches == ()


def test_different_amount_does_not_match() -> None:
    inv = invoice("inv-1")
    pay = tx("t1", -12_001, DUE, counterparty="Flour Mills Ltd", reference="J-inv-1")
    assert match_invoices([inv], [pay]).matches == ()


@pytest.mark.parametrize(
    ("offset", "matched"), [(-8, False), (-7, True), (0, True), (7, True), (8, False)]
)
def test_payment_within_7_days_of_due_date(offset: int, matched: bool) -> None:
    inv = invoice("inv-1")
    pay = tx("t1", -12_000, days(offset), counterparty="Flour Mills Ltd")
    assert bool(match_invoices([inv], [pay]).matches) is matched


@pytest.mark.parametrize(
    ("offset", "matched"), [(-31, False), (-30, True), (-8, True), (7, True), (8, False)]
)
def test_a_payment_with_the_invoice_number_may_come_from_the_issue_date(
    offset: int, matched: bool
) -> None:
    inv = invoice("inv-1", jinius_id="INV-1")  # issued 30 days before it is due
    pay = tx("t1", -12_000, days(offset), counterparty="Other", reference="Payment INV-1")
    result = match_invoices([inv], [pay])
    assert bool(result.matches) is matched
    if matched:
        assert result.matches[0].basis is MatchBasis.REFERENCE


def test_day_7_payment_matches_and_day_8_payment_stays_unmatched() -> None:
    inv = invoice("inv-1")
    day_8 = tx("t8", -12_000, days(8), counterparty="Flour Mills Ltd")
    day_7 = tx("t7", -12_000, days(7), counterparty="Flour Mills Ltd")
    result = match_invoices([inv], [day_8, day_7])
    assert result.matches == (
        InvoiceMatch(invoice=inv, payment=day_7, basis=MatchBasis.COUNTERPARTY),
    )
    assert result.unmatched_payments == (day_8,)


def test_two_invoices_of_the_same_amount_take_one_payment_each_by_date() -> None:
    late = invoice("inv-b", due=days(3))
    early = invoice("inv-a")
    second = tx("t2", -12_000, days(4), counterparty="Flour Mills Ltd")
    first = tx("t1", -12_000, days(1), counterparty="Flour Mills Ltd")
    result = match_invoices([late, early], [second, first])
    assert result.matches == (
        InvoiceMatch(invoice=early, payment=first, basis=MatchBasis.COUNTERPARTY),
        InvoiceMatch(invoice=late, payment=second, basis=MatchBasis.COUNTERPARTY),
    )
    assert result.unmatched_invoices == ()
    assert result.unmatched_payments == ()


def test_two_invoices_of_the_same_amount_and_one_payment() -> None:
    a = invoice("inv-a")
    b = invoice("inv-b")
    pay = tx("t1", -12_000, DUE, counterparty="Flour Mills Ltd")
    result = match_invoices([b, a], [pay])
    assert result.matches == (InvoiceMatch(invoice=a, payment=pay, basis=MatchBasis.COUNTERPARTY),)
    assert result.unmatched_invoices == (b,)


def test_reference_match_wins_over_an_earlier_counterparty_match() -> None:
    a = invoice("inv-a", jinius_id="INV-A")
    b = invoice("inv-b", jinius_id="INV-B")
    pay_b = tx("t1", -12_000, DUE, counterparty="Flour Mills Ltd", reference="INV-B")
    result = match_invoices([a, b], [pay_b])
    assert result.matches == (InvoiceMatch(invoice=b, payment=pay_b, basis=MatchBasis.REFERENCE),)
    assert result.unmatched_invoices == (a,)


def test_reference_payment_wins_over_an_earlier_counterparty_payment() -> None:
    inv = invoice("inv-a", jinius_id="INV-A")
    by_name = tx("t1", -12_000, days(-2), counterparty="Flour Mills Ltd")
    by_ref = tx("t2", -12_000, days(1), counterparty="Other", reference="INV-A")
    result = match_invoices([inv], [by_name, by_ref])
    assert result.matches == (
        InvoiceMatch(invoice=inv, payment=by_ref, basis=MatchBasis.REFERENCE),
    )
    assert result.unmatched_payments == (by_name,)


def test_positive_transactions_never_match_invoices() -> None:
    inv = invoice("inv-1", jinius_id="INV-1")
    refund = tx("t1", 12_000, DUE, counterparty="Flour Mills Ltd", reference="INV-1")
    result = match_invoices([inv], [refund])
    assert result.matches == ()
    assert result.unmatched_invoices == (inv,)
    assert result.unmatched_payments == ()


def test_empty_names_never_match() -> None:
    inv = invoice("inv-1", supplier=" ")
    pay = tx("t1", -12_000, DUE, counterparty="")
    assert match_invoices([inv], [pay]).matches == ()


# Card sales


def test_sale_matches_deposit_by_order_id_whatever_the_amount_and_date() -> None:
    s = sale("s1", 4_500, DUE, "ord-7f3a")
    dep = tx("d1", 4_410, days(10), reference="JCC SETTLEMENT ORD-7F3A")
    result = match_card_sales([s], [dep])
    assert result.matches == (SaleMatch(sale=s, deposit=dep, basis=MatchBasis.ORDER_ID),)
    assert result.unmatched_sales == ()
    assert result.unmatched_deposits == ()


def test_sale_matches_deposit_by_amount_and_date() -> None:
    s = sale("s1", 4_500, DUE, "ord-1")
    dep = tx("d1", 4_500, days(2), reference="JCC")
    result = match_card_sales([s], [dep])
    assert result.matches == (SaleMatch(sale=s, deposit=dep, basis=MatchBasis.AMOUNT_DATE),)


def test_order_id_match_wins_over_an_earlier_amount_and_date_match() -> None:
    s = sale("s1", 4_500, DUE, "ord-1")
    by_amount = tx("d1", 4_500, DUE, reference="JCC")
    by_order = tx("d2", 4_500, days(3), reference="ord-1")
    result = match_card_sales([s], [by_amount, by_order])
    assert result.matches == (SaleMatch(sale=s, deposit=by_order, basis=MatchBasis.ORDER_ID),)
    assert result.unmatched_deposits == (by_amount,)


@pytest.mark.parametrize(("offset", "matched"), [(-1, False), (0, True), (3, True), (4, False)])
def test_deposit_within_3_days_after_the_sale(offset: int, matched: bool) -> None:
    s = sale("s1", 4_500, DUE, "ord-1")
    dep = tx("d1", 4_500, days(offset), reference="JCC")
    assert bool(match_card_sales([s], [dep]).matches) is matched


def test_day_3_deposit_matches_and_day_4_deposit_stays_unmatched() -> None:
    s = sale("s1", 4_500, DUE, "ord-1")
    day_4 = tx("d4", 4_500, days(4))
    day_3 = tx("d3", 4_500, days(3))
    result = match_card_sales([s], [day_4, day_3])
    assert result.matches == (SaleMatch(sale=s, deposit=day_3, basis=MatchBasis.AMOUNT_DATE),)
    assert result.unmatched_deposits == (day_4,)


def test_negative_transactions_never_match_sales() -> None:
    s = sale("s1", 4_500, DUE, "ord-1")
    chargeback = tx("d1", -4_500, DUE, reference="ord-1")
    result = match_card_sales([s], [chargeback])
    assert result.matches == ()
    assert result.unmatched_sales == (s,)
    assert result.unmatched_deposits == ()


def test_two_sales_of_the_same_amount_take_one_deposit_each() -> None:
    s2 = sale("s2", 4_500, days(1), "ord-2")
    s1 = sale("s1", 4_500, DUE, "ord-1")
    d2 = tx("d2", 4_500, days(2))
    d1 = tx("d1", 4_500, days(1))
    result = match_card_sales([s2, s1], [d2, d1])
    assert result.matches == (
        SaleMatch(sale=s1, deposit=d1, basis=MatchBasis.AMOUNT_DATE),
        SaleMatch(sale=s2, deposit=d2, basis=MatchBasis.AMOUNT_DATE),
    )


# The books


def test_match_books_splits_matched_and_unmatched() -> None:
    inv_paid = invoice("inv-1", jinius_id="INV-1")
    inv_open = invoice("inv-2", 30_000, supplier="Dairy Co")
    s_paid = sale("s1", 4_500, DUE, "ord-1")
    s_open = sale("s2", 9_900, DUE, "ord-2")
    pay = tx("t1", -12_000, DUE, reference="INV-1")
    rent = tx("t2", -80_000, DUE, counterparty="Landlord")
    dep = tx("d1", 4_500, days(1), reference="ord-1")
    transfer = tx("d2", 25_000, days(1), counterparty="Customer")
    books = match_books([inv_open, inv_paid], [s_open, s_paid], [transfer, rent, dep, pay])
    assert books == Books(
        invoice_matches=(InvoiceMatch(invoice=inv_paid, payment=pay, basis=MatchBasis.REFERENCE),),
        sale_matches=(SaleMatch(sale=s_paid, deposit=dep, basis=MatchBasis.ORDER_ID),),
        unmatched_invoices=(inv_open,),
        unmatched_sales=(s_open,),
        unmatched_payments=(rent,),
        unmatched_deposits=(transfer,),
    )


def test_match_books_is_independent_of_input_order() -> None:
    invoices = [invoice("inv-a"), invoice("inv-b"), invoice("inv-c", due=days(2))]
    sales = [sale("s1", 4_500, DUE, "ord-1"), sale("s2", 4_500, DUE, "ord-2")]
    transactions = [
        tx("t1", -12_000, DUE, counterparty="Flour Mills Ltd"),
        tx("t2", -12_000, DUE, counterparty="Flour Mills Ltd"),
        tx("d1", 4_500, days(1)),
        tx("d2", 4_500, days(1)),
    ]
    expected = match_books(invoices, sales, transactions)
    assert match_books(invoices[::-1], sales[::-1], transactions[::-1]) == expected
    assert [m.payment.id for m in expected.invoice_matches] == ["t1", "t2"]
    assert [m.invoice.id for m in expected.invoice_matches] == ["inv-a", "inv-b"]
    assert expected.unmatched_invoices == (invoices[2],)


# Malformed input


def test_zero_amount_transaction_is_refused() -> None:
    with pytest.raises(ValueError, match="zero amount"):
        match_books([], [], [tx("t1", 0, DUE)])


def test_duplicate_transaction_ids_are_refused() -> None:
    with pytest.raises(ValueError, match="transactions share the id 't1'"):
        match_invoices([], [tx("t1", -1, DUE), tx("t1", -2, DUE)])


def test_duplicate_invoice_ids_are_refused() -> None:
    with pytest.raises(ValueError, match="invoices share the id 'inv-1'"):
        match_invoices([invoice("inv-1"), invoice("inv-1")], [])


def test_duplicate_sale_ids_are_refused() -> None:
    with pytest.raises(ValueError, match="card sales share the id 's1'"):
        match_card_sales([sale("s1", 1, DUE, "a"), sale("s1", 2, DUE, "b")], [])
