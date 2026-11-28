"""Pay links: `register.do` in the JCC connector, the `create_pay_link` tool and its card."""

import asyncio
import datetime as dt
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import httpx
import pytest

from ena.agent import card_event
from ena.cache import CachedReply, cacheable
from ena.connectors.jcc import JccClient, JccError, JccSettings
from ena.gate import Gate
from ena.mcp_server import (
    MAX_PAY_LINK_CENTS,
    NO_PAY_LINKS,
    EnaTools,
    Ledger,
    ReplayDay,
    ToolError,
    call_tool,
)
from ena.models import PayLink, Receipt
from ena.seed import LEAN_WEEK, synthetic_month
from ena.store import Store

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "jcc"
REGISTER = json.loads((FIXTURES / "register.json").read_text(encoding="utf-8"))
ORDER_ID = REGISTER["response"]["orderId"]
FORM_URL = REGISTER["response"]["formUrl"]
MONTH = synthetic_month()
NOW = dt.datetime(2026, 11, 16, 9, 0, tzinfo=dt.UTC)


def jcc(handler: Any, **changes: Any) -> JccClient:
    settings = JccSettings(
        api_user="ena-api",
        api_password="api-password",
        callback_secret="test-callback-secret",
        _env_file=None,
        **({"return_url": "https://ena.test/paid"} | changes),
    )
    return JccClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def registered(seen: list[httpx.Request]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=REGISTER["response"])

    return handler


def refuse(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"no request expected, got {request.url}")


def body(request: httpx.Request) -> dict[str, str]:
    return dict(parse_qsl(request.content.decode()))


# The connector


def test_register_posts_a_eur_order_and_answers_its_pay_link() -> None:
    seen: list[httpx.Request] = []
    client = jcc(registered(seen))
    assert client.issues_links and client.sandbox
    registration = asyncio.run(client.register("ena-0123456789abcdef", 1500, "Two loaves"))
    assert str(registration.order_id) == ORDER_ID
    assert str(registration.form_url) == FORM_URL
    (request,) = seen
    assert request.url == "https://gateway-test.jcc.com.cy/payment/rest/register.do"
    assert body(request) == {
        "userName": "ena-api",
        "password": "api-password",
        "orderNumber": "ena-0123456789abcdef",
        "amount": "1500",
        "currency": "978",
        "returnUrl": "https://ena.test/paid",
        "description": "Two loaves",
        "language": "en",
        "sessionTimeoutSecs": "3600",
    }


def test_register_sends_the_callback_url_only_when_set() -> None:
    seen: list[httpx.Request] = []
    client = jcc(registered(seen), callback_url="https://tunnel.test/jcc/callback")
    asyncio.run(client.register("ena-1", 1500, "Two loaves"))
    assert body(seen[0])["dynamicCallbackUrl"] == "https://tunnel.test/jcc/callback"


def test_register_needs_a_return_url() -> None:
    client = jcc(refuse, return_url=None)
    assert not client.issues_links
    with pytest.raises(JccError, match="JCC_RETURN_URL"):
        asyncio.run(client.register("ena-1", 1500, "Two loaves"))


@pytest.mark.parametrize(
    ("order_number", "amount", "description"),
    [
        pytest.param("ena 1", 1500, "x", id="space in order number"),
        pytest.param("e" * 37, 1500, "x", id="order number too long"),
        pytest.param("ena-1", 0, "x", id="zero amount"),
        pytest.param("ena-1", 1500, " ", id="blank description"),
        pytest.param("ena-1", 1500, "x" * 599, id="description too long"),
    ],
)
def test_register_refuses_a_malformed_order(
    order_number: str, amount: int, description: str
) -> None:
    with pytest.raises(ValueError):
        asyncio.run(jcc(refuse).register(order_number, amount, description))


@pytest.mark.parametrize(
    "response",
    [
        pytest.param({"errorCode": "1", "errorMessage": "Duplicate"}, id="error code"),
        pytest.param({"orderId": "not-an-order", "formUrl": FORM_URL}, id="bad order id"),
        pytest.param({"orderId": ORDER_ID, "formUrl": "http://x.test/"}, id="plain http"),
    ],
)
def test_register_refuses_a_bad_answer(response: dict[str, str]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)

    with pytest.raises((JccError, ValueError)):
        asyncio.run(jcc(handler).register("ena-1", 1500, "Two loaves"))


# The tool


class NoBank:
    def pay(self, action: str, amount_cents: int, ref: str) -> str:
        raise AssertionError("a pay link pays no one")


def tools(pay_links: JccClient | None) -> tuple[EnaTools, Store]:
    store = Store(":memory:")
    gate = Gate(MONTH.business, store, NoBank(), lambda: NOW)
    ledger = Ledger(MONTH, store, ReplayDay(LEAN_WEEK))
    return EnaTools(ledger, store, gate, lambda: NOW, pay_links=pay_links), store


