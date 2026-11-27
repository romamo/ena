"""`ena-boc`: walk the bocAPI sandbox by hand, from consent to a payment that reaches CPLT.

    uv run ena-boc token                       # are the app credentials right?
    uv run ena-boc subscribe                   # prints the consent URL
    uv run ena-boc activate '<redirect URL>'   # prints BOC_SUBSCRIPTION_ID for .env
    uv run ena-boc accounts
    uv run ena-boc statement 351012345671 --start 2026-01-01 --end 2026-11-27
    uv run ena-boc pay --debtor 351012345671 --amount 30   # prints the payment consent URL
    uv run ena-boc execute '<redirect URL>'    # CPLT

After each consent the browser lands on the redirect URI (https://localhost/callback), which
does not load: copy the whole URL from the address bar, it carries the `code`.
Add `--record` to save each raw response to fixtures/boc/ (tokens redacted).
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from ena.connectors.boc import (
    AccountId,
    AuthCode,
    BocClient,
    BocError,
    BocSettings,
    Money,
    PaymentId,
    PaymentOrder,
    Recorder,
    SubscriptionId,
)

STATE_FILE = Path("tmp/boc-state.json")
FIXTURES = Path("fixtures/boc")

# BoC's documented sample payee (a SWIFT transfer that the sandbox accepts)
SAMPLE_CREDITOR = "48193222324233"
SAMPLE_CREDITOR_BANK = "CITIUS33"


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level={0: logging.WARNING, 1: logging.INFO}.get(args.verbose, logging.DEBUG),
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        settings = BocSettings()  # filled from the environment and .env
    except ValidationError as error:
        missing = ", ".join(f"BOC_{'_'.join(map(str, e['loc'])).upper()}" for e in error.errors())
        raise SystemExit(
            f"bocAPI settings invalid or missing: {missing}. Set them in .env in {Path.cwd()} "
            "(see .env.example)"
        ) from error
    recorder = Recorder(FIXTURES) if args.record else None
    with httpx.Client(timeout=30) as http:
        client = BocClient(settings, http, recorder)
        try:
            args.handler(client, args)
        except BocError as error:
            raise SystemExit(f"bocAPI error: {error}") from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ena-boc", description=__doc__.split("\n")[0])
    parser.add_argument("-v", "--verbose", action="count", default=0, help="-v INFO, -vv DEBUG")
    parser.add_argument("--record", action="store_true", help="save raw responses as fixtures")
    commands = parser.add_subparsers(required=True, metavar="command")

    commands.add_parser("token", help="get a client token").set_defaults(handler=_token)
    commands.add_parser("subscribe", help="request account consent").set_defaults(
        handler=_subscribe
    )

    activate = commands.add_parser("activate", help="finish account consent")
    activate.add_argument("redirect_url")
    activate.set_defaults(handler=_activate)

    commands.add_parser("accounts", help="list accounts and balances").set_defaults(
        handler=_accounts
    )

    statement = commands.add_parser("statement", help="list transactions")
    statement.add_argument("account", type=AccountId)
    statement.add_argument("--start", type=date.fromisoformat, required=True)
    statement.add_argument("--end", type=date.fromisoformat, required=True)
    statement.set_defaults(handler=_statement)

    pay = commands.add_parser("pay", help="sign and initiate a payment")
    pay.add_argument("--debtor", type=AccountId, required=True)
    pay.add_argument("--amount", type=Money.parse, required=True, help="euros, e.g. 30 or 12.50")
    pay.add_argument("--creditor", type=AccountId, default=AccountId(SAMPLE_CREDITOR))
    pay.add_argument("--creditor-bank", default=SAMPLE_CREDITOR_BANK, help="BIC, empty for BoC")
    pay.add_argument("--details", default="Ena sandbox payment")
    pay.set_defaults(handler=_pay)

    execute = commands.add_parser("execute", help="execute the payment after its consent")
    execute.add_argument("redirect_url")
    execute.set_defaults(handler=_execute)

    return parser


def _token(client: BocClient, _: argparse.Namespace) -> None:
    token = client.client_token()
    print(f"Client token OK: scope {token.scope}, expires in {token.expires_in} s")


def _subscribe(client: BocClient, _: argparse.Namespace) -> None:
    subscription = client.create_subscription(client.client_token())
    _save_state(subscription_id=subscription.subscription_id)
    print(f"Subscription {subscription.subscription_id} is {subscription.status}.")
    print("Open this URL, log in as 999999 / 112233, pick the accounts and consent:\n")
    print(client.subscription_consent_url(SubscriptionId(subscription.subscription_id)))
    print("\nThen run: uv run ena-boc activate '<the URL you landed on>'")


def _activate(client: BocClient, args: argparse.Namespace) -> None:
    code = AuthCode.from_redirect(args.redirect_url)
    user_token = client.user_token(code)  # exchange at once: the code is short-lived
    subscription_id = SubscriptionId(_require_state("subscription_id"))
    current = _one(client.get_subscription(client.client_token(), subscription_id))
    picked = [AccountId(item["accountId"]) for item in current.get("selectedAccounts", [])]
    if not picked:
        raise SystemExit(
            "The subscription lists no selected accounts after consent; "
            f"raw response: {json.dumps(current)}"
        )
    subscription = client.activate_subscription(user_token, current, picked)
    print(f"Subscription {subscription.subscription_id} is {subscription.status}.")
    print("Accounts: " + ", ".join(item.account_id for item in subscription.selected_accounts))
    print(f"\nAdd to .env:\nBOC_SUBSCRIPTION_ID={subscription.subscription_id}")


def _accounts(client: BocClient, _: argparse.Namespace) -> None:
    token = client.client_token()
    subscription_id = _subscription_id(client)
    for account in client.accounts(token, subscription_id):
        detail = client.balance(token, subscription_id, AccountId(account.account_id))
        balances = ", ".join(f"{b.balance_type} {b.amount}" for b in detail.balances)
        print(f"{account.account_id}  {account.iban or '-'}  {account.currency}  {balances}")


def _statement(client: BocClient, args: argparse.Namespace) -> None:
    rows = client.statement(
        client.client_token(), _subscription_id(client), args.account, args.start, args.end
    )
    for row in rows:
        sign = "-" if row.dc_ind == "DEBIT" else "+" if row.dc_ind == "CREDIT" else "?"
        amount = row.transaction_amount
        print(f"{row.posting_date}  {sign}{amount.amount} {amount.currency}  {row.description}")
    print(f"{len(rows)} transactions")


def _pay(client: BocClient, args: argparse.Namespace) -> None:
    order = PaymentOrder(
        debtor=args.debtor,
        creditor=args.creditor,
        creditor_bank_id=args.creditor_bank,
        amount=args.amount,
        details=args.details,
    )
    signed = client.sign_payment(order)
    initiated = client.initiate_payment(client.client_token(), signed)
    payment_id = initiated.payment.payment_id
    _save_state(payment_id=payment_id)
    print(f"Payment {payment_id} initiated, consent needed: {initiated.auth_code_needed}.")
    print("Open this URL, log in as 999999 / 112233 and approve the payment:\n")
    print(client.payment_consent_url(PaymentId(payment_id)))
    print("\nThen run: uv run ena-boc execute '<the URL you landed on>'")


def _execute(client: BocClient, args: argparse.Namespace) -> None:
    code = AuthCode.from_redirect(args.redirect_url)
    user_token = client.user_token(code)
    payment_id = PaymentId(_require_state("payment_id"))
    result = client.execute_payment(user_token, payment_id)
    print(f"Payment {payment_id}: {result.code}, ref {result.ref_number}")
    if not result.completed:
        raise SystemExit(f"Payment did not complete: {result.description}")


def _subscription_id(client: BocClient) -> SubscriptionId:
    if client.settings.subscription_id is not None:
        return SubscriptionId(client.settings.subscription_id)
    return SubscriptionId(_require_state("subscription_id"))


def _one(data: Any) -> dict[str, Any]:
    """Single-item endpoints sometimes answer with an array of one (boc-notes §4)."""
    if isinstance(data, list):
        if len(data) != 1:
            raise BocError(f"expected one item, got {len(data)}")
        data = data[0]
    if not isinstance(data, dict):
        raise BocError(f"expected a JSON object, got {type(data).__name__}")
    return data


def _load_state() -> dict[str, str]:
    if not STATE_FILE.exists():
        return {}
    state: dict[str, str] = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return state


def _save_state(**values: str) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state = {**_load_state(), **values}
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _require_state(key: str) -> str:
    state = _load_state()
    if key not in state:
        step = "subscribe" if key == "subscription_id" else "pay"
        raise SystemExit(f"No {key} in {STATE_FILE}; run `uv run ena-boc {step}` first")
    return state[key]


if __name__ == "__main__":
    main()
