"""The owner's dashboard: what the chat's cards said, kept on one screen.

Read only. Every figure is computed here from the ledger, the store and the rules, the
same code the agent's tools call, so the page only displays it (Q-2). Nothing here pays,
approves or writes a receipt:

- It reads the ledger and the store directly, not through `EnaTools`: a tool adds its
  card to the chat turn in progress (`Turn.cards`), and `propose_loan` writes an
  "offered" receipt, so a page load through the tools would change the chat
- A pending approval is shown so the owner can find it; deciding it stays the owner's tap
  on the approval card (`POST /approvals/{id}/...`, `ena/api.py`)
- `credit` answers "do I need a loan?": when the forecast's lean week closes below the
  threshold, the reserve rule (the owner's own money) or the loan rule (a new loan,
  simulated) covers the gap. The existing loan is never changed (§3 rule 4)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from ena.gate import PaymentResult
from ena.mcp_server import Ledger
from ena.models import Actor, ApprovalRequest, Forecast, InvoiceStatus, IsoDate, Offer, Receipt
from ena.rules.loan import loan_offer
from ena.rules.reserve import reserve_offer
from ena.store import Store


class _View(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Overview(_View):
    """The month on one screen, as of `as_of`."""

    as_of: IsoDate
    business: str
    synthetic: bool
    limit_cents: int
    """The owner's auto-pay limit: shown, never changed here."""
    balance_cents: int
    pending: tuple[ApprovalRequest, ...]
    """Payments waiting for the owner's tap, oldest first."""
    paid_on_its_own: int
    """Payments Ena made within the limit."""
    paid_on_its_own_cents: int
    stepped_in: int
    """The owner's decisions: each approval or decline."""
    paid_out_cents: int
    """Every payment the gate made, by Ena or after approval."""
    unpaid_invoices: int
    unpaid_cents: int
    lean_week: IsoDate | None
    lean_week_closing_cents: int | None
    """The lean week's closing balance, or None when no week is lean."""


class Receipts(_View):
    receipts: tuple[Receipt, ...]
    """Newest first."""


class ForecastView(_View):
    """The forecast card's data, with the safe line the chart draws."""

    forecast: Forecast
    threshold_cents: int
    """The instalment plus a week of outflows: a week closing below it is lean."""


Verdict = Literal["no_lean_week", "reserve_covers", "loan_needed"]
"""Whether the forecast needs money: no lean week, a gap the owner's reserve covers, or a
gap only a loan covers. The loan and reserve rules decide; `credit` only names it."""


class Credit(_View):
    """Does the forecast need a loan? The lean week, its gap, and the rules' offers."""

    verdict: Verdict
    lean_week: IsoDate | None
    lean_week_closing_cents: int | None
    threshold_cents: int
    """The instalment plus a week of outflows: a week closing below it is lean."""
    gap_cents: int | None
    """How far the lean week closes below the threshold, or None with no lean week."""
    instalment_cents: int
    instalment_due: IsoDate
    reserve: Offer | None
    """Set money aside for the instalment, or None when no lean week comes before it."""
    loan: Offer | None
    """The Digital Business Loan pre-fill (simulated), or None when the reserve covers the gap."""


class Dashboard:
    def __init__(self, ledger: Ledger, store: Store) -> None:
        self._ledger = ledger
        self._store = store

    def overview(self) -> Overview:
        business = self._ledger.month.business
        receipts = self._store.receipts()
        paid = [r for r in receipts if r.result == PaymentResult.PAID]
        by_ena = [r for r in paid if r.actor is Actor.ENA]
        unpaid = [i for i in self._ledger.invoices() if i.status is not InvoiceStatus.PAID]
        forecast = self._ledger.cash_forecast().forecast
        lean = next((w for w in forecast.weeks if w.week_start == forecast.lean_week), None)
        return Overview(
            as_of=self._ledger.today(),
            business=business.name,
            synthetic=business.synthetic,
            limit_cents=business.auto_pay_limit_cents,
            balance_cents=self._ledger.balance_cents(),
            pending=tuple(self._store.pending_approvals()),
            paid_on_its_own=len(by_ena),
            paid_on_its_own_cents=sum(r.amount_cents for r in by_ena),
            stepped_in=sum(1 for r in receipts if r.actor is Actor.OWNER),
            paid_out_cents=sum(r.amount_cents for r in paid),
            unpaid_invoices=len(unpaid),
            unpaid_cents=sum(i.amount_cents for i in unpaid),
            lean_week=forecast.lean_week,
            lean_week_closing_cents=None if lean is None else lean.closing_cents,
        )

    def receipts(self) -> Receipts:
        return Receipts(receipts=tuple(reversed(self._store.receipts())))

    def forecast(self) -> ForecastView:
        cash = self._ledger.cash_forecast()
        return ForecastView(forecast=cash.forecast, threshold_cents=cash.threshold_cents)

    def credit(self) -> Credit:
        cash = self._ledger.cash_forecast()
        lean = cash.lean
        loan = loan_offer(cash)
        verdict: Verdict
        if lean is None:
            verdict = "no_lean_week"
        elif loan is None:
            verdict = "reserve_covers"
        else:
            verdict = "loan_needed"
        return Credit(
            verdict=verdict,
            lean_week=None if lean is None else lean.week_start,
            lean_week_closing_cents=None if lean is None else lean.closing_cents,
            threshold_cents=cash.threshold_cents,
            gap_cents=None if lean is None else cash.threshold_cents - lean.closing_cents,
            instalment_cents=cash.instalment_cents,
            instalment_due=cash.instalment_due,
            reserve=reserve_offer(cash),
            loan=loan,
        )
