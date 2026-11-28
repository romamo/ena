"""The JCC connector: the callback checksum, the order-status call and its mapping."""

import asyncio
import datetime as dt
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import httpx
import pytest
from pydantic import SecretStr

from ena.connectors.jcc import (
    Callback,
    ChecksumError,
    DepositMismatch,
    JccClient,
    JccError,
    JccSettings,
    JccUnavailable,
    OrderId,
    OrderStatus,
    checksum,
    confirm_deposit,
    verify_checksum,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "jcc"
CALLBACK = json.loads((FIXTURES / "callback_deposited.json").read_text(encoding="utf-8"))
ORDER_STATUS = json.loads((FIXTURES / "order_status_deposited.json").read_text(encoding="utf-8"))
SECRET = SecretStr("test-callback-secret")
"""A test key: the sandbox secret that signed the saved callback is not in the repo."""
ORDER = OrderId(CALLBACK["fields"]["mdOrder"])


def unsigned_fields() -> dict[str, str]:
    fields = dict(CALLBACK["fields"])
    del fields["checksum"]
    return fields


def signed_fields(**changes: str) -> dict[str, str]:
    fields = unsigned_fields() | changes
    return fields | {"checksum": checksum(fields, SECRET)}


def settings() -> JccSettings:
    return JccSettings(
        api_user="ena-api",
        api_password="api-password",
        callback_secret="test-callback-secret",
        _env_file=None,
    )


def status_with(**changes: Any) -> OrderStatus:
    return OrderStatus.model_validate(ORDER_STATUS["response"] | changes)


# Checksum


def test_checksum_signs_sorted_name_value_pairs() -> None:
    fields = unsigned_fields()
    expected = checksum(
        {
            "mdOrder": fields["mdOrder"],
            "operation": "deposited",
            "orderNumber": fields["orderNumber"],
            "status": "1",
        },
        SECRET,
    )
    message = (
        f"mdOrder;{fields['mdOrder']};operation;deposited;"
        f"orderNumber;{fields['orderNumber']};status;1;"
    )
    by_hand = hmac.new(b"test-callback-secret", message.encode(), hashlib.sha256).hexdigest()
    assert expected == by_hand.upper()


def test_checksum_skips_checksum_and_sign_alias() -> None:
    fields = unsigned_fields()
    assert checksum(fields | {"sign_alias": "key-1", "checksum": "X"}, SECRET) == checksum(
        fields, SECRET
    )


def test_saved_raw_body_holds_the_saved_fields() -> None:
    assert dict(parse_qsl(CALLBACK["raw_body"], strict_parsing=True)) == CALLBACK["fields"]


def test_verify_accepts_a_signed_callback_in_either_case() -> None:
    fields = signed_fields()
    verify_checksum(fields, SECRET)
    verify_checksum(fields | {"checksum": fields["checksum"].lower()}, SECRET)


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param(CALLBACK["fields"], id="saved checksum, other key"),
        pytest.param(unsigned_fields(), id="missing"),
        pytest.param(signed_fields() | {"checksum": "abc"}, id="not hex"),
        pytest.param(signed_fields() | {"status": "0"}, id="field changed after signing"),
    ],
)
def test_verify_refuses(fields: dict[str, str]) -> None:
    with pytest.raises(ChecksumError):
        verify_checksum(fields, SECRET)


def test_checksum_error_never_shows_the_expected_checksum() -> None:
    fields = signed_fields()
    with pytest.raises(ChecksumError) as caught:
        verify_checksum(fields | {"checksum": "0" * 64}, SECRET)
    assert fields["checksum"] not in str(caught.value)
    assert SECRET.get_secret_value() not in str(caught.value)


# Mapping


def test_confirm_deposit_books_the_saved_sale() -> None:
    callback = Callback.model_validate(CALLBACK["fields"])
    sale = confirm_deposit(callback, status_with())
    assert sale.id == f"jcc-{ORDER}"
    assert sale.jcc_order_id == str(ORDER)
    assert sale.amount_cents == 1500
    assert sale.date == dt.date(2026, 11, 28)
    assert sale.status == "deposited"


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"orderStatus": 1}, id="only pre-authorized"),
        pytest.param({"orderStatus": 4}, id="refunded"),
        pytest.param({"errorCode": "5"}, id="error code"),
        pytest.param({"orderNumber": "ENA-OTHER"}, id="other order number"),
        pytest.param({"currency": "840"}, id="not EUR"),
        pytest.param({"amount": 2000}, id="deposited less than the order"),
        pytest.param(
            {"amount": 0, "paymentAmountInfo": {"paymentState": "DEPOSITED", "depositedAmount": 0}},
            id="nothing deposited",
        ),
        pytest.param({"depositedDate": None}, id="no deposit date"),
    ],
)
def test_confirm_deposit_refuses_a_mismatch(changes: dict[str, Any]) -> None:
    callback = Callback.model_validate(CALLBACK["fields"])
    with pytest.raises(DepositMismatch):
        confirm_deposit(callback, status_with(**changes))


def test_confirm_deposit_refuses_a_callback_that_is_not_a_deposit() -> None:
    callback = Callback.model_validate(CALLBACK["fields"] | {"operation": "refunded"})
    with pytest.raises(DepositMismatch):
        confirm_deposit(callback, status_with())


@pytest.mark.parametrize(
    "changes",
    [{"mdOrder": "not-an-order"}, {"status": "2"}, {"orderNumber": ""}],
)
def test_callback_refuses_malformed_fields(changes: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        Callback.model_validate(CALLBACK["fields"] | changes)


def test_order_status_refuses_a_string_amount() -> None:
    with pytest.raises(ValueError):
        status_with(amount="1500")


# Client


def client(handler: Any) -> JccClient:
    return JccClient(settings(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_order_status_posts_the_order_id_with_the_api_user() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ORDER_STATUS["response"])

    status = asyncio.run(client(handler).order_status(ORDER))
    assert status.order_status == 2
    assert status.payment_amount_info.deposited_amount == 1500
    (request,) = seen
    assert request.method == "POST"
    assert request.url == "https://gateway-test.jcc.com.cy/payment/rest/getOrderStatusExtended.do"
    body = dict(parse_qsl(request.content.decode()))
    assert body == {"userName": "ena-api", "password": "api-password", "orderId": str(ORDER)}


def test_order_status_refuses_an_error_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errorCode": "6", "errorMessage": "Order not found"})

    with pytest.raises(JccError, match="errorCode 6"):
        asyncio.run(client(handler).order_status(ORDER))


def test_order_status_down_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    with pytest.raises(JccUnavailable):
        asyncio.run(client(handler).order_status(ORDER))
