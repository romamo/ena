"""`POST /jcc/callback` books a card sale from the saved JCC callback, with no network.

The saved callback was signed with the sandbox secret, which is not in the repo, so these
tests sign the saved fields again with a test key. Order status answers from the saved
`order_status_deposited.json` through `httpx.MockTransport`.
"""

import datetime as dt
import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from ena.api import create_app
from ena.connectors.jcc import JccClient, JccSettings, checksum
from ena.models import CardSale
from ena.store import Store

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "jcc"
CALLBACK = json.loads((FIXTURES / "callback_deposited.json").read_text(encoding="utf-8"))
ORDER_STATUS = json.loads((FIXTURES / "order_status_deposited.json").read_text(encoding="utf-8"))
SECRET = SecretStr("test-callback-secret")
MD_ORDER = CALLBACK["fields"]["mdOrder"]
FORM = CALLBACK["content_type"]


@dataclass
class FakeJcc:
    """Order status from the saved response, optionally changed; counts the calls."""

    changes: dict[str, Any] = field(default_factory=dict)
    calls: int = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(200, json=ORDER_STATUS["response"] | self.changes)


@dataclass
class Api:
    http: TestClient
    store: Store
    jcc: FakeJcc

    def post(self, fields: dict[str, str]) -> httpx.Response:
        response: httpx.Response = self.http.post(
            "/jcc/callback", content=urlencode(fields), headers={"Content-Type": FORM}
        )
        return response


@pytest.fixture
def api() -> Iterator[Api]:
    store = Store(":memory:")
    fake = FakeJcc()
    settings = JccSettings(
        api_user="ena-api",
        api_password="api-password",
        callback_secret="test-callback-secret",
        _env_file=None,
    )
    jcc = JccClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    with TestClient(create_app(store, jcc, settings.callback_secret)) as http:
        yield Api(http, store, fake)
    store.close()


def signed(**changes: str) -> dict[str, str]:
    """The saved callback's fields, changed as given, signed with the test key."""
    fields = {k: v for k, v in CALLBACK["fields"].items() if k != "checksum"} | changes
    return fields | {"checksum": checksum(fields, SECRET)}


SAVED_SALE = CardSale(
    id=f"jcc-{MD_ORDER}",
    amount_cents=1500,
    date=dt.date(2026, 11, 28),
    jcc_order_id=MD_ORDER,
    status="deposited",
)


def test_saved_callback_books_one_sale(api: Api) -> None:
    response = api.post(signed())
    assert response.status_code == 200
    assert response.json() == {"result": "booked", "jcc_order_id": MD_ORDER}
    assert api.store.card_sales() == [SAVED_SALE]
    assert api.jcc.calls == 1


def test_repeated_callback_books_no_second_sale(api: Api) -> None:
    assert api.post(signed()).status_code == 200
    response = api.post(signed())
    assert response.status_code == 200
    assert response.json() == {"result": "already_booked", "jcc_order_id": MD_ORDER}
    assert api.store.card_sales() == [SAVED_SALE]
    assert api.jcc.calls == 1


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param(CALLBACK["fields"], id="saved checksum, other key"),
        pytest.param(signed() | {"checksum": "0" * 64}, id="wrong"),
        pytest.param({k: v for k, v in signed().items() if k != "checksum"}, id="missing"),
        pytest.param(signed() | {"orderNumber": "ENA-OTHER"}, id="field changed after signing"),
    ],
)
def test_bad_checksum_is_refused_and_books_nothing(api: Api, fields: dict[str, str]) -> None:
    response = api.post(fields)
    assert response.status_code == 403
    assert SECRET.get_secret_value() not in response.text
    assert api.store.card_sales() == []
    assert api.jcc.calls == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"operation": "approved"},
        {"operation": "declinedByTimeout", "status": "0"},
        {"operation": "refunded"},
        {"status": "0"},
    ],
)
def test_callback_that_is_not_a_deposit_books_nothing(api: Api, changes: dict[str, str]) -> None:
    response = api.post(signed(**changes))
    assert response.status_code == 200
    assert response.json() == {"result": "ignored", "jcc_order_id": MD_ORDER}
    assert api.store.card_sales() == []
    assert api.jcc.calls == 0


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"orderStatus": 0}, id="not paid"),
        pytest.param({"amount": 2000}, id="amount"),
        pytest.param({"currency": "840"}, id="currency"),
        pytest.param({"orderNumber": "ENA-OTHER"}, id="order number"),
    ],
)
def test_order_status_mismatch_is_refused_and_books_nothing(
    api: Api, changes: dict[str, Any]
) -> None:
    api.jcc.changes = changes
    response = api.post(signed())
    assert response.status_code == 409
    assert api.store.card_sales() == []
    assert api.jcc.calls == 1


def test_mismatch_then_confirmed_retry_books_once(api: Api) -> None:
    api.jcc.changes = {"orderStatus": 0}
    assert api.post(signed()).status_code == 409
    api.jcc.changes = {}
    assert api.post(signed()).status_code == 200
    assert api.store.card_sales() == [SAVED_SALE]


def test_malformed_callback_is_refused(api: Api) -> None:
    response = api.post(signed(mdOrder="not-an-order-id"))
    assert response.status_code == 422
    assert api.store.card_sales() == []


def test_repeated_field_is_refused(api: Api) -> None:
    body = urlencode(signed()) + "&status=0"
    response = api.http.post("/jcc/callback", content=body, headers={"Content-Type": FORM})
    assert response.status_code == 400
    assert api.store.card_sales() == []


def test_json_body_is_refused(api: Api) -> None:
    response = api.http.post("/jcc/callback", json=signed())
    assert response.status_code == 415
    assert api.store.card_sales() == []


def test_store_refuses_a_second_sale_for_one_order() -> None:
    store = Store(":memory:")
    store.add_card_sale(SAVED_SALE)
    with pytest.raises(sqlite3.IntegrityError):
        store.add_card_sale(SAVED_SALE.model_copy(update={"id": "jcc-other"}))
    assert store.card_sale_for(MD_ORDER) == SAVED_SALE
    assert store.card_sales() == [SAVED_SALE]


def test_order_status_down_answers_503_so_jcc_retries() -> None:
    store = Store(":memory:")
    settings = JccSettings(
        api_user="ena-api",
        api_password="api-password",
        callback_secret="test-callback-secret",
        _env_file=None,
    )

    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="maintenance")

    jcc = JccClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(down)))
    with TestClient(create_app(store, jcc, settings.callback_secret)) as http:
        response = http.post(
            "/jcc/callback", content=urlencode(signed()), headers={"Content-Type": FORM}
        )
    assert response.status_code == 503
    assert "api-password" not in response.text
    assert store.card_sales() == []
