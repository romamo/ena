"""The gate (docs/build-plan.md §6, §11, §13): every branch writes exactly one receipt."""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest

from ena.gate import Gate, GateError
from ena.models import Actor, ApprovalRequest, ApprovalStatus, Business, Receipt
from ena.store import Store, StoreError

T0 = dt.datetime(2026, 11, 28, 9, 0, tzinfo=dt.UTC)
T1 = dt.datetime(2026, 11, 28, 10, 30, tzinfo=dt.UTC)

BUSINESS = Business(
    id="biz-1",
    name="Bakery",
    sector="bakery",
    account_id="acc-1",
    auto_pay_limit_cents=50_000,
    synthetic=True,
)


@dataclass
class FakeBank:
    """Answers every payment with `status` and records each call."""

    status: str = "CPLT"
    calls: list[tuple[str, int, str]] = field(default_factory=list)

    def pay(self, action: str, amount_cents: int, ref: str) -> str:
        self.calls.append((action, amount_cents, ref))
        return self.status


@dataclass
class FakeClock:
    now: dt.datetime = T0

    def __call__(self) -> dt.datetime:
        return self.now


@pytest.fixture
def store() -> Iterator[Store]:
    store = Store(":memory:")
    yield store
    store.close()


@pytest.fixture
def bank() -> FakeBank:
    return FakeBank()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def gate(store: Store, bank: FakeBank, clock: FakeClock) -> Gate:
    return Gate(BUSINESS, store, bank, clock)


def _pending(gate: Gate, amount_cents: int = 124_000) -> ApprovalRequest:
    request = gate.request_payment("pay_invoice", amount_cents, "inv-17")
    assert isinstance(request, ApprovalRequest)
    return request


# Below or at the limit: pays


@pytest.mark.parametrize(
    ("amount_cents", "explanation"),
    [
        (
            12_000,
            "€120.00 is within the €500.00 auto-pay limit; paid €120.00, bank status CPLT",
        ),
        (
            50_000,
            "€500.00 is within the €500.00 auto-pay limit; paid €500.00, bank status CPLT",
        ),
    ],
)
def test_at_or_below_the_limit_pays_with_one_receipt_by_ena(
    gate: Gate, store: Store, bank: FakeBank, amount_cents: int, explanation: str
) -> None:
    receipt = gate.request_payment("pay_invoice", amount_cents, "inv-1")

    assert receipt == Receipt(
        id="receipt-inv-1",
        at=T0,
        actor=Actor.ENA,
        action="pay_invoice",
        amount_cents=amount_cents,
        ref="inv-1",
        result="paid",
        explanation=explanation,
    )
    assert store.receipts() == [receipt]
    assert bank.calls == [("pay_invoice", amount_cents, "inv-1")]


def test_a_status_other_than_cplt_writes_one_failed_receipt(
    gate: Gate, store: Store, bank: FakeBank
) -> None:
    bank.status = "RJCT"

    receipt = gate.request_payment("pay_invoice", 12_000, "inv-1")

    assert isinstance(receipt, Receipt)
    assert receipt.result == "failed"
    assert "RJCT" in receipt.explanation
    assert store.receipts() == [receipt]
    assert len(bank.calls) == 1


def test_a_failed_payment_is_not_retried(gate: Gate, store: Store, bank: FakeBank) -> None:
    bank.status = "RJCT"
    failed = gate.request_payment("pay_invoice", 12_000, "inv-1")
    bank.status = "CPLT"

    again = gate.request_payment("pay_invoice", 12_000, "inv-1")

    assert again == failed
    assert store.receipts() == [failed]
    assert len(bank.calls) == 1


# Duplicate trigger: the same receipt


def test_a_duplicate_trigger_returns_the_same_receipt(
    gate: Gate, store: Store, bank: FakeBank, clock: FakeClock
) -> None:
    first = gate.request_payment("pay_invoice", 12_000, "inv-1")
    clock.now = T1

    second = gate.request_payment("pay_invoice", 12_000, "inv-1")

    assert second == first
    assert store.receipts() == [first]
    assert len(bank.calls) == 1


def test_a_duplicate_trigger_after_approval_returns_the_owners_receipt(
    gate: Gate, store: Store, bank: FakeBank
) -> None:
    receipt = gate.approve(_pending(gate).id)

    assert gate.request_payment("pay_invoice", 124_000, "inv-17") == receipt
    assert store.receipts() == [receipt]
    assert len(bank.calls) == 1


# Above the limit: an approval request


def test_above_the_limit_creates_a_pending_approval_and_no_receipt(
    gate: Gate, store: Store, bank: FakeBank
) -> None:
    request = gate.request_payment("pay_invoice", 50_001, "inv-17")

    assert request == ApprovalRequest(
        id="approval-inv-17",
        action="pay_invoice",
        amount_cents=50_001,
        reason="€500.01 is above the €500.00 auto-pay limit",
        status=ApprovalStatus.PENDING,
        created_at=T0,
        decided_at=None,
    )
    assert store.receipts() == []
    assert bank.calls == []


def test_a_duplicate_trigger_returns_the_same_pending_approval(
    gate: Gate, store: Store, bank: FakeBank, clock: FakeClock
) -> None:
    first = _pending(gate)
    clock.now = T1

    assert gate.request_payment("pay_invoice", 124_000, "inv-17") == first
    assert store.pending_approval_for("inv-17") == first
    assert store.receipts() == []
    assert bank.calls == []


