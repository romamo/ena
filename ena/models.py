"""Ena's data model (docs/build-plan.md §4).

Amounts are integer euro cents (`..._cents`), refused when given as a float or a string.
Dates are `datetime.date` (ISO `YYYY-MM-DD` in JSON); timestamps are timezone-aware.
Connectors convert their APIs' formats (bocAPI's `dd/mm/yyyy`) into these models.
Every model is frozen and refuses unknown fields.
"""

from __future__ import annotations

import datetime as dt
import re
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

# Field types


def _iso_date(value: object) -> object:
    """Let only a `date` or a `YYYY-MM-DD` string through: no timestamps, no datetimes."""
    if isinstance(value, dt.datetime) or not isinstance(value, (dt.date, str)):
        raise ValueError(f"a date must be a date or a YYYY-MM-DD string, got {value!r}")
    if isinstance(value, str) and not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError(f"not a YYYY-MM-DD date: {value!r}")
    return value


IsoDate = Annotated[dt.date, BeforeValidator(_iso_date)]
"""A calendar date, ISO `YYYY-MM-DD` in JSON."""

Id = Annotated[str, StringConstraints(min_length=1)]
"""An entity's id: any non-empty string."""

Cents = Annotated[int, Field(strict=True)]
"""A signed amount in euro cents."""

NonNegativeCents = Annotated[int, Field(strict=True, ge=0)]
"""An amount in euro cents that is never negative."""

Iban = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$")]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
Flag = Annotated[bool, Field(strict=True)]


class _Entity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# Statuses and kinds


class InvoiceStatus(StrEnum):
    NEW = "new"
    SCHEDULED = "scheduled"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_BANK = "awaiting_bank"
    """Approved or within the limit, waiting for the owner's confirmation in the bank."""
    PAID = "paid"
    DECLINED = "declined"


class EventStatus(StrEnum):
    BOOKED = "booked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class OfferKind(StrEnum):
    RESERVE = "reserve"
    LOAN = "loan"
    EVENT_ADVANCE = "event_advance"


class AdvanceStatus(StrEnum):
    """An event advance offer, as the owner left it."""

    OFFERED = "offered"
    ACCEPTED = "accepted"
    DECLINED = "declined"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DECLINED = "declined"


class Actor(StrEnum):
    ENA = "ena"
    OWNER = "owner"


class Role(StrEnum):
    OWNER = "owner"
    ENA = "ena"


# Entities


class Business(_Entity):
    id: Id
    name: str
    sector: str
    account_id: Id
    auto_pay_limit_cents: NonNegativeCents
    synthetic: Flag


class Account(_Entity):
    id: Id
    iban: Iban
    balance_cents: Cents
    currency: Currency


class Transaction(_Entity):
    id: Id
    account_id: Id
    date: IsoDate
    amount_cents: Cents
    """Positive for money in, negative for money out."""
    counterparty: str
    reference: str


class SupplierInvoice(_Entity):
    id: Id
    supplier_name: str
    supplier_iban: Iban
    amount_cents: NonNegativeCents
    issue_date: IsoDate
    due_date: IsoDate
    status: InvoiceStatus
    jinius_id: Id


class CardSale(_Entity):
    id: Id
    amount_cents: NonNegativeCents
    date: IsoDate
    jcc_order_id: Id
    status: str
    """JCC's status, as its callback sends it (values unknown until the JCC task)."""


class Loan(_Entity):
    """The existing loan: read-only, the loan never changes (§3 rule 4)."""

    id: Id
    instalment_cents: NonNegativeCents
    next_due_date: IsoDate


class ForecastWeek(_Entity):
    week_start: IsoDate
    opening_cents: Cents
    in_cents: NonNegativeCents
    out_cents: NonNegativeCents
    closing_cents: Cents

    @model_validator(mode="after")
    def _closing_adds_up(self) -> Self:
        expected = self.opening_cents + self.in_cents - self.out_cents
        if self.closing_cents != expected:
            raise ValueError(
                f"closing_cents {self.closing_cents} != opening + in - out = {expected}"
            )
        return self


class Forecast(_Entity):
    weeks: tuple[ForecastWeek, ...]
    lean_week: IsoDate | None
    """The `week_start` of the first lean week, or None when no week is lean."""
    reason: str

    @model_validator(mode="after")
    def _lean_week_is_a_week(self) -> Self:
        starts = {week.week_start for week in self.weeks}
        if self.lean_week is not None and self.lean_week not in starts:
            raise ValueError(f"lean_week {self.lean_week} is not one of the weeks")
        return self


