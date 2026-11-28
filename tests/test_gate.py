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


class BankDown(RuntimeError):
    """The bank call broke off: whether the money went out is unknown."""


@dataclass
class FakeBank:
    """Answers every payment with `status` and records each call; raises once when `down`."""

    status: str = "CPLT"
    down: bool = False
    calls: list[tuple[str, int, str]] = field(default_factory=list)
    simulated: bool = False
    """Whether its payments are simulated; False makes it a real, bocAPI-shaped bank."""

    def pay(self, action: str, amount_cents: int, ref: str) -> str:
        self.calls.append((action, amount_cents, ref))
        if self.down:
            self.down = False
            raise BankDown("connection reset")
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


# A simulated bank: its payments say so (docs/build-plan.md §3 principle 5)

SIMULATED = " (simulated: no money moved)"


def _paid_by(gate: Gate, path: str) -> Receipt:
    if path == "auto":
        receipt = gate.request_payment("pay_invoice", 12_000, "inv-1")
        assert isinstance(receipt, Receipt)
        return receipt
    return gate.approve(_pending(gate).id)


@pytest.mark.parametrize("path", ["auto", "approve"])
def test_a_payment_a_simulated_bank_completed_is_labelled_simulated(
    store: Store, bank: FakeBank, path: str
) -> None:
    bank.simulated = True
    gate = Gate(BUSINESS, store, bank, FakeClock())

    receipt = _paid_by(gate, path)

    assert (receipt.result, receipt.simulated) == ("paid", True)
    assert receipt.explanation.endswith("bank status CPLT" + SIMULATED)
    assert store.receipts() == [receipt]
    assert gate.request_payment("pay_invoice", receipt.amount_cents, receipt.ref) == receipt
    assert len(bank.calls) == 1


@pytest.mark.parametrize("path", ["auto", "approve"])
def test_a_payment_a_real_bank_completed_is_not_labelled_simulated(
    gate: Gate, store: Store, path: str
) -> None:
    receipt = _paid_by(gate, path)

    assert (receipt.result, receipt.simulated) == ("paid", False)
    assert "simulated" not in receipt.explanation
    assert store.receipts() == [receipt]


@pytest.mark.parametrize("path", ["auto", "approve"])
def test_a_failed_payment_through_a_simulated_bank_claims_no_simulated_payment(
    store: Store, bank: FakeBank, path: str
) -> None:
    bank.simulated = True
    bank.status = "RJCT"
    gate = Gate(BUSINESS, store, bank, FakeClock())

    receipt = _paid_by(gate, path)

    assert (receipt.result, receipt.simulated) == ("failed", False)
    assert "simulated" not in receipt.explanation


def test_a_decline_with_a_simulated_bank_claims_no_simulated_payment(
    store: Store, bank: FakeBank
) -> None:
    bank.simulated = True
    gate = Gate(BUSINESS, store, bank, FakeClock())

    receipt = gate.decline(_pending(gate).id)

    assert (receipt.result, receipt.simulated) == ("declined", False)


class UndeclaredBank:
    """A bank that doesn't say whether its payments are simulated."""

    def pay(self, action: str, amount_cents: int, ref: str) -> str:
        return "CPLT"


def test_a_bank_that_does_not_declare_counts_as_simulated(store: Store) -> None:
    gate = Gate(BUSINESS, store, UndeclaredBank(), FakeClock())

    receipt = gate.request_payment("pay_invoice", 12_000, "inv-1")

    assert isinstance(receipt, Receipt)
    assert receipt.simulated is True
    assert receipt.explanation.endswith(SIMULATED)


@dataclass
class VagueBank:
    simulated: object

    def pay(self, action: str, amount_cents: int, ref: str) -> str:
        return "CPLT"


@pytest.mark.parametrize("simulated", [None, 0, 1, "yes"])
def test_a_bank_whose_declaration_is_not_a_bool_is_refused(store: Store, simulated: object) -> None:
    with pytest.raises(TypeError, match="simulated must be a bool"):
        Gate(BUSINESS, store, VagueBank(simulated), FakeClock())


def test_a_receipt_stored_before_the_label_loads_as_not_simulated() -> None:
    stored = (
        '{"id": "receipt-inv-1", "at": "2026-11-28T09:00:00Z", "actor": "ena",'
        ' "action": "pay_invoice", "amount_cents": 12000, "ref": "inv-1", "result": "paid",'
        ' "explanation": "paid €120.00, bank status CPLT"}'
    )

    assert Receipt.model_validate_json(stored).simulated is False


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


