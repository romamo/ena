import datetime as dt

import pytest
from pydantic import BaseModel, ValidationError

from ena.models import (
    Account,
    Actor,
    ApprovalRequest,
    ApprovalStatus,
    Business,
    CardSale,
    ChatMessage,
    Event,
    EventStatus,
    Forecast,
    ForecastWeek,
    InvoiceStatus,
    Loan,
    Offer,
    OfferKind,
    Receipt,
    Role,
    SupplierInvoice,
    Transaction,
)

AT = dt.datetime(2026, 11, 28, 9, 30, tzinfo=dt.UTC)
IBAN = "CY17002001280000001200527600"

BUSINESS = Business(
    id="biz-1",
    name="Andreou Bakery",
    sector="bakery",
    account_id="351012345671",
    auto_pay_limit_cents=50_000,
    synthetic=True,
)
ACCOUNT = Account(id="351012345671", iban=IBAN, balance_cents=-1_250, currency="EUR")
TRANSACTION = Transaction(
    id="tx-1",
    account_id="351012345671",
    date=dt.date(2026, 11, 2),
    amount_cents=-124_000,
    counterparty="Flour Mills Ltd",
    reference="INV-17",
)
INVOICE = SupplierInvoice(
    id="inv-17",
    supplier_name="Flour Mills Ltd",
    supplier_iban=IBAN,
    amount_cents=124_000,
    issue_date=dt.date(2026, 11, 1),
    due_date=dt.date(2026, 11, 15),
    status=InvoiceStatus.AWAITING_APPROVAL,
    jinius_id="J-0017",
)
CARD_SALE = CardSale(
    id="sale-1",
    amount_cents=1_850,
    date=dt.date(2026, 11, 3),
    jcc_order_id="ORD-991",
    status="settled",
)
LOAN = Loan(id="loan-1", instalment_cents=85_000, next_due_date=dt.date(2026, 12, 1))
FORECAST = Forecast(
    weeks=(
        ForecastWeek(
            week_start=dt.date(2026, 11, 30),
            opening_cents=200_000,
            in_cents=90_000,
            out_cents=250_000,
            closing_cents=40_000,
        ),
    ),
    lean_week=dt.date(2026, 11, 30),
    reason="Closing balance below the next instalment plus a week of outflows",
)
EVENT = Event(
    id="ev-1",
    customer="Georgiou wedding",
    event_date=dt.date(2026, 12, 19),
    price_cents=800_000,
    deposit_cents=200_000,
    costs_cents=480_000,
    final_payment_cents=600_000,
    status=EventStatus.BOOKED,
)
OFFER = Offer(
    id="off-1",
    kind=OfferKind.EVENT_ADVANCE,
    amount_cents=180_000,
    term_days=16,
    basis={"gap_cents": 180_000, "completed_events": 12},
    simulated=True,
)
APPROVAL = ApprovalRequest(
    id="apr-1",
    action="pay_invoice",
    amount_cents=124_000,
    reason="Above the €500 limit",
    status=ApprovalStatus.APPROVED,
    created_at=AT,
    decided_at=AT + dt.timedelta(minutes=5),
)
RECEIPT = Receipt(
    id="rcp-1",
    at=AT,
    actor=Actor.OWNER,
    action="pay_invoice",
    amount_cents=124_000,
    ref="inv-17",
    result="paid",
    explanation="Paid the flour supplier €1,240 after your approval.",
)
CHAT = ChatMessage(
    id="msg-1",
    at=AT,
    role=Role.ENA,
    text="Paid the flour supplier.",
    cards=(RECEIPT, FORECAST, OFFER, APPROVAL),
)

ENTITIES: list[BaseModel] = [
    BUSINESS,
    ACCOUNT,
    TRANSACTION,
    INVOICE,
    CARD_SALE,
    LOAN,
    FORECAST,
    EVENT,
    OFFER,
    APPROVAL,
    RECEIPT,
    CHAT,
]


@pytest.mark.parametrize("entity", ENTITIES, ids=lambda entity: type(entity).__name__)
def test_round_trips_through_json(entity: BaseModel) -> None:
    assert type(entity).model_validate_json(entity.model_dump_json()) == entity


def test_dates_are_iso_in_json() -> None:
    data = INVOICE.model_dump(mode="json")
    assert data["issue_date"] == "2026-11-01"
    assert data["status"] == "awaiting_approval"


def _invoice_json(**changes: object) -> dict[str, object]:
    data: dict[str, object] = INVOICE.model_dump(mode="json")
    data.update(changes)
    return data


@pytest.mark.parametrize(
    "changes",
    [
        {"amount_cents": 1240.0},
        {"amount_cents": "124000"},
        {"amount_cents": True},
        {"amount_cents": -1},
        {"status": "lost"},
        {"due_date": "15/11/2026"},
        {"due_date": "2026-13-01"},
        {"due_date": 0},
        {"due_date": "2026-11-15T00:00:00"},
        {"supplier_iban": "not an iban"},
        {"id": ""},
        {"note": "an extra field"},
    ],
    ids=lambda changes: next(iter(changes)) + "=" + repr(next(iter(changes.values()))),
)
def test_invoice_refuses_malformed_input(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SupplierInvoice.model_validate(_invoice_json(**changes))


def test_transaction_amount_is_signed() -> None:
    money_in = TRANSACTION.model_copy(update={"amount_cents": 5_000})
    assert Transaction.model_validate_json(money_in.model_dump_json()).amount_cents == 5_000


def test_timestamps_must_be_aware() -> None:
    data = RECEIPT.model_dump(mode="json")
    data["at"] = "2026-11-28T09:30:00"
    with pytest.raises(ValidationError):
        Receipt.model_validate(data)


def test_models_are_frozen() -> None:
    with pytest.raises(ValidationError):
        LOAN.instalment_cents = 0  # type: ignore[misc]
    with pytest.raises(ValidationError):
        RECEIPT.result = "failed"  # type: ignore[misc]


def test_offer_basis_is_immutable() -> None:
    with pytest.raises(TypeError):
        OFFER.basis["gap_cents"] = 1  # type: ignore[index]
    assert hash(OFFER) == hash(OFFER.model_copy())


def test_flags_are_strict_booleans() -> None:
    data = OFFER.model_dump(mode="json")
    data["simulated"] = "yes"
    with pytest.raises(ValidationError):
        Offer.model_validate(data)


def test_forecast_week_closing_must_add_up() -> None:
    week = FORECAST.weeks[0].model_dump(mode="json")
    week["closing_cents"] = 40_001
    with pytest.raises(ValidationError):
        ForecastWeek.model_validate(week)


def test_forecast_lean_week_must_be_a_week() -> None:
    data = FORECAST.model_dump(mode="json")
    data["lean_week"] = "2026-12-07"
    with pytest.raises(ValidationError):
        Forecast.model_validate(data)


def test_approval_decided_at_matches_status() -> None:
    pending = APPROVAL.model_dump(mode="json")
    pending["status"] = "pending"
    with pytest.raises(ValidationError):
        ApprovalRequest.model_validate(pending)
    pending["decided_at"] = None
    assert ApprovalRequest.model_validate(pending).status is ApprovalStatus.PENDING