# Approve: pays once


def test_approve_pays_once_with_one_receipt_by_the_owner(
    gate: Gate, store: Store, bank: FakeBank, clock: FakeClock
) -> None:
    request = _pending(gate)
    clock.now = T1

    receipt = gate.approve(request.id)

    assert receipt.actor is Actor.OWNER
    assert receipt.result == "paid"
    assert (receipt.id, receipt.at, receipt.amount_cents, receipt.ref) == (
        "receipt-inv-17",
        T1,
        124_000,
        "inv-17",
    )
    assert store.receipts() == [receipt]
    assert bank.calls == [("pay_invoice", 124_000, "inv-17")]
    stored = store.approval(request.id)
    assert stored is not None
    assert stored.request.status is ApprovalStatus.APPROVED
    assert stored.request.decided_at == T1


def test_approving_twice_fails_and_pays_once(gate: Gate, store: Store, bank: FakeBank) -> None:
    request = _pending(gate)
    receipt = gate.approve(request.id)

    with pytest.raises(GateError, match="already approved"):
        gate.approve(request.id)
    with pytest.raises(GateError, match="already approved"):
        gate.decline(request.id)
    assert store.receipts() == [receipt]
    assert len(bank.calls) == 1


def test_approve_with_a_failed_payment_writes_one_failed_receipt(
    gate: Gate, store: Store, bank: FakeBank
) -> None:
    bank.status = "RJCT"

    receipt = gate.approve(_pending(gate).id)

    assert (receipt.actor, receipt.result) == (Actor.OWNER, "failed")
    assert store.receipts() == [receipt]
    assert len(bank.calls) == 1


# Decline: never pays


def test_decline_writes_one_declined_receipt_and_never_pays(
    gate: Gate, store: Store, bank: FakeBank, clock: FakeClock
) -> None:
    request = _pending(gate)
    clock.now = T1

    receipt = gate.decline(request.id)

    assert receipt == Receipt(
        id="receipt-inv-17",
        at=T1,
        actor=Actor.OWNER,
        action="pay_invoice",
        amount_cents=124_000,
        ref="inv-17",
        result="declined",
        explanation="Declined by the owner; nothing was paid",
    )
    assert store.receipts() == [receipt]
    assert bank.calls == []
    stored = store.approval(request.id)
    assert stored is not None
    assert stored.request.status is ApprovalStatus.DECLINED
    assert stored.request.decided_at == T1


def test_declining_twice_fails_and_never_pays(gate: Gate, store: Store, bank: FakeBank) -> None:
    request = _pending(gate)
    receipt = gate.decline(request.id)

    with pytest.raises(GateError, match="already declined"):
        gate.decline(request.id)
    with pytest.raises(GateError, match="already declined"):
        gate.approve(request.id)
    assert gate.request_payment("pay_invoice", 124_000, "inv-17") == receipt
    assert store.receipts() == [receipt]
    assert bank.calls == []


def test_an_unknown_approval_fails(gate: Gate, store: Store, bank: FakeBank) -> None:
    with pytest.raises(GateError, match="no approval request"):
        gate.approve("approval-nope")
    with pytest.raises(GateError, match="no approval request"):
        gate.decline("approval-nope")
    assert store.receipts() == []
    assert bank.calls == []


# Strict input


@pytest.mark.parametrize(
    ("action", "amount_cents", "ref"),
    [
        ("", 100, "inv-1"),
        ("pay_invoice", 100, ""),
        ("pay_invoice", 0, "inv-1"),
        ("pay_invoice", -100, "inv-1"),
        ("pay_invoice", True, "inv-1"),
        ("pay_invoice", 1.5, "inv-1"),
    ],
)
def test_malformed_payments_are_refused(
    gate: Gate, bank: FakeBank, action: str, amount_cents: int, ref: str
) -> None:
    with pytest.raises(ValueError, match="a payment"):
        gate.request_payment(action, amount_cents, ref)
    assert bank.calls == []


def test_a_naive_clock_is_refused(store: Store, bank: FakeBank) -> None:
    gate = Gate(BUSINESS, store, bank, lambda: dt.datetime(2026, 11, 28, 9, 0))

    with pytest.raises(ValueError, match="timezone-aware"):
        gate.request_payment("pay_invoice", 124_000, "inv-17")


# The store: receipts are append-only


def test_the_store_has_no_api_to_change_or_delete_a_receipt() -> None:
    public = {name for name in vars(Store) if not name.startswith("_")}

    assert public == {
        "close",
        "add_receipt",
        "receipt_for",
        "receipts",
        "add_approval",
        "approval",
        "pending_approval_for",
        "decide_approval",
    }


def test_the_store_refuses_a_second_receipt_for_a_ref(gate: Gate, store: Store) -> None:
    receipt = gate.request_payment("pay_invoice", 12_000, "inv-1")
    assert isinstance(receipt, Receipt)

    with pytest.raises(sqlite3.IntegrityError):
        store.add_receipt(receipt.model_copy(update={"id": "receipt-other"}))
    assert store.receipts() == [receipt]


def test_the_store_refuses_to_decide_a_request_twice(gate: Gate, store: Store) -> None:
    request = _pending(gate)
    gate.decline(request.id)
    approved = ApprovalRequest.model_validate(
        {**request.model_dump(), "status": ApprovalStatus.APPROVED, "decided_at": T1}
    )

    with pytest.raises(StoreError, match="not pending"):
        store.decide_approval(approved)