# A bank call that raises: never paid twice

MAY_HAVE_GONE_OUT = "payment for inv-1 may have gone out: check the bank before retrying"


def test_a_raising_bank_on_the_auto_path_blocks_the_next_trigger(
    gate: Gate, store: Store, bank: FakeBank
) -> None:
    bank.down = True
    with pytest.raises(BankDown):
        gate.request_payment("pay_invoice", 12_000, "inv-1")

    with pytest.raises(GateError) as error:
        gate.request_payment("pay_invoice", 12_000, "inv-1")

    assert str(error.value) == MAY_HAVE_GONE_OUT
    assert store.has_attempt("inv-1")
    assert store.receipts() == []
    assert len(bank.calls) == 1


def test_a_raising_bank_on_the_approve_path_blocks_the_next_trigger(
    gate: Gate, store: Store, bank: FakeBank
) -> None:
    request = _pending(gate)
    bank.down = True
    with pytest.raises(BankDown):
        gate.approve(request.id)

    with pytest.raises(GateError) as error:
        gate.request_payment("pay_invoice", 124_000, "inv-17")
    with pytest.raises(GateError, match="already approved"):
        gate.approve(request.id)

    assert str(error.value) == MAY_HAVE_GONE_OUT.replace("inv-1", "inv-17")
    assert store.has_attempt("inv-17")
    assert store.receipts() == []
    assert len(bank.calls) == 1


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


def test_the_store_has_no_api_to_change_or_delete_a_receipt_or_an_attempt() -> None:
    public = {name for name in vars(Store) if not name.startswith("_")}

    assert public == {
        "close",
        "add_receipt",
        "receipt_for",
        "receipts",
        "add_attempt",
        "has_attempt",
        "add_card_sale",
        "card_sale_for",
        "card_sales",
        "add_approval",
        "approval",
        "pending_approvals",
        "pending_approval_for",
        "record_approval",
        "record_decline",
        "add_advance",
        "advance",
        "accepted_advance_for",
        "record_advance_decision",
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
        store.record_approval(approved, "inv-17", T1)
    assert not store.has_attempt("inv-17")


# A decision and its next write land together


def _decided(request: ApprovalRequest, status: ApprovalStatus) -> ApprovalRequest:
    return ApprovalRequest.model_validate(
        {**request.model_dump(), "status": status, "decided_at": T1}
    )


@dataclass
class BreakingClock:
    """Timezone-aware for the first `good` calls, naive after: the gate breaks off there."""

    good: int

    def __call__(self) -> dt.datetime:
        self.good -= 1
        return T0 if self.good >= 0 else T0.replace(tzinfo=None)


def test_an_approve_that_breaks_off_never_leaves_the_ref_stuck(
    store: Store, bank: FakeBank
) -> None:
    gate = Gate(BUSINESS, store, bank, BreakingClock(good=2))
    request = _pending(gate)
    with pytest.raises(ValueError, match="timezone-aware"):
        gate.approve(request.id)

    restarted = Gate(BUSINESS, store, bank, FakeClock())
    with pytest.raises(GateError, match="may have gone out"):
        restarted.request_payment("pay_invoice", 124_000, "inv-17")
    assert len(bank.calls) <= 1


def test_an_approval_whose_attempt_fails_stays_pending(store: Store) -> None:
    gate = Gate(BUSINESS, store, FakeBank(), FakeClock())
    request = _pending(gate)
    store.add_attempt("inv-17", T0)

    with pytest.raises(sqlite3.IntegrityError):
        store.record_approval(_decided(request, ApprovalStatus.APPROVED), "inv-17", T1)
    assert store.pending_approval_for("inv-17") == request


def test_a_decline_whose_receipt_fails_stays_pending(store: Store) -> None:
    gate = Gate(BUSINESS, store, FakeBank(), FakeClock())
    request = _pending(gate)
    declined = _decided(request, ApprovalStatus.DECLINED)
    receipt = Receipt(
        id="receipt-inv-17",
        at=T1,
        actor=Actor.OWNER,
        action="pay_invoice",
        amount_cents=124_000,
        ref="inv-17",
        result="declined",
        explanation="Declined by the owner; nothing was paid",
    )
    store.add_receipt(receipt.model_copy(update={"id": "receipt-other"}))

    with pytest.raises(sqlite3.IntegrityError):
        store.record_decline(declined, receipt)
    assert store.pending_approval_for("inv-17") == request
