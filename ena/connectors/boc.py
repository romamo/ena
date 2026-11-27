"""bocAPI sandbox connector: OAuth, subscription consent, accounts and payments.

The flow follows docs/boc-notes.md and BoC's Postman samples. Two tokens exist: the client
token (app identity) reads accounts and initiates payments; the user token, issued after the
owner's consent in BoC's own browser screen, activates the subscription and executes a payment.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BOC_DATE_FORMAT = "%d/%m/%Y"


class BocError(RuntimeError):
    """bocAPI answered with something other than success."""


class BocSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BOC_", env_file=".env", extra="ignore")

    client_id: str = Field(min_length=1)
    client_secret: SecretStr
    redirect_uri: str = "https://localhost/callback"
    api_base: str = "https://sandbox-apis.bankofcyprus.com/df-boc-org-sb/sb/psd2"
    sign_url: str = "https://sandbox-apis.bankofcyprus.com/df-boc-org-sb/sb/jwssignverifyapi/sign"
    subscription_id: str | None = None

    @field_validator("subscription_id", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return None if value == "" else value


# Value objects


@dataclass(frozen=True, slots=True)
class AccountId:
    value: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9A-Z]{6,34}", self.value):
            raise ValueError(f"not a bank account id: {self.value!r}")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class SubscriptionId:
    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("empty subscription id")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class PaymentId:
    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("empty payment id")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class AuthCode:
    """The one-time `code` BoC appends to the redirect URI after consent."""

    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("empty authorization code")

    @classmethod
    def from_redirect(cls, redirect_url: str, expected_state: str) -> AuthCode:
        """Read the code, checking the `state` we sent in the consent URL came back unchanged."""
        query = parse_qs(urlsplit(redirect_url.strip()).query)
        if "error" in query:
            details = query.get("error_description", [""])[0]
            raise BocError(f"consent refused: {query['error'][0]} {details}".strip())
        codes = query.get("code")
        if not codes:
            raise ValueError(f"no `code` parameter in the redirect URL: {redirect_url!r}")
        states = query.get("state")
        if states != [expected_state]:
            raise BocError(f"redirect state {states} does not match the consent URL's state")
        return cls(codes[0])


@dataclass(frozen=True, slots=True)
class Money:
    cents: int
    currency: str = "EUR"

    def __post_init__(self) -> None:
        if self.cents <= 0:
            raise ValueError(f"amount must be positive, got {self.cents} cents")
        if not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise ValueError(f"not an ISO currency code: {self.currency!r}")

    @classmethod
    def parse(cls, euros: str, currency: str = "EUR") -> Money:
        """Parse an amount such as `30` or `1240.50` into cents, refusing sub-cent values."""
        amount = Decimal(euros)
        cents = amount * 100
        if cents != cents.to_integral_value():
            raise ValueError(f"amount has more than 2 decimals: {euros!r}")
        return cls(int(cents), currency)

    def as_boc_amount(self) -> int | float:
        """bocAPI takes the amount as a JSON number of whole units, like `30`."""
        whole, rest = divmod(self.cents, 100)
        return whole if rest == 0 else self.cents / 100


# Response models: only the fields we use, parsed strictly


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class AccessToken(_Model):
    access_token: SecretStr
    expires_in: int
    scope: str

    def bearer(self) -> str:
        return f"Bearer {self.access_token.get_secret_value()}"


class SelectedAccount(_Model):
    account_id: str = Field(alias="accountId")


class Subscription(_Model):
    subscription_id: str = Field(alias="subscriptionId")
    status: str
    selected_accounts: list[SelectedAccount] = Field(alias="selectedAccounts", default=[])


class Balance(_Model):
    amount: Decimal
    balance_type: str = Field(alias="balanceType")


class Account(_Model):
    account_id: str = Field(alias="accountId")
    account_name: str | None = Field(alias="accountName", default=None)
    iban: str | None = Field(alias="IBAN", default=None)
    currency: str
    balances: list[Balance] = []


class TransactionAmount(_Model):
    amount: Decimal
    currency: str


class Transaction(_Model):
    id: str
    dc_ind: Literal["DEBIT", "CREDIT"] | None = Field(alias="dcInd", default=None)
    transaction_amount: TransactionAmount = Field(alias="transactionAmount")
    description: str | None = None
    posting_date: date = Field(alias="postingDate")
    value_date: date = Field(alias="valueDate")

    @field_validator("posting_date", "value_date", mode="before")
    @classmethod
    def _boc_date(cls, value: object) -> object:
        if isinstance(value, str):
            return datetime.strptime(value, BOC_DATE_FORMAT).date()
        return value


class Statement(_Model):
    transaction: list[Transaction] = []


class Signature(_Model):
    protected: str
    signature: str


class SignedPayment(_Model):
    payload: str
    signatures: list[Signature]


class PaymentRef(_Model):
    payment_id: str = Field(alias="paymentId")


class InitiatedPayment(_Model):
    auth_code_needed: bool = Field(alias="authCodeNeeded")
    payment: PaymentRef


class ExecutedPayment(_Model):
    code: str
    ref_number: str | None = Field(alias="refNumber", default=None)
    description: list[str] | str | None = None

    @property
    def completed(self) -> bool:
        return self.code == "CPLT"


@dataclass(frozen=True, slots=True)
class PaymentOrder:
    debtor: AccountId
    creditor: AccountId
    amount: Money
    details: str
    creditor_bank_id: str = ""
    debtor_bank_id: str = ""

    def to_boc(self) -> dict[str, Any]:
        return {
            "debtor": {"bankId": self.debtor_bank_id, "accountId": str(self.debtor)},
            "creditor": {"bankId": self.creditor_bank_id, "accountId": str(self.creditor)},
            "transactionAmount": {
                "amount": self.amount.as_boc_amount(),
                "currency": self.amount.currency,
            },
            "paymentDetails": self.details,
        }


class Recorder:
    """Saves raw bocAPI responses to `fixtures/boc/` so others can build against them."""

    SECRET_KEYS = frozenset({"access_token", "refresh_token", "id_token"})

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def save(self, name: str, body: Any) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"{name}.json"
        path.write_text(json.dumps(self._redact(body), indent=2) + "\n", encoding="utf-8")
        return path

    def _redact(self, body: Any) -> Any:
        if isinstance(body, dict):
            return {
                key: "REDACTED" if key in self.SECRET_KEYS else self._redact(value)
                for key, value in body.items()
            }
        if isinstance(body, list):
            return [self._redact(item) for item in body]
        return body


class BocClient:
    def __init__(
        self,
        settings: BocSettings,
        http: httpx.Client,
        recorder: Recorder | None = None,
    ) -> None:
        self.settings = settings
        self.http = http
        self.recorder = recorder

    # OAuth

    def client_token(self) -> AccessToken:
        return AccessToken.model_validate(
            self._token({"grant_type": "client_credentials", "scope": "TPPOAuth2Security"})
        )

    def user_token(self, code: AuthCode) -> AccessToken:
        return AccessToken.model_validate(
            self._token(
                {
                    "grant_type": "authorization_code",
                    "scope": "UserOAuth2Security",
                    "code": code.value,
                }
            )
        )

    def subscription_consent_url(self, subscription_id: SubscriptionId, state: str) -> str:
        return self._authorize_url(state, {"subscriptionid": str(subscription_id)})

    def payment_consent_url(self, payment_id: PaymentId, state: str) -> str:
        return self._authorize_url(state, {"paymentid": str(payment_id)})

    # Subscription (account and payment consent)

    def create_subscription(self, token: AccessToken) -> Subscription:
        body = {
            "accounts": {
                "transactionHistory": True,
                "balance": True,
                "details": True,
                "checkFundsAvailability": True,
            },
            # Values from BoC's own sample; their meaning is undocumented (boc-notes §8)
            "payments": {"limit": 99999999, "currency": "EUR", "amount": 999999999},
        }
        data = self._call(
            "POST",
            "/v1/subscriptions",
            token,
            "subscription_create",
            json_body=body,
            headers={"originUserId": "999999"},
        )
        return Subscription.model_validate(data)

    def get_subscription(self, token: AccessToken, subscription_id: SubscriptionId) -> Any:
        return self._call("GET", f"/v1/subscriptions/{subscription_id}", token, "subscription_get")

    def activate_subscription(
        self,
        user_token: AccessToken,
        current: dict[str, Any],
        accounts: list[AccountId],
    ) -> Subscription:
        if not accounts:
            raise ValueError("pick at least one account to activate the subscription")
        body = {
            **current,
            "status": "INPROGRESS",
            "selectedAccounts": [{"accountId": str(account)} for account in accounts],
        }
        subscription_id = current["subscriptionId"]
        data = self._call(
            "PATCH",
            f"/v1/subscriptions/{subscription_id}",
            user_token,
            "subscription_activate",
            json_body=body,
        )
        return Subscription.model_validate(data)

    # Accounts

    def accounts(self, token: AccessToken, subscription_id: SubscriptionId) -> list[Account]:
        data = self._call("GET", "/v1/accounts", token, "accounts", subscription_id=subscription_id)
        return [Account.model_validate(item) for item in _as_list(data)]

    def balance(
        self, token: AccessToken, subscription_id: SubscriptionId, account: AccountId
    ) -> Account:
        data = self._call(
            "GET",
            f"/v1/accounts/{account}/balance",
            token,
            "balance",
            subscription_id=subscription_id,
        )
        items = _as_list(data)
        if len(items) != 1:
            raise BocError(f"expected one account in the balance response, got {len(items)}")
        return Account.model_validate(items[0])

    def statement(
        self,
        token: AccessToken,
        subscription_id: SubscriptionId,
        account: AccountId,
        start: date,
        end: date,
    ) -> list[Transaction]:
        if start > end:
            raise ValueError(f"start {start} is after end {end}")
        query = {
            "startDate": start.strftime(BOC_DATE_FORMAT),
            "endDate": end.strftime(BOC_DATE_FORMAT),
            "maxCount": "0",
        }
        data = self._call(
            "GET",
            f"/v1/accounts/{account}/statement",
            token,
            "statement",
            subscription_id=subscription_id,
            params=query,
        )
        return Statement.model_validate(data).transaction

    # Payments

    def sign_payment(self, order: PaymentOrder) -> SignedPayment:
        response = self.http.post(
            self.settings.sign_url,
            json=order.to_boc(),
            headers={"tppId": "singpaymentdata", "env": "Sandbox"},
        )
        return SignedPayment.model_validate(self._json(response, "payment_sign"))

    def initiate_payment(self, token: AccessToken, signed: SignedPayment) -> InitiatedPayment:
        now = str(int(time.time()))
        data = self._call(
            "POST",
            "/v1/payments/initiate",
            token,
            "payment_initiate",
            json_body=signed.model_dump(),
            headers={
                "lang": "en",
                "loginTimeStamp": now,
                "customerDevice": str(uuid.uuid4()),
                "customerIP": "127.0.0.1",
                "customerSessionId": str(uuid.uuid4()),
            },
        )
        return InitiatedPayment.model_validate(data)

    def execute_payment(self, user_token: AccessToken, payment_id: PaymentId) -> ExecutedPayment:
        data = self._call(
            "POST",
            f"/v1/payments/{payment_id}/execute",
            user_token,
            "payment_execute",
            json_body={},
        )
        return ExecutedPayment.model_validate(data)

    def payment_status(self, token: AccessToken, payment_id: PaymentId) -> Any:
        return self._call("GET", f"/v1/payments/{payment_id}/status", token, "payment_status")

    # Plumbing

    def _token(self, form: dict[str, str]) -> Any:
        response = self.http.post(
            f"{self.settings.api_base}/oauth2/token",
            data={
                "client_id": self.settings.client_id,
                "client_secret": self.settings.client_secret.get_secret_value(),
                **form,
            },
        )
        return self._json(response, f"token_{form['grant_type']}")

    def _authorize_url(self, state: str, extra: dict[str, str]) -> str:
        # OAuth CSRF protection; BoC echoes it back on the redirect
        if not state:
            raise ValueError("the consent URL needs a non-empty state")
        query = {
            "state": state,
            "response_type": "code",
            "redirect_uri": self.settings.redirect_uri,
            "scope": "UserOAuth2Security",
            "client_id": self.settings.client_id,
            **extra,
        }
        return f"{self.settings.api_base}/oauth2/authorize?{urlencode(query)}"

    def _call(
        self,
        method: str,
        path: str,
        token: AccessToken,
        record_as: str,
        *,
        json_body: Any = None,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        subscription_id: SubscriptionId | None = None,
    ) -> Any:
        all_headers = {
            "Authorization": token.bearer(),
            "journeyId": str(uuid.uuid4()),
            "timeStamp": str(int(time.time())),
            **(headers or {}),
        }
        if subscription_id is not None:
            all_headers["subscriptionId"] = str(subscription_id)
        response = self.http.request(
            method,
            f"{self.settings.api_base}{path}",
            json=json_body,
            params=params,
            headers=all_headers,
        )
        return self._json(response, record_as)

    def _json(self, response: httpx.Response, record_as: str) -> Any:
        if response.is_error:
            raise BocError(
                f"{response.request.method} {response.request.url.path} -> "
                f"{response.status_code}: {response.text[:500]}"
            )
        data = response.json()
        if self.recorder is not None:
            self.recorder.save(record_as, data)
        return data


def _as_list(data: Any) -> list[Any]:
    if not isinstance(data, list):
        raise BocError(f"expected a JSON array, got {type(data).__name__}")
    return data
