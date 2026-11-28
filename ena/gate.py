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

A bank that needs the owner's confirmation in its own screen for every payment (bocAPI,
`ConsentBank`) splits paying in two. When the gate decides to pay (within the limit, or
the owner approved), it records the attempt, has the bank initiate the payment and returns
a `BankConsent`: the card with the bank's confirmation link. Nothing is paid yet. The
bank's redirect after the confirmation brings a one-time code to `confirm`, which executes
the payment and writes the receipt; a refusal there, or the owner's `cancel`, writes a
failed one. The redirect names no payment, so at most one consent is open at a time: a
second payment waits until the first is confirmed or cancelled.

Nothing is paid before `execute`, so a consent bank's attempt is recorded together with
its consent, after `initiate` succeeded: an `initiate` that fails (`BankUnavailable`)
records nothing, and the owner can ask again. In `confirm`, a code the bank won't exchange
(`BankUnavailable`) leaves the consent open, while an `execute` that breaks off
(`BankOutcomeUnknown`) closes it with a receipt whose result is unknown: it is to be
checked at the bank, and the ref is never paid again.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from ena.models import Actor, ApprovalRequest, ApprovalStatus, BankConsent, Business, Receipt
from ena.store import Store, StoredConsent

COMPLETED = "CPLT"
"""The bank's status for a completed payment."""

SIMULATED_NOTE = " (simulated: no money moved)"
"""Ends the explanation of a payment a simulated bank completed."""

REPAY_PREFIX = "repay-"
"""Starts the ref of a simulated advance's repayment: never paid with real money.

The guard reads the ref, not the offer: every event advance is simulated (N-1, lending
itself is a non-goal). Real lending would need the guard to read the offer instead."""


class PaymentResult(StrEnum):
    PAID = "paid"
    FAILED = "failed"
    DECLINED = "declined"
    UNKNOWN = "unknown"
    """The bank call broke off after the payment was sent: check the bank."""


class BankUnavailable(RuntimeError):
    """A consent bank failed before anything could be paid: nothing went out."""


class BankOutcomeUnknown(RuntimeError):
    """A consent bank's execute broke off: whether it paid is unknown."""


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


@dataclass(frozen=True, slots=True)
class Initiated:
    """A payment the bank has initiated and holds for the owner's confirmation."""

    payment_id: str
    consent_url: str
    """The bank's own screen where the owner confirms this payment."""


@dataclass(frozen=True, slots=True)
class Executed:
    """The bank's answer to executing a confirmed payment."""

    status: str
    """`CPLT` when completed."""
    bank_ref: str | None


@runtime_checkable
class ConsentBank(Protocol):
    """A bank that asks the owner to confirm every payment in its own screen (bocAPI).

    It declares `simulated` like a `PaymentBank`.
    """

    def initiate(self, action: str, amount_cents: int, ref: str) -> Initiated:
        """Initiate the payment; nothing is paid until `execute`. Raises `BankUnavailable`
        when the bank can't initiate it."""
        ...

    def execute(self, payment_id: str, code: str) -> Executed:
        """Execute an initiated payment with the code the owner's confirmation returned.

        Raises `BankUnavailable` when it fails before the payment is sent (the code is
        refused), `BankOutcomeUnknown` when the execute call itself breaks off.
        """
        ...


