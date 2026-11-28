"""The gate: the one door for money (docs/build-plan.md §3 principle 2, §6, §11).

Every payment goes through `Gate.request_payment`; nothing else calls the bank.
At or below the business's auto-pay limit the gate pays and writes a receipt by Ena;
above it, it creates a pending approval request that only the owner's `approve` or
`decline` settles. Each settled payment has exactly one receipt, and a ref that has one
is never paid again: a duplicate trigger gets the same receipt back, and a failed
payment is not retried.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from enum import StrEnum
from typing import Protocol

from ena.models import Actor, ApprovalRequest, ApprovalStatus, Business, Receipt
from ena.store import Store

COMPLETED = "CPLT"
"""The bank's status for a completed payment."""


class PaymentResult(StrEnum):
    PAID = "paid"
    FAILED = "failed"
    DECLINED = "declined"


class GateError(RuntimeError):
    """A request the gate refuses: an unknown or already decided approval."""


class PaymentBank(Protocol):
    """The payment connector, as the gate sees it."""

    def pay(self, action: str, amount_cents: int, ref: str) -> str:
        """Make the payment and return the bank's status (`CPLT` when completed)."""
        ...


Clock = Callable[[], dt.datetime]
"""Returns the current time, timezone-aware."""


def _euros(cents: int) -> str:
    return f"€{cents // 100:,}.{cents % 100:02d}"


class Gate:
    def __init__(self, business: Business, store: Store, bank: PaymentBank, clock: Clock) -> None:
        self._business = business
        self._store = store
        self._bank = bank
        self._clock = clock

    def request_payment(
        self, action: str, amount_cents: int, ref: str
    ) -> Receipt | ApprovalRequest:
        """Pay at or below the limit, ask the owner above it; a known ref gets its answer back."""
        if not action or not ref:
            raise ValueError("a payment needs an action and a ref")
        if type(amount_cents) is not int or amount_cents <= 0:
            raise ValueError(f"a payment amount must be positive integer cents: {amount_cents!r}")
        existing = self._store.receipt_for(ref)
        if existing is not None:
            return existing
        pending = self._store.pending_approval_for(ref)
        if pending is not None:
            return pending
        limit = self._business.auto_pay_limit_cents
        if amount_cents <= limit:
            return self._execute(
                Actor.ENA,
                action,
                amount_cents,
                ref,
                f"{_euros(amount_cents)} is within the {_euros(limit)} auto-pay limit",
            )
        request = ApprovalRequest(
            id=f"approval-{ref}",
            action=action,
            amount_cents=amount_cents,
            reason=f"{_euros(amount_cents)} is above the {_euros(limit)} auto-pay limit",
            status=ApprovalStatus.PENDING,
            created_at=self._now(),
            decided_at=None,
        )
        self._store.add_approval(request, ref)
        return request

    def approve(self, approval_id: str) -> Receipt:
        """The owner's approval: pay once and write the owner's receipt."""
        request, ref, _ = self._decide(approval_id, ApprovalStatus.APPROVED)
        return self._execute(
            Actor.OWNER, request.action, request.amount_cents, ref, "Approved by the owner"
        )

    def decline(self, approval_id: str) -> Receipt:
        """The owner's decline: pay nothing and write a declined receipt."""
        request, ref, now = self._decide(approval_id, ApprovalStatus.DECLINED)
        receipt = Receipt(
            id=f"receipt-{ref}",
            at=now,
            actor=Actor.OWNER,
            action=request.action,
            amount_cents=request.amount_cents,
            ref=ref,
            result=PaymentResult.DECLINED,
            explanation="Declined by the owner; nothing was paid",
        )
        self._store.add_receipt(receipt)
        return receipt

    def _decide(
        self, approval_id: str, status: ApprovalStatus
    ) -> tuple[ApprovalRequest, str, dt.datetime]:
        stored = self._store.approval(approval_id)
        if stored is None:
            raise GateError(f"no approval request {approval_id!r}")
        if stored.request.status is not ApprovalStatus.PENDING:
            raise GateError(f"approval {approval_id!r} is already {stored.request.status}")
        now = self._now()
        decided = ApprovalRequest.model_validate(
            {**stored.request.model_dump(), "status": status, "decided_at": now}
        )
        self._store.decide_approval(decided)
        return decided, stored.ref, now

    def _execute(self, actor: Actor, action: str, amount_cents: int, ref: str, why: str) -> Receipt:
        status = self._bank.pay(action, amount_cents, ref)
        if status == COMPLETED:
            result = PaymentResult.PAID
            explanation = f"{why}; paid {_euros(amount_cents)}, bank status {status}"
        else:
            result = PaymentResult.FAILED
            explanation = f"{why}; the bank returned {status!r}, not paid and not retried"
        receipt = Receipt(
            id=f"receipt-{ref}",
            at=self._now(),
            actor=actor,
            action=action,
            amount_cents=amount_cents,
            ref=ref,
            result=result,
            explanation=explanation,
        )
        self._store.add_receipt(receipt)
        return receipt

    def _now(self) -> dt.datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError(f"the clock must return a timezone-aware datetime: {now!r}")
        return now
