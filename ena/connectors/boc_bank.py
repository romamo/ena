"""The gate's bank on the bocAPI sandbox, and the live account it reads (`ena-serve --bank boc`).

`BocBank` is a `ConsentBank` (ena/gate.py): `initiate` signs and initiates the payment
with a client token and returns BoC's consent URL; the owner confirms it in BoC's own
screen (test login `999999` / `112233`, OTP `123456`), and the browser comes back to
`BOC_REDIRECT_URI` with a one-time code; `execute` exchanges that code for a user token
and executes the payment, `CPLT` when completed (docs/boc-notes.md §3).

Every payment goes from `BOC_ACCOUNT_ID` to the sample payee (`BOC_CREDITOR_ACCOUNT`): the
synthetic suppliers have no sandbox account, so the invoice's own IBAN is never sent.

`account` reads the same account's balance and its statement since `STATEMENT_SINCE`: a
sample customer's account, shown beside the synthetic month, never mixed into it.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from decimal import Decimal
from typing import Literal

from ena.connectors.boc import (
    Account,
    AccountId,
    AuthCode,
    BocClient,
    BocError,
    BocSettings,
    Money,
    PaymentId,
    PaymentOrder,
    SubscriptionId,
    Transaction,
)
from ena.gate import Executed, Initiated
from ena.models import BankAccount, BankLine

STATEMENT_SINCE = dt.date(2020, 1, 1)
"""The sandbox's sample statement lines are dated February 2020 (read 2026-11-28): a
window of recent days finds none."""
MAX_LINES = 10
"""The most statement lines the account card shows, newest first."""

CONSENT_PATH = "/boc/consent"
"""Ena's route the owner's browser comes back to after confirming a payment in BoC."""


class BocBank:
    """Pays through bocAPI, one owner confirmation in BoC's screen per payment."""

    simulated = False
    """The sandbox executes the payment (test money): receipts say sandbox, not simulated."""

    def __init__(
        self,
        client: BocClient,
        subscription_id: SubscriptionId,
        debtor: AccountId,
        clock: Callable[[], dt.datetime],
    ) -> None:
        self._client = client
        self._subscription = subscription_id
        self._debtor = debtor
        self._creditor = AccountId(client.settings.creditor_account)
        self._creditor_bank = client.settings.creditor_bank
        self._clock = clock

    @classmethod
    def from_settings(cls, client: BocClient, clock: Callable[[], dt.datetime]) -> BocBank:
        """The bank from `BOC_*`; refuses settings that could not complete a payment."""
        settings = client.settings
        subscription, account = settings.subscription_id, settings.account_id
        if subscription is None or account is None:
            missing = [
                name
                for name, value in (
                    ("BOC_SUBSCRIPTION_ID", subscription),
                    ("BOC_ACCOUNT_ID", account),
                )
                if value is None
            ]
            raise ValueError(f"set {' and '.join(missing)} in .env (uv run ena-boc activate)")
        check_redirect(settings)
        return cls(client, SubscriptionId(subscription), AccountId(account), clock)

    def initiate(self, action: str, amount_cents: int, ref: str) -> Initiated:
        order = PaymentOrder(
            debtor=self._debtor,
            creditor=self._creditor,
            creditor_bank_id=self._creditor_bank,
            amount=Money(amount_cents),
            details=f"Ena {ref}",
        )
        signed = self._client.sign_payment(order)
        initiated = self._client.initiate_payment(self._client.client_token(), signed)
        if not initiated.auth_code_needed:
            raise BocError(
                f"payment {initiated.payment.payment_id} needs no consent: Ena only knows the"
                " consented flow"
            )
        payment_id = PaymentId(initiated.payment.payment_id)
        return Initiated(str(payment_id), self._client.payment_consent_url(payment_id))

    def execute(self, payment_id: str, code: str) -> Executed:
        user_token = self._client.user_token(AuthCode(code))  # the code is short-lived
        result = self._client.execute_payment(user_token, PaymentId(payment_id))
        return Executed(result.code, result.ref_number)

    def account(self) -> BankAccount:
        token = self._client.client_token()
        detail = self._client.balance(token, self._subscription, self._debtor)
        rows = self._client.statement(
            token, self._subscription, self._debtor, STATEMENT_SINCE, self._clock().date()
        )
        newest = sorted(rows, key=lambda row: (row.posting_date, row.id), reverse=True)
        return BankAccount(
            account_id=detail.account_id,
            iban=detail.iban,
            currency=detail.currency,
            available_cents=_balance(detail, "AVAILABLE"),
            current_cents=_balance(detail, "CURRENT"),
            read_at=self._clock(),
            lines=tuple(_line(row) for row in newest[:MAX_LINES]),
        )


def check_redirect(settings: BocSettings) -> None:
    """Refuse a redirect URI that does not come back to Ena's consent route: the
    confirmation would land on a page that never completes the payment."""
    if not settings.redirect_uri.rstrip("/").endswith(CONSENT_PATH):
        raise ValueError(
            f"BOC_REDIRECT_URI is {settings.redirect_uri!r}: point it at the chat page's"
            f" {CONSENT_PATH}, e.g. http://localhost:5173{CONSENT_PATH}, here and in the"
            " app's redirect URI in the sandbox portal"
        )


def _balance(account: Account, kind: Literal["AVAILABLE", "CURRENT"]) -> int | None:
    amounts = [b.amount for b in account.balances if b.balance_type.upper() == kind]
    if len(amounts) > 1:
        raise BocError(f"the balance response has {len(amounts)} {kind} balances")
    return None if not amounts else _cents(amounts[0])


def _line(row: Transaction) -> BankLine:
    direction: Literal["in", "out"] | None = {"CREDIT": "in", "DEBIT": "out", None: None}[
        row.dc_ind
    ]
    return BankLine(
        id=row.id,
        date=row.posting_date,
        amount_cents=_cents(abs(row.transaction_amount.amount)),
        direction=direction,
        description=row.description or "",
    )


def _cents(amount: Decimal) -> int:
    cents = amount * 100
    if cents != cents.to_integral_value():
        raise BocError(f"an amount with more than 2 decimals: {amount}")
    return int(cents)
