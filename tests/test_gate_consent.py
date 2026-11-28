"""The gate with a bank that asks the owner to confirm each payment in its own screen."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest

from ena.gate import REPAY_PREFIX, Executed, Gate, GateError, Initiated, PaymentResult
from ena.models import Actor, ApprovalRequest, BankConsent, Business, Receipt
from ena.store import Store

T0 = dt.datetime(2026, 11, 28, 9, 0, tzinfo=dt.UTC)

BUSINESS = Business(
    id="biz-1",
    name="Bakery",
    sector="bakery",
    account_id="acc-1",
    auto_pay_limit_cents=50_000,
    synthetic=True,
)


@dataclass
class FakeConsentBank:
    """Initiates every payment and executes it with `status`; records each call."""

    status: str = "CPLT"
    simulated: bool = False
    initiated: list[tuple[str, int, str]] = field(default_factory=list)
    executed: list[tuple[str, str]] = field(default_factory=list)

    def initiate(self, action: str, amount_cents: int, ref: str) -> Initiated:
        self.initiated.append((action, amount_cents, ref))
        return Initiated(f"pay-{ref}", f"https://bank.test/authorize?paymentid=pay-{ref}")

    def execute(self, payment_id: str, code: str) -> Executed:
        self.executed.append((payment_id, code))
        return Executed(self.status, "REF-1")


@pytest.fixture
def store() -> Iterator[Store]:
    store = Store(":memory:")
    yield store
    store.close()


@pytest.fixture
def bank() -> FakeConsentBank:
    return FakeConsentBank()


@pytest.fixture
def gate(store: Store, bank: FakeConsentBank) -> Gate:
    return Gate(BUSINESS, store, bank, lambda: T0)


def test_within_the_limit_the_payment_waits_for_the_bank_confirmation(
    gate: Gate, bank: FakeConsentBank, store: Store
) -> None:
    consent = gate.request_payment("pay_invoice", 18_000, "inv-1")

    assert isinstance(consent, BankConsent)
    assert (consent.actor, consent.amount_cents, consent.ref) == (Actor.ENA, 18_000, "inv-1")
    assert consent.consent_url == "https://bank.test/authorize?paymentid=pay-inv-1"
    assert bank.initiated == [("pay_invoice", 18_000, "inv-1")]
    assert bank.executed == []
    assert store.receipts() == []
    assert gate.needs_consent
    # Asked again, the same consent comes back and nothing is initiated twice
    assert gate.request_payment("pay_invoice", 18_000, "inv-1") == consent
    assert len(bank.initiated) == 1


def test_the_confirmation_executes_the_payment_and_writes_a_sandbox_receipt(
    gate: Gate, bank: FakeConsentBank, store: Store
) -> None:
    consent = gate.request_payment("pay_invoice", 18_000, "inv-1")
    assert isinstance(consent, BankConsent)

    receipt = gate.confirm("code-1")

    assert bank.executed == [("pay-inv-1", "code-1")]
    assert receipt.result == PaymentResult.PAID
    assert (receipt.actor, receipt.sandbox, receipt.simulated) == (Actor.ENA, True, False)
    assert "confirmed in the bank, paid €180.00, bank status CPLT, bank ref REF-1" in (
        receipt.explanation
    )
    assert store.receipts() == [receipt]
    assert gate.consent(consent.id) == receipt
    assert gate.request_payment("pay_invoice", 18_000, "inv-1") == receipt
    with pytest.raises(GateError, match="no payment waits"):
        gate.confirm("code-2")


def test_an_approved_payment_waits_for_the_bank_too(gate: Gate, bank: FakeConsentBank) -> None:
    request = gate.request_payment("pay_invoice", 124_000, "inv-2")
    assert isinstance(request, ApprovalRequest)
    assert bank.initiated == []

    consent = gate.approve(request.id)

    assert isinstance(consent, BankConsent)
    assert consent.actor is Actor.OWNER
    receipt = gate.confirm("code-1")
    assert (receipt.actor, receipt.result) == (Actor.OWNER, PaymentResult.PAID)
    assert receipt.explanation.startswith("Approved by the owner; confirmed in the bank")


def test_a_bank_status_other_than_cplt_is_a_failed_receipt(
    gate: Gate, bank: FakeConsentBank
) -> None:
    bank.status = "RJCT"
    gate.request_payment("pay_invoice", 18_000, "inv-1")

    receipt = gate.confirm("code-1")

    assert (receipt.result, receipt.sandbox) == (PaymentResult.FAILED, False)
    assert "'RJCT', not paid and not retried" in receipt.explanation


def test_a_refused_confirmation_is_a_failed_receipt(gate: Gate, bank: FakeConsentBank) -> None:
    gate.request_payment("pay_invoice", 18_000, "inv-1")

    receipt = gate.refuse("access_denied")

    assert receipt.result == PaymentResult.FAILED
    assert "refused (access_denied)" in receipt.explanation
    assert bank.executed == []


def test_the_owner_cancels_a_waiting_confirmation(gate: Gate, bank: FakeConsentBank) -> None:
    consent = gate.request_payment("pay_invoice", 18_000, "inv-1")
    assert isinstance(consent, BankConsent)

    receipt = gate.cancel(consent.id)

    assert receipt.result == PaymentResult.FAILED
    assert "cancelled before the bank's confirmation" in receipt.explanation
    with pytest.raises(GateError, match="already settled"):
        gate.cancel(consent.id)
    with pytest.raises(GateError, match="no bank confirmation"):
        gate.cancel("consent-unknown")


def test_one_confirmation_waits_at_a_time(gate: Gate, bank: FakeConsentBank) -> None:
    gate.request_payment("pay_invoice", 18_000, "inv-1")
    request = gate.request_payment("pay_invoice", 124_000, "inv-2")
    assert isinstance(request, ApprovalRequest)

    with pytest.raises(GateError, match="waits for its confirmation in the bank"):
        gate.request_payment("pay_invoice", 9_000, "inv-3")
    with pytest.raises(GateError, match="waits for its confirmation in the bank"):
        gate.approve(request.id)
    assert len(bank.initiated) == 1

    gate.confirm("code-1")
    assert isinstance(gate.request_payment("pay_invoice", 9_000, "inv-3"), BankConsent)


def test_a_simulated_advance_is_never_repaid_through_the_sandbox(
    gate: Gate, bank: FakeConsentBank
) -> None:
    with pytest.raises(GateError, match="simulated advance"):
        gate.request_payment("repay", 10_000, f"{REPAY_PREFIX}offer-1")
    assert bank.initiated == []


def test_a_bank_without_consent_refuses_the_confirmation_routes(store: Store) -> None:
    @dataclass
    class StandIn:
        simulated: bool = True

        def pay(self, action: str, amount_cents: int, ref: str) -> str:
            return "CPLT"

    gate = Gate(BUSINESS, store, StandIn(), lambda: T0)

    assert not gate.needs_consent
    assert isinstance(gate.request_payment("pay_invoice", 18_000, "inv-1"), Receipt)
    with pytest.raises(GateError, match="without a confirmation"):
        gate.confirm("code-1")
