"""The event advance after its offer: the owner's accept and decline, and the repayment
from the final payment through the gate, on the synthetic month with a fake bank."""

import datetime as dt
from dataclasses import dataclass, field

import pytest

from ena.advances import ACCEPTED, DECLINED, AdvanceError, Advances
from ena.gate import Gate, PaymentResult
from ena.mcp_server import EnaTools, Ledger, ReplayDay
from ena.models import AdvanceStatus, ApprovalRequest, Offer, Receipt, Transaction
from ena.replay import TZ, DayStarted, TransactionPosted, month_events
from ena.seed import BOOKED_EVENT_ID, LEAN_WEEK, synthetic_month
from ena.store import Store

MONTH = synthetic_month()
WEDDING = next(event for event in MONTH.events if event.id == BOOKED_EVENT_ID)
NOW = dt.datetime(2026, 11, 16, 9, 0, tzinfo=dt.UTC)


@dataclass
class FakeBank:
    simulated: bool = True
    calls: list[tuple[str, int, str]] = field(default_factory=list)

    def pay(self, action: str, amount_cents: int, ref: str) -> str:
        self.calls.append((action, amount_cents, ref))
        return "CPLT"


@dataclass
class Setup:
    store: Store
    bank: FakeBank
    gate: Gate
    day: ReplayDay
    tools: EnaTools
    advances: Advances


def setup(
    *, simulated: bool = True, limit_cents: int | None = None, day: dt.date = LEAN_WEEK
) -> Setup:
    store = Store(":memory:")
    bank = FakeBank(simulated=simulated)
    business = MONTH.business
    if limit_cents is not None:
        business = business.model_copy(update={"auto_pay_limit_cents": limit_cents})
    gate = Gate(business, store, bank, lambda: NOW)
    today = ReplayDay(day)
    tools = EnaTools(Ledger(MONTH, store, today), store, gate, lambda: NOW)
    return Setup(store, bank, gate, today, tools, Advances(store, gate, MONTH.events, lambda: NOW))


def offer(s: Setup) -> Offer:
    result = s.tools.propose_event_advance(BOOKED_EVENT_ID)
    assert result.offer is not None
    return result.offer


def final_payment(
    amount_cents: int = WEDDING.final_payment_cents, reference: str | None = None
) -> Transaction:
    return Transaction(
        id="syn-tx-final",
        account_id=MONTH.account.id,
        date=dt.date(2026, 12, 28),
        amount_cents=amount_cents,
        counterparty=WEDDING.customer,
        reference=f"Final payment {WEDDING.id}" if reference is None else reference,
    )


def posted(line: Transaction) -> TransactionPosted:
    return TransactionPosted(at=dt.datetime(2026, 12, 28, 6, 0, tzinfo=TZ), transaction=line)


# The owner's accept and decline


def test_the_offer_is_kept_offered_with_its_event() -> None:
    s = setup()
    advance = offer(s)
    stored = s.store.advance(advance.id)
    assert stored is not None
    assert (stored.offer, stored.event_id, stored.status) == (
        advance,
        BOOKED_EVENT_ID,
        AdvanceStatus.OFFERED,
    )


def test_accepting_writes_the_owners_simulated_receipt_and_moves_no_money() -> None:
    s = setup()
    advance = offer(s)
    receipt = s.advances.accept(advance.id)
    assert receipt.result == ACCEPTED
    assert receipt.actor == "owner"
    assert receipt.amount_cents == advance.amount_cents == 280_000
    assert receipt.ref == f"accept-{advance.id}"
    assert "Simulated" in receipt.explanation
    assert "€6,000.00 final payment" in receipt.explanation
    assert s.bank.calls == []
    stored = s.store.accepted_advance_for(BOOKED_EVENT_ID)
    assert stored is not None and stored.offer.id == advance.id


def test_declining_writes_a_declined_receipt_and_takes_nothing() -> None:
    s = setup()
    advance = offer(s)
    receipt = s.advances.decline(advance.id)
    assert receipt.result == DECLINED
    assert s.store.accepted_advance_for(BOOKED_EVENT_ID) is None
    assert s.bank.calls == []