def is_simulated(bank: PaymentBank | ConsentBank) -> bool:
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
    def __init__(
        self, business: Business, store: Store, bank: PaymentBank | ConsentBank, clock: Clock
    ) -> None:
        self._business = business
        self._store = store
        self._bank = bank
        self._simulated = is_simulated(bank)
        self._clock = clock

    @property
    def simulated(self) -> bool:
        """Whether the gate's bank moves no money (the stand-in)."""
        return self._simulated

    @property
    def needs_consent(self) -> bool:
        """Whether each payment waits for the owner's confirmation in the bank."""
        return isinstance(self._bank, ConsentBank)

    def request_payment(
        self, action: str, amount_cents: int, ref: str
    ) -> Receipt | ApprovalRequest | BankConsent:
        """Pay at or below the limit, ask the owner above it; a known ref gets its answer back.

        With a `ConsentBank` a payment the gate makes comes back as the `BankConsent` that
        waits for the owner's confirmation in the bank.
        """
        if not action or not ref:
            raise ValueError("a payment needs an action and a ref")
        if type(amount_cents) is not int or amount_cents <= 0:
            raise ValueError(f"a payment amount must be positive integer cents: {amount_cents!r}")
        existing = self._store.receipt_for(ref)
        if existing is not None:
            return existing
        waiting = self._store.open_consent()
        if waiting is not None and waiting.consent.ref == ref:
            return waiting.consent
        if self._store.has_attempt(ref):
            raise GateError(f"payment for {ref} may have gone out: check the bank before retrying")
        self._refuse_real_money_for_simulated(ref)
        pending = self._store.pending_approval_for(ref)
        if pending is not None:
            return pending
        limit = self._business.auto_pay_limit_cents
        if amount_cents <= limit:
            why = f"{_euros(amount_cents)} is within the {_euros(limit)} auto-pay limit"
            if isinstance(self._bank, ConsentBank):
                self._refuse_second_consent()
                consent, payment_id = self._initiate(
                    self._bank, Actor.ENA, action, amount_cents, ref
                )
                self._store.add_consent(consent, payment_id, why, self._now())
                return consent
            self._store.add_attempt(ref, self._now())
            return self._pay(Actor.ENA, action, amount_cents, ref, why)
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

    def approve(self, approval_id: str) -> Receipt | BankConsent:
        """The owner's approval: pay once and write the owner's receipt (with a
        `ConsentBank`, return the consent that waits for the owner's confirmation)."""
        request, ref, now = self._decide(approval_id, ApprovalStatus.APPROVED)
        self._refuse_real_money_for_simulated(ref)
        why = "Approved by the owner"
        if isinstance(self._bank, ConsentBank):
            self._refuse_second_consent()
            consent, payment_id = self._initiate(
                self._bank, Actor.OWNER, request.action, request.amount_cents, ref
            )
            self._store.record_approval(request, ref, now, StoredConsent(consent, payment_id, why))
            return consent
        self._store.record_approval(request, ref, now)
        return self._pay(Actor.OWNER, request.action, request.amount_cents, ref, why)

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

    def confirm(self, code: str) -> Receipt:
        """The bank's redirect after the owner confirmed: execute the open consent's
        payment with its one-time code and write the receipt."""
        if not code.strip():
            raise ValueError("the bank's confirmation code is empty")
        bank = self._consent_bank()
        stored = self._open_consent()
        try:
            executed = bank.execute(stored.payment_id, code)
        except BankUnavailable as error:
            raise GateError(
                f"the bank's confirmation could not be used ({error}): nothing was paid; open the"
                " bank's screen again from the card"
            ) from error
        except BankOutcomeUnknown as error:
            return self._close(
                stored,
                PaymentResult.UNKNOWN,
                f"{stored.why}; the bank's answer to the payment was lost ({error}): check the"
                " bank, Ena will not pay it again",
            )
        consent = stored.consent
        amount = _euros(consent.amount_cents)
        if executed.status == COMPLETED:
            result = PaymentResult.PAID
            explanation = (
                f"{stored.why}; confirmed in the bank, paid {amount}, bank status {executed.status}"
            )
            if executed.bank_ref:
                explanation += f", bank ref {executed.bank_ref}"
            if self._simulated:
                explanation += SIMULATED_NOTE
        else:
            result = PaymentResult.FAILED
            explanation = (
                f"{stored.why}; the bank returned {executed.status!r}, not paid and not retried"
            )
        paid = result is PaymentResult.PAID
        return self._close(
            stored,
            result,
            explanation,
            simulated=paid and self._simulated,
            sandbox=paid and not self._simulated,
        )

    def refuse(self, error: str) -> Receipt:
        """The bank's redirect said the owner did not confirm: a failed receipt."""
        self._consent_bank()
        stored = self._open_consent()
        return self._close(
            stored,
            PaymentResult.FAILED,
            f"{stored.why}; the bank confirmation was refused ({error}), not paid and not retried",
        )

    def cancel(self, consent_id: str) -> Receipt:
        """The owner stops waiting for the bank's confirmation: a failed receipt."""
        stored = self._store.consent(consent_id)
        if stored is None:
            raise GateError(f"no bank confirmation {consent_id!r}")
        waiting = self._store.open_consent()
        if waiting is None or waiting.consent.id != consent_id:
            raise GateError(f"bank confirmation {consent_id!r} is already settled")
        return self._close(
            stored,
            PaymentResult.FAILED,
            f"{stored.why}; cancelled before the bank's confirmation reached Ena, not paid"
            " through Ena (if you confirmed it in the bank, check the bank)",
        )

    def consent(self, consent_id: str) -> Receipt | BankConsent:
        """The consent while it waits, its receipt once settled."""
        stored = self._store.consent(consent_id)
        if stored is None:
            raise GateError(f"no bank confirmation {consent_id!r}")
        receipt = self._store.receipt_for(stored.consent.ref)
        return stored.consent if receipt is None else receipt

    def _initiate(
        self, bank: ConsentBank, actor: Actor, action: str, amount_cents: int, ref: str
    ) -> tuple[BankConsent, str]:
        """Have the bank initiate the payment; nothing is recorded, so a failure can be
        asked again."""
        try:
            initiated = bank.initiate(action, amount_cents, ref)
        except BankUnavailable as error:
            raise GateError(
                f"the bank could not start the payment ({error}): nothing was paid, ask again"
            ) from error
        consent = BankConsent(
            id=f"consent-{ref}",
            action=action,
            amount_cents=amount_cents,
            ref=ref,
            actor=actor,
            consent_url=initiated.consent_url,
            created_at=self._now(),
        )
        return consent, initiated.payment_id

    def _consent_bank(self) -> ConsentBank:
        if not isinstance(self._bank, ConsentBank):
            raise GateError("this bank completes payments without a confirmation in the bank")
        return self._bank

    def _open_consent(self) -> StoredConsent:
        stored = self._store.open_consent()
        if stored is None:
            raise GateError("no payment waits for a confirmation in the bank")
        return stored

    def _close(
        self,
        stored: StoredConsent,
        result: PaymentResult,
        explanation: str,
        *,
        simulated: bool = False,
        sandbox: bool = False,
    ) -> Receipt:
        consent = stored.consent
        receipt = Receipt(
            id=f"receipt-{consent.ref}",
            at=self._now(),
            actor=consent.actor,
            action=consent.action,
            amount_cents=consent.amount_cents,
            ref=consent.ref,
            result=result,
            explanation=explanation,
            simulated=simulated,
            sandbox=sandbox,
        )
        self._store.close_consent(consent.id, receipt)
        return receipt

    def _refuse_second_consent(self) -> None:
        """A payment through a `ConsentBank` waits while another one is open."""
        if not self.needs_consent:
            return
        waiting = self._store.open_consent()
        if waiting is not None:
            raise GateError(
                f"{waiting.consent.action} waits for its confirmation in the bank: confirm or"
                " cancel it first"
            )

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
        """Call a bank that pays at once and write the receipt; the caller has recorded the
        attempt."""
        if isinstance(self._bank, ConsentBank):
            raise TypeError("a consent bank pays through _initiate and confirm")
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
