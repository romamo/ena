"""The reserve for the loan instalment in a lean month (docs/build-plan.md §7).

A pure function, no I/O. When the forecast has a lean week that starts on or before the
next instalment's due date (the lean week comes before the instalment, or is its week), it
offers to move the instalment amount to a reserve now. Otherwise there is nothing to
reserve for, and it returns None.

The offer is real money, not simulated (`simulated=False`): the move goes through the gate
as a payment later, not here. Its term is the days from the forecast's first day to the
due date, both counted (`(due - start).days + 1`, so at least 1).

The loan never changes (§3 rule 4): the reserve is the owner's own money set aside, and
nothing here defers or alters the instalment.
"""

from __future__ import annotations

from ena.models import Offer, OfferKind
from ena.rules.forecast import CashForecast


def reserve_offer(cash: CashForecast) -> Offer | None:
    """Offer to reserve the instalment now when a lean week comes before it, else None."""
    lean = cash.lean
    if lean is None or lean.week_start > cash.instalment_due:
        return None
    term_days = (cash.instalment_due - cash.start).days + 1
    return Offer(
        id=f"reserve-{cash.instalment_due:%Y%m%d}",
        kind=OfferKind.RESERVE,
        amount_cents=cash.instalment_cents,
        term_days=term_days,
        basis=frozendict(
            instalment_cents=cash.instalment_cents,
            lean_week_closing_cents=lean.closing_cents,
            buffer_cents=cash.buffer_cents,
            threshold_cents=cash.threshold_cents,
            days_to_instalment=term_days,
        ),
        simulated=False,
    )
