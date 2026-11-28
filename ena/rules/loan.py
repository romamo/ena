"""The Digital Business Loan pre-fill, simulated (docs/build-plan.md §7).

A pure function, no I/O. The gap is how far the lean week's closing falls short of the
threshold:

    gap = forecast threshold (instalment + buffer) - the lean week's closing

which is at least 1 cent whenever a week is lean. The reserve covers the gap when the
reserve rule makes an offer and the amount it sets aside (the instalment) is at least the
gap. When it doesn't (no reserve offer, or a gap above the instalment), the loan offer is:

- amount = the gap rounded up to the next €500 (`LOAN_STEP_CENTS`), at most €20,000
  (`LOAN_CAP_CENTS`): a gap above the cap is offered the cap
- term = 730 days (`LOAN_TERM_DAYS`), the 2-year maximum, always
- `simulated=True`: a pre-fill to show the owner, never a promise or an application

With no lean week, or a gap the reserve covers, it returns None.

The offer's id names the lean week and the amount (`loan-<YYYYMMDD>-<cents>`): a loan of
another amount is another offer.

The existing loan never changes (§3 rule 4): this offers a new loan and nothing here defers
or alters an instalment.
"""

from __future__ import annotations

from ena.models import Offer, OfferKind
from ena.rules.forecast import CashForecast
from ena.rules.reserve import reserve_offer

LOAN_STEP_CENTS = 50_000
"""The loan amount is a multiple of €500."""

LOAN_CAP_CENTS = 2_000_000
"""The Digital Business Loan's maximum, €20,000."""

LOAN_TERM_DAYS = 730
"""The term offered: 2 years, the product's maximum."""


def loan_offer(cash: CashForecast) -> Offer | None:
    """Pre-fill a simulated loan for the gap the reserve can't cover, else None."""
    lean = cash.lean
    if lean is None:
        return None
    gap = cash.threshold_cents - lean.closing_cents
    reserve = reserve_offer(cash)
    reserve_cents = 0 if reserve is None else reserve.amount_cents
    if reserve is not None and reserve_cents >= gap:
        return None
    rounded = -(-gap // LOAN_STEP_CENTS) * LOAN_STEP_CENTS
    amount = min(rounded, LOAN_CAP_CENTS)
    return Offer(
        id=f"loan-{lean.week_start:%Y%m%d}-{amount}",
        kind=OfferKind.LOAN,
        amount_cents=amount,
        term_days=LOAN_TERM_DAYS,
        basis=frozendict(
            gap_cents=gap,
            rounded_gap_cents=rounded,
            cap_cents=LOAN_CAP_CENTS,
            step_cents=LOAN_STEP_CENTS,
            reserve_cents=reserve_cents,
            lean_week_closing_cents=lean.closing_cents,
            threshold_cents=cash.threshold_cents,
            instalment_cents=cash.instalment_cents,
            buffer_cents=cash.buffer_cents,
            term_days=LOAN_TERM_DAYS,
        ),
        simulated=True,
    )
