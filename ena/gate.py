"""The gate: the one door for money (docs/build-plan.md §3 principle 2, §6, §11).

Every payment goes through `Gate.request_payment`; nothing else calls the bank.
At or below the business's auto-pay limit the gate pays and writes a receipt by Ena;
above it, it creates a pending approval request that only the owner's `approve` or
`decline` settles. Each settled payment has exactly one receipt, and a ref that has one
is never paid again: a duplicate trigger gets the same receipt back, and a failed
payment is not retried. An attempt is recorded before the bank is called, so when the
call raises and its outcome is unknown, the next trigger for the ref fails loudly
instead of paying again.

A ref starting with `REPAY_PREFIX` repays a simulated event advance (`ena/advances.py`):
the gate refuses it, before any attempt or approval is recorded, when its bank moves real
money, both when it is requested and when the owner approves it. So an approval created
on the stand-in bank never pays real money after a restart onto a real one.
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

SIMULATED_NOTE = " (simulated: no money moved)"

REPAY_PREFIX = "repay-"
"""Starts the ref of a simulated advance's repayment: never paid with real money."""
"""Ends the explanation of a payment a simulated bank completed."""


class PaymentResult(StrEnum):
    PAID = "paid"
    FAILED = "failed"
    DECLINED = "declined"


class GateError(RuntimeError):
    """A request the gate refuses: an unknown or already decided approval, or a ref
    whose earlier bank call ended without a receipt."""


class PaymentBank(Protocol):
    """The payment connector, as the gate sees it.

    A bank declares whether its payments are simulated with a `simulated` bool attribute:
    a real bank (bocAPI) sets it False. A bank that doesn't declare it counts as simulated,
    so no receipt says real money moved unless its bank says so (docs/build-plan.md §3
    principle 5).
    """

    def pay(self, action: str, amount_cents: int, ref: str) -> str:
        """Make the payment and return the bank's status (`CPLT` when completed)."""
        ...


def is_simulated(bank: PaymentBank) -> bool:
    """The bank's `simulated` declaration, True when it makes none; refuses a non-bool."""
    declared = getattr(bank, "simulated", True)
    if type(declared) is not bool:
        raise TypeError(f"a bank's simulated must be a bool, got {declared!r}")
    return declared


Clock = Callable[[], dt.datetime]
"""Returns the current time, timezone-aware."""


def _euros(cents: int) -> str:
    return f"€{cents // 100:,}.{cents % 100:02d}"


class Gate:
    def __init__(self, business: Business, store: Store, bank: PaymentBank, clock: Clock) -> None:
        self._business = business
        self._store = store
        self._bank = bank
        self._simulated = is_simulated(bank)
        self._clock = clock

    @property
    def simulated(self) -> bool:
        """Whether the gate's bank moves no money (the stand-in)."""
        return self._simulated

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
        if self._store.has_attempt(ref):
            raise GateError(f"payment for {ref} may have gone out: check the bank before retrying")
        self._refuse_real_money_for_simulated(ref)
        pending = self._store.pending_approval_for(ref)
        if pending is not None:
            return pending
        limit = self._business.auto_pay_limit_cents
        if amount_cents <= limit:
            self._store.add_attempt(ref, self._now())
            return self._pay(
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
        request, ref, now = self._decide(approval_id, ApprovalStatus.APPROVED)
        self._refuse_real_money_for_simulated(ref)
        self._store.record_approval(request, ref, now)
        return self._pay(
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
        self._store.record_decline(request, receipt)
        return receipt

    def _refuse_real_money_for_simulated(self, ref: str) -> None:
        if ref.startswith(REPAY_PREFIX) and not self._simulated:
            raise GateError(f"{ref} repays a simulated advance: it is never paid with real money")

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
        return decided, stored.ref, now

    def _pay(self, actor: Actor, action: str, amount_cents: int, ref: str, why: str) -> Receipt:
        """Call the bank and write the receipt; the caller has recorded the attempt."""
        status = self._bank.pay(action, amount_cents, ref)
        simulated = False
        if status == COMPLETED:
            result = PaymentResult.PAID
            simulated = self._simulated
            explanation = f"{why}; paid {_euros(amount_cents)}, bank status {status}"
            if simulated:
                explanation += SIMULATED_NOTE
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
            simulated=simulated,
        )
        self._store.add_receipt(receipt)
        return receipt

    def _now(self) -> dt.datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError(f"the clock must return a timezone-aware datetime: {now!r}")
        return now
