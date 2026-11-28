"""The event advance after its offer: the owner's accept or decline, and the repayment
through the gate (docs/build-plan.md §6, §7).

The owner takes an advance with a tap on its offer card (`ena/api.py`), never through a
tool: `accept` writes the owner's receipt that the advance is credited, and `decline` the
receipt that it is not. The advance is simulated (there is no lending API), so accepting
it moves no money and the receipt says so. An offer is decided once, and an event has at
most one accepted advance.

The repayment comes out of the event's final payment. `observe` is a replay `Handler`
(`ena/replay.py`), the entry point live events will use too: when a statement line posts
that is the final payment of an event with an accepted advance, it asks the gate to repay
the advance. The final payment is a line of money in, of exactly the event's final
payment, whose reference names the event's id as one of its words (ASSUMED: the customer
quotes the booking).
The gate pays within the owner's limit and otherwise asks for the owner's approval, and
its ref (`repay-<offer id>`) makes a second posting of the line get the same answer back.

A simulated advance is never repaid with real money: when the gate's bank moves money,
the repayment is refused (`AdvanceError` here, and the gate refuses its `repay-` ref too,
also at the owner's approval) and nothing is paid.

Nothing in the app posts statement lines yet: the server serves one day of the synthetic
month and has no statement feed, so `observe` runs in the tests and the replay only. Once
a feed posts the final payment, the repayment's approval card reaches the owner like any
other approval.

Neither the credit nor the repayment is a line on the ledger's statement
(`ena/mcp_server.py`): no money moved for the advance, so the balance and the forecast
don't count either.
"""

from __future__ import annotations

from collections.abc import Sequence

from ena.gate import REPAY_PREFIX, Clock, Gate
from ena.models import (
    Actor,
    AdvanceStatus,
    ApprovalRequest,
    Event,
    OfferKind,
    Receipt,
    Transaction,
)
from ena.replay import ReplayEvent, TransactionPosted
from ena.rules.forecast import euros
from ena.store import Store, StoredAdvance

ACCEPTED = "accepted"
DECLINED = "declined"


class AdvanceError(RuntimeError):
    """A decision or repayment the advances refuse: an unknown or decided offer, a second
    accepted advance for an event, or a repayment that would move real money."""


class Advances:
    def __init__(self, store: Store, gate: Gate, events: Sequence[Event], clock: Clock) -> None:
        self._store = store
        self._gate = gate
        self._events = {event.id: event for event in events}
        self._clock = clock

    def accept(self, offer_id: str) -> Receipt:
        """The owner takes the advance: the receipt says it is credited, simulated."""
        stored = self._offered(offer_id)
        taken = self._store.accepted_advance_for(stored.event_id)
        if taken is not None:
            raise AdvanceError(
                f"the event {stored.event_id} already has an accepted advance {taken.offer.id}"
            )
        event = self._event(stored)
        offer = stored.offer
        receipt = Receipt(
            id=f"receipt-accept-{offer.id}",
            at=self._clock(),
            actor=Actor.OWNER,
            action=f"Accept the event advance for {event.customer} (simulated)",
            amount_cents=offer.amount_cents,
            ref=f"accept-{offer.id}",
            result=ACCEPTED,
            explanation=(
                f"Accepted by the owner: {euros(offer.amount_cents)} for {offer.term_days}"
                f" days, repaid from the {euros(event.final_payment_cents)} final payment"
                " through the gate. Simulated: there is no lending API, no money moved"
            ),
        )
        self._store.record_advance_decision(offer.id, AdvanceStatus.ACCEPTED, receipt)
        return receipt

    def decline(self, offer_id: str) -> Receipt:
        """The owner doesn't take the advance: nothing is credited or repaid."""
        stored = self._offered(offer_id)
        event = self._event(stored)
        offer = stored.offer
        receipt = Receipt(
            id=f"receipt-decline-{offer.id}",
            at=self._clock(),
            actor=Actor.OWNER,
            action=f"Decline the event advance for {event.customer} (simulated)",
            amount_cents=offer.amount_cents,
            ref=f"decline-{offer.id}",
            result=DECLINED,
            explanation="Declined by the owner; nothing was credited",
        )
        self._store.record_advance_decision(offer.id, AdvanceStatus.DECLINED, receipt)
        return receipt

    def observe(self, event: ReplayEvent) -> None:
        """A replay `Handler`: repay an accepted advance when its final payment posts."""
        if isinstance(event, TransactionPosted):
            self.repay_from(event.transaction)

    def repay_from(self, line: Transaction) -> Receipt | ApprovalRequest | None:
        """Repay the accepted advance the line is the final payment of, else None."""
        for event in self._events.values():
            if not _is_final_payment(line, event):
                continue
            stored = self._store.accepted_advance_for(event.id)
            if stored is None:
                return None
            offer = stored.offer
            if offer.simulated and not self._gate.simulated:
                raise AdvanceError(
                    f"the advance {offer.id} is simulated: it is never repaid with real money"
                )
            return self._gate.request_payment(
                f"Repay the event advance for {event.customer} (simulated)",
                offer.amount_cents,
                f"{REPAY_PREFIX}{offer.id}",
            )
        return None

    def _offered(self, offer_id: str) -> StoredAdvance:
        stored = self._store.advance(offer_id)
        if stored is None or stored.offer.kind is not OfferKind.EVENT_ADVANCE:
            raise AdvanceError(f"no event advance offer {offer_id!r}")
        if stored.status is not AdvanceStatus.OFFERED:
            raise AdvanceError(f"the advance {offer_id!r} is already {stored.status}")
        return stored

    def _event(self, stored: StoredAdvance) -> Event:
        event = self._events.get(stored.event_id)
        if event is None:
            raise AdvanceError(f"no event {stored.event_id!r} for the advance {stored.offer.id}")
        return event


def _is_final_payment(line: Transaction, event: Event) -> bool:
    return (
        line.amount_cents > 0
        and line.amount_cents == event.final_payment_cents
        and event.id in line.reference.split()
    )