class Event(_Entity):
    id: Id
    customer: str
    event_date: IsoDate
    price_cents: NonNegativeCents
    deposit_cents: NonNegativeCents
    costs_cents: NonNegativeCents
    final_payment_cents: NonNegativeCents
    status: EventStatus
    costs_paid_date: IsoDate | None = None
    """When the event's costs were paid; set on a completed event."""
    final_paid_date: IsoDate | None = None
    """When the final payment arrived; set on a completed event, and only there."""

    @model_validator(mode="after")
    def _payments_add_up(self) -> Self:
        if self.deposit_cents + self.final_payment_cents != self.price_cents:
            raise ValueError(
                f"deposit_cents {self.deposit_cents} + final_payment_cents"
                f" {self.final_payment_cents} != price_cents {self.price_cents}"
            )
        completed = self.status is EventStatus.COMPLETED
        if completed and (self.costs_paid_date is None or self.final_paid_date is None):
            raise ValueError("a completed event has costs_paid_date and final_paid_date")
        if not completed and self.final_paid_date is not None:
            raise ValueError(f"a {self.status} event has no final_paid_date")
        return self


class Offer(_Entity):
    id: Id
    kind: OfferKind
    amount_cents: NonNegativeCents
    term_days: Annotated[int, Field(strict=True, gt=0)]
    basis: frozendict[str, Annotated[int, Field(strict=True)]]
    """The numbers the rule used, by name (cents, days, counts)."""
    simulated: Flag


class ApprovalRequest(_Entity):
    id: Id
    action: str
    amount_cents: NonNegativeCents
    reason: str
    status: ApprovalStatus
    created_at: AwareDatetime
    decided_at: AwareDatetime | None

    @model_validator(mode="after")
    def _decided_once_decided(self) -> Self:
        pending = self.status is ApprovalStatus.PENDING
        if pending != (self.decided_at is None):
            raise ValueError("decided_at is set exactly when the status is not pending")
        return self


class Receipt(_Entity):
    """One action Ena or the owner took. Append-only: never changed once written."""

    id: Id
    at: AwareDatetime
    actor: Actor
    action: str
    amount_cents: NonNegativeCents
    ref: str
    """What the action was on: an invoice, a payment or an offer."""
    result: str
    explanation: str
    simulated: Flag = False
    """A paid payment whose bank moves no money (the stand-in); False for any other
    receipt, and for one stored before the label existed."""
    sandbox: Flag = False
    """A paid payment the Bank of Cyprus sandbox executed (test money, `CPLT`); False for
    any other receipt."""

    @model_validator(mode="after")
    def _one_bank_label(self) -> Self:
        if self.simulated and self.sandbox:
            raise ValueError("a receipt is simulated or from the sandbox, not both")
        return self


class BankConsent(_Entity):
    """A payment the bank holds until the owner confirms it in the bank's own screen.

    bocAPI asks for that confirmation on every payment (docs/boc-notes.md §3): the gate has
    decided to pay (within the limit, or the owner approved), the bank has the payment
    initiated, and nothing is paid until the owner confirms at `consent_url`.
    """

    id: Id
    action: str
    amount_cents: NonNegativeCents
    ref: str
    actor: Actor
    """Who decided to pay: Ena within the limit, or the owner by approving."""
    consent_url: Annotated[str, StringConstraints(pattern=r"^https://")]
    created_at: AwareDatetime


class BankLine(_Entity):
    """A statement line as the Bank of Cyprus sandbox returned it."""

    id: Id
    date: IsoDate
    amount_cents: NonNegativeCents
    direction: Literal["in", "out"] | None
    """None when the sandbox's line carries no `dcInd` (some sample rows don't)."""
    description: str


class BankAccount(_Entity):
    """The Bank of Cyprus sandbox account, read live: a sample customer's, not the
    synthetic business's, so its figures are never mixed with the synthetic month's."""

    account_id: Id
    iban: Iban | None
    currency: Currency
    available_cents: Cents | None
    current_cents: Cents | None
    read_at: AwareDatetime
    lines: tuple[BankLine, ...]
    """Newest first."""


Card = ApprovalRequest | BankConsent | Receipt | Forecast | Offer | BankAccount
"""What a chat message can show as a card (§8)."""


class ChatMessage(_Entity):
    id: Id
    at: AwareDatetime
    role: Role
    text: str
    cards: tuple[Card, ...] = ()