def test_create_pay_link_shows_the_link_as_a_card_and_writes_a_receipt() -> None:
    seen: list[httpx.Request] = []
    t, store = tools(jcc(registered(seen)))
    t.start_turn()
    result = asyncio.run(t.create_pay_link(1500, "  Two loaves\nand a coffee "))
    link = result.pay_link
    assert link.jcc_order_id == ORDER_ID
    assert link.url == FORM_URL
    assert link.amount_cents == 1500
    assert link.description == "Two loaves and a coffee"
    assert link.sandbox
    assert link.expires_at == NOW + dt.timedelta(hours=1)
    assert body(seen[0])["orderNumber"] == link.id
    assert result.amount == "€15.00"
    assert "paid" not in result.note
    turn = t.finish_turn()
    assert turn.cards == [link]
    assert turn.receipts == []
    (receipt,) = store.receipts()
    assert (receipt.ref, receipt.result, receipt.amount_cents) == (ORDER_ID, "link_created", 1500)
    assert not receipt.simulated
    assert store.card_sales() == []


def test_each_pay_link_is_a_new_order() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        order = f"{ORDER_ID[:-1]}{len(seen)}"
        return httpx.Response(200, json={"orderId": order, "formUrl": FORM_URL})

    t, store = tools(jcc(handler))
    asyncio.run(t.create_pay_link(1500, "Two loaves"))
    asyncio.run(t.create_pay_link(1500, "Two loaves"))
    numbers = {body(request)["orderNumber"] for request in seen}
    assert len(numbers) == 2
    assert len(store.receipts()) == 2


@pytest.mark.parametrize(
    "pay_links",
    [pytest.param(None, id="no JCC"), pytest.param(jcc(refuse, return_url=None), id="no return")],
)
def test_create_pay_link_refuses_without_jcc_and_a_return_url(
    pay_links: JccClient | None,
) -> None:
    t, store = tools(pay_links)
    with pytest.raises(ToolError, match="pay links are off"):
        asyncio.run(t.create_pay_link(1500, "Two loaves"))
    assert store.receipts() == []
    assert "JCC_RETURN_URL" in NO_PAY_LINKS


@pytest.mark.parametrize(
    ("amount", "description"),
    [
        pytest.param(0, "x", id="zero"),
        pytest.param(MAX_PAY_LINK_CENTS + 1, "x", id="above the most"),
        pytest.param(15.0, "x", id="a float"),
        pytest.param("1500", "x", id="a string"),
        pytest.param(1500, "   ", id="blank description"),
        pytest.param(1500, "x" * 121, id="description too long"),
    ],
)
def test_create_pay_link_refuses_a_bad_amount_or_description(amount: Any, description: str) -> None:
    t, store = tools(jcc(refuse))
    with pytest.raises(ToolError):
        asyncio.run(t.create_pay_link(amount, description))
    assert store.receipts() == []


def test_a_jcc_failure_is_a_tool_error_and_writes_nothing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    t, store = tools(jcc(handler))
    t.start_turn()
    with pytest.raises(ToolError, match="JCC did not create the link"):
        asyncio.run(t.create_pay_link(1500, "Two loaves"))
    assert t.finish_turn().cards == []
    assert store.receipts() == []


def test_the_tool_runs_through_call_tool() -> None:
    t, _ = tools(jcc(registered([])))
    text = asyncio.run(
        call_tool(t, "create_pay_link", {"amount_cents": 1500, "description": "Two loaves"})
    )
    assert json.loads(text)["pay_link"]["url"] == FORM_URL


# The card


def link() -> PayLink:
    return PayLink(
        id="ena-1",
        jcc_order_id=ORDER_ID,
        amount_cents=1500,
        description="Two loaves",
        url=FORM_URL,
        created_at=NOW,
        expires_at=NOW + dt.timedelta(hours=1),
        sandbox=True,
    )


def test_a_pay_link_is_a_pay_link_card_and_is_never_cached() -> None:
    event = card_event(link())
    assert event.card_kind == "pay_link"
    assert event.model_dump(mode="json")["card"]["url"] == FORM_URL
    assert not cacheable(CachedReply(text="Here is the link.", cards=(link(),)))


@pytest.mark.parametrize(
    "changes",
    [
        {"url": "http://x.test/"},
        {"amount_cents": 0},
        {"expires_at": NOW},
    ],
)
def test_a_pay_link_refuses_a_bad_field(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        PayLink.model_validate(link().model_dump() | changes)


def test_a_receipt_is_not_read_as_a_pay_link() -> None:
    receipt = Receipt(
        id="r",
        at=NOW,
        actor="ena",
        action="a",
        amount_cents=1,
        ref="x",
        result="link_created",
        explanation="e",
    )
    assert card_event(receipt).card_kind == "receipt"
