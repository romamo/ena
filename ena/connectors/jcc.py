"""JCC payment gateway connector: register a pay link, verify a callback, confirm a deposit.

The registration, the callback, its checksum and the order-status response follow
docs/jcc-notes.md §0, §2, §3 and §4, and the requests saved in `fixtures/jcc/`.

- `register.do` creates a one-phase order and answers its `formUrl`, the hosted payment
  page: a one-off pay link. Its `returnUrl` is mandatory and absolute; `dynamicCallbackUrl`
  is sent only when set, since the sandbox's callback came from the portal's Static webhook
  (§0). The link stays payable for `link_seconds` (`sessionTimeoutSecs`)

- The callback is a form-encoded POST: `mdOrder`, `orderNumber`, `operation`, `status` and
  `checksum`, plus any extra fields configured in the Merchant Portal
- Its symmetric checksum is HMAC-SHA256, keyed with the webhook's signing secret, over every
  field but `checksum` and `sign_alias`, sorted by name, each as `name;value;`; uppercase hex
- `getOrderStatusExtended.do` confirms the payment: `orderStatus` 2 is authorized and
  captured, and `paymentAmountInfo.depositedAmount` is what was taken, in cents

Mapping a confirmed deposit onto `CardSale`:

- `jcc_order_id` is the gateway's order id (`mdOrder`, the `orderId` from `register.do`)
- `id` is `jcc-<mdOrder>`, one sale per gateway order
- `amount_cents` is the deposited amount; it must equal the order's amount, in EUR
- `date` is the deposit's day in Cyprus (`depositedDate` is epoch milliseconds)
- `status` is the callback's operation, `deposited`
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ena.models import CardSale

CYPRUS = ZoneInfo("Europe/Nicosia")
EUR = "978"
"""ISO 4217 numeric code for the euro, as JCC sends `currency`."""
PAID = 2
"""`orderStatus` of an order authorized and captured (docs/jcc-notes.md §4)."""
UNSIGNED_FIELDS = frozenset({"checksum", "sign_alias"})
TEST_HOST = "gateway-test.jcc.com.cy"
MAX_DESCRIPTION = 598
"""The longest `description` `register.do` takes (docs/jcc-notes.md §2)."""


class JccError(RuntimeError):
    """JCC answered with something other than success."""


class JccUnavailable(JccError):
    """JCC could not be reached, or failed on its side (5xx)."""


class ChecksumError(ValueError):
    """A callback's checksum is missing, malformed or does not match."""


class DepositMismatch(ValueError):
    """The order status does not confirm the deposit the callback announced."""


class JccSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JCC_", env_file=".env", extra="ignore")

    base_url: str = "https://gateway-test.jcc.com.cy/payment/rest/"
    api_user: str = Field(min_length=1)
    api_password: SecretStr
    callback_secret: SecretStr
    """The webhook's symmetric signing secret (Settings > Merchant > Webhooks)."""
    return_url: AnyHttpUrl | None = None
    """Where the payer's browser goes after paying; without it Ena creates no pay link."""
    callback_url: AnyHttpUrl | None = None
    """Sent as `dynamicCallbackUrl` when set; the portal's Static webhook works without it."""
    link_seconds: int = Field(default=3_600, gt=0)
    """How long a pay link stays payable (`sessionTimeoutSecs`; JCC's default is 1,200)."""

    @field_validator("callback_secret", "api_password")
    @classmethod
    def _not_empty(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("must not be empty")
        return value


# Value objects


@dataclass(frozen=True, slots=True)
class OrderId:
    """The gateway's order id: `mdOrder` in a callback, `orderId` in the API."""

    value: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", self.value):
            raise ValueError(f"not a JCC order id: {self.value!r}")

    def __str__(self) -> str:
        return self.value


# Checksum


def checksum(fields: Mapping[str, str], secret: SecretStr) -> str:
    """The symmetric checksum JCC sends for these callback fields, uppercase hex."""
    signed = "".join(
        f"{name};{value};" for name, value in sorted(fields.items()) if name not in UNSIGNED_FIELDS
    )
    key = secret.get_secret_value().encode("utf-8")
    return hmac.new(key, signed.encode("utf-8"), hashlib.sha256).hexdigest().upper()


def verify_checksum(fields: Mapping[str, str], secret: SecretStr) -> None:
    """Raise `ChecksumError` unless the fields carry the checksum the secret gives them."""
    received = fields.get("checksum")
    if received is None:
        raise ChecksumError("the callback carries no checksum")
    if not re.fullmatch(r"[0-9A-Fa-f]{64}", received):
        raise ChecksumError("the callback's checksum is not 64 hex digits")
    if not hmac.compare_digest(checksum(fields, secret), received.upper()):
        raise ChecksumError("the callback's checksum does not match")


# Callback and response models: only the fields we use


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class Registration(_Model):
    """`register.do`'s answer (fixtures/jcc/register.json): the order and its pay link."""

    order_id: OrderId = Field(alias="orderId")
    form_url: AnyHttpUrl = Field(alias="formUrl")

    @field_validator("order_id", mode="before")
    @classmethod
    def _order_id(cls, value: object) -> OrderId:
        if not isinstance(value, str):
            raise ValueError(f"orderId must be a string, got {value!r}")
        return OrderId(value)

    @field_validator("form_url")
    @classmethod
    def _https(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.scheme != "https":
            raise ValueError(f"formUrl must be https, got {value}")
        return value


class Callback(_Model):
    """A callback's fields, once its checksum is verified."""

    md_order: OrderId = Field(alias="mdOrder")
    order_number: str = Field(alias="orderNumber", min_length=1)
    operation: str = Field(min_length=1)
    status: Literal["0", "1"]

    @field_validator("md_order", mode="before")
    @classmethod
    def _order_id(cls, value: object) -> OrderId:
        if not isinstance(value, str):
            raise ValueError(f"mdOrder must be a string, got {value!r}")
        return OrderId(value)

    @property
    def is_deposit(self) -> bool:
        """A successful one-phase payment: the only callback that books a sale."""
        return self.operation == "deposited" and self.status == "1"


class PaymentAmountInfo(_Model):
    payment_state: str = Field(alias="paymentState")
    deposited_amount: int = Field(alias="depositedAmount", strict=True, ge=0)


class OrderStatus(_Model):
    """`getOrderStatusExtended.do`'s answer (fixtures/jcc/order_status_deposited.json)."""

    error_code: str = Field(alias="errorCode")
    order_number: str = Field(alias="orderNumber")
    order_status: int = Field(alias="orderStatus", strict=True)
    amount: int = Field(strict=True, ge=0)
    currency: str
    deposited_date: int | None = Field(None, alias="depositedDate", strict=True)
    payment_amount_info: PaymentAmountInfo = Field(alias="paymentAmountInfo")


# Mapping


def confirm_deposit(callback: Callback, status: OrderStatus) -> CardSale:
    """The card sale a `deposited` callback books, once its order status confirms it."""
    order = callback.md_order
    if not callback.is_deposit:
        raise DepositMismatch(
            f"order {order}: {callback.operation} with status {callback.status} is not a deposit"
        )
    if status.error_code != "0":
        raise DepositMismatch(f"order {order}: order status errorCode {status.error_code}")
    if status.order_number != callback.order_number:
        raise DepositMismatch(
            f"order {order}: the callback names order number {callback.order_number}, "
            f"the order status {status.order_number}"
        )
    if status.order_status != PAID:
        raise DepositMismatch(f"order {order}: orderStatus {status.order_status}, not {PAID}")
    if status.currency != EUR:
        raise DepositMismatch(f"order {order}: currency {status.currency}, not EUR ({EUR})")
    deposited = status.payment_amount_info.deposited_amount
    if deposited != status.amount or deposited == 0:
        raise DepositMismatch(
            f"order {order}: deposited {deposited} cents of an order for {status.amount}"
        )
    if status.deposited_date is None:
        raise DepositMismatch(f"order {order}: the order status has no depositedDate")
    deposited_at = dt.datetime.fromtimestamp(status.deposited_date / 1000, tz=CYPRUS)
    return CardSale(
        id=f"jcc-{order}",
        amount_cents=deposited,
        date=deposited_at.date(),
        jcc_order_id=str(order),
        status=callback.operation,
    )


# Client


class JccClient:
    def __init__(self, settings: JccSettings, http: httpx.AsyncClient) -> None:
        self.settings = settings
        self.http = http

    @property
    def sandbox(self) -> bool:
        """Whether this is JCC's test gateway, where only test cards pay."""
        return httpx.URL(self.settings.base_url).host == TEST_HOST

    @property
    def issues_links(self) -> bool:
        """Whether pay links can be registered: `register.do` needs a return URL."""
        return self.settings.return_url is not None

    async def register(
        self, order_number: str, amount_cents: int, description: str
    ) -> Registration:
        """A one-phase EUR order for `amount_cents`; its `formUrl` is the pay link."""
        if self.settings.return_url is None:
            raise JccError("register.do needs JCC_RETURN_URL")
        if not re.fullmatch(r"[A-Za-z0-9-]{1,36}", order_number):
            raise ValueError(f"not an order number JCC takes: {order_number!r}")
        if type(amount_cents) is not int or amount_cents <= 0:
            raise ValueError(f"the amount must be a positive number of cents, got {amount_cents!r}")
        if not description.strip() or len(description) > MAX_DESCRIPTION:
            raise ValueError(f"the description must be 1 to {MAX_DESCRIPTION} characters")
        params = {
            "orderNumber": order_number,
            "amount": str(amount_cents),
            "currency": EUR,
            "returnUrl": str(self.settings.return_url),
            "description": description,
            "language": "en",
            "sessionTimeoutSecs": str(self.settings.link_seconds),
        }
        if self.settings.callback_url is not None:
            params["dynamicCallbackUrl"] = str(self.settings.callback_url)
        data = await self._call("register.do", params)
        return Registration.model_validate(data)

    async def order_status(self, order: OrderId) -> OrderStatus:
        data = await self._call("getOrderStatusExtended.do", {"orderId": str(order)})
        return OrderStatus.model_validate(data)

    async def _call(self, method: str, params: dict[str, str]) -> Any:
        url = f"{self.settings.base_url.rstrip('/')}/{method}"
        body = {
            "userName": self.settings.api_user,
            "password": self.settings.api_password.get_secret_value(),
            **params,
        }
        try:
            response = await self.http.post(url, data=body)
        except httpx.TransportError as error:
            raise JccUnavailable(f"POST {method}: {error}") from error
        if response.status_code >= 500:
            raise JccUnavailable(f"POST {method} -> {response.status_code}: {response.text[:300]}")
        if response.is_error:
            raise JccError(f"POST {method} -> {response.status_code}: {response.text[:300]}")
        data = response.json()
        if not isinstance(data, dict):
            raise JccError(f"POST {method}: expected a JSON object, got {type(data).__name__}")
        error_code = data.get("errorCode", "0")
        if str(error_code) != "0":
            raise JccError(f"POST {method}: errorCode {error_code}: {data.get('errorMessage')}")
        return data