def test_an_offer_is_decided_once() -> None:
    s = setup()
    advance = offer(s)
    s.advances.accept(advance.id)
    with pytest.raises(AdvanceError, match="already accepted"):
        s.advances.accept(advance.id)
    with pytest.raises(AdvanceError, match="already accepted"):
        s.advances.decline(advance.id)


def test_an_event_has_one_accepted_advance() -> None:
    # 2 November's offer (with spare cash) and the lean Monday's (none) are two offers
    s = setup(day=dt.date(2026, 11, 2))
    first = offer(s)
    s.day.observe(DayStarted(at=NOW, day=LEAN_WEEK))
    second = offer(s)
    assert first.id != second.id
    s.advances.accept(first.id)
    with pytest.raises(AdvanceError, match="already has an accepted advance"):
        s.advances.accept(second.id)
    s.advances.decline(second.id)


def test_an_unknown_offer_or_another_kind_is_refused() -> None:
    s = setup()
    with pytest.raises(AdvanceError, match="no event advance offer"):
        s.advances.accept("advance-syn-event-0014-1")
    loan = s.tools.propose_loan().offer
    assert loan is not None
    with pytest.raises(AdvanceError, match="no event advance offer"):
        s.advances.accept(loan.id)


# The repayment from the final payment


def test_the_final_payment_repays_the_accepted_advance_through_the_gate() -> None:
    # €2,800 is above the €500 limit: the repayment waits for the owner's approval
    s = setup()
    advance = offer(s)
    s.advances.accept(advance.id)
    s.advances.observe(posted(final_payment()))
    approval = s.store.pending_approval_for(f"repay-{advance.id}")
    assert isinstance(approval, ApprovalRequest)
    assert approval.amount_cents == 280_000
    assert "Repay the event advance" in approval.action
    assert s.bank.calls == []
    receipt = s.gate.approve(approval.id)
    assert receipt.result == PaymentResult.PAID
    assert receipt.simulated is True
    assert s.bank.calls == [(approval.action, 280_000, f"repay-{advance.id}")]


def test_within_the_limit_the_gate_repays_on_its_own_and_once() -> None:
    s = setup(limit_cents=300_000)
    advance = offer(s)
    s.advances.accept(advance.id)
    first = s.advances.repay_from(final_payment())
    second = s.advances.repay_from(final_payment())
    assert isinstance(first, Receipt) and first.result == PaymentResult.PAID
    assert second == first
    assert len(s.bank.calls) == 1


@pytest.mark.parametrize(
    "line",
    [
        final_payment(amount_cents=WEDDING.final_payment_cents - 1),
        final_payment(amount_cents=-WEDDING.final_payment_cents),
        final_payment(reference="Final payment syn-event-00141"),
        final_payment(reference="Final payment"),
    ],
)
def test_a_line_that_isnt_the_final_payment_repays_nothing(line: Transaction) -> None:
    s = setup(limit_cents=300_000)
    s.advances.accept(offer(s).id)
    assert s.advances.repay_from(line) is None
    assert s.bank.calls == []


def test_without_an_accepted_advance_the_final_payment_repays_nothing() -> None:
    s = setup(limit_cents=300_000)
    s.advances.decline(offer(s).id)
    assert s.advances.repay_from(final_payment()) is None
    assert s.bank.calls == []


def test_a_simulated_advance_is_never_repaid_with_real_money() -> None:
    s = setup(simulated=False, limit_cents=300_000)
    s.advances.accept(offer(s).id)
    with pytest.raises(AdvanceError, match="never repaid with real money"):
        s.advances.repay_from(final_payment())
    assert s.bank.calls == []


def test_the_november_replay_repays_nothing() -> None:
    # The wedding's final payment lands after the month: the replay pays no repayment
    s = setup(limit_cents=300_000)
    s.advances.accept(offer(s).id)
    for event in month_events(MONTH):
        s.advances.observe(event)
    assert s.bank.calls == []
