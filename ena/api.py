"""Ena's HTTP API (docs/build-plan.md §3, §5, §8): the chat, the owner's taps, the JCC callback.

The API is thin: the chat turn is `ena.agent.Chat`, money is the gate's.

`POST /chat` takes the owner's message (`{"text": ...}`) and streams the turn over SSE,
one event per `ena.agent.ChatEvent`, named by its `kind`: `card` (an approval, a receipt,
the forecast, an offer), `text` (a piece of the reply), then `done` (the whole reply) or
`error` ("try again", with no detail of the failure).

`POST /approvals/{id}/approve` and `POST /approvals/{id}/decline` are the owner's tap on
an approval card. They call the gate's approval path directly, never the agent, and
answer with the receipt card; an unknown or already decided approval is refused with
409. No tool can reach these: approval comes only from the owner (§5).

`GET /bank` says which bank the gate pays through: `{"live": false}` for the stand-in,
true for the bocAPI sandbox, so the page labels its payments.

With the bocAPI sandbox each payment the gate makes waits for the owner's confirmation in
BoC's own screen (`ena/gate.py`). `GET /boc/consent` is where that screen sends the
browser back: with `code` it executes the payment and answers a page with the receipt,
with `error` it writes a failed receipt. `GET /consents/{id}` answers the consent card
while it waits and the receipt card once it is settled (the chat page polls it), and
`POST /consents/{id}/cancel` stops waiting, with a failed receipt. A consent already
settled, or none open, is refused with 409.

`POST /offers/{id}/accept` and `POST /offers/{id}/decline` are the owner's tap on an
event advance's offer card (`ena/advances.py`). They answer with the receipt card; an
unknown or already decided offer, or a second accepted advance for an event, is refused
with 409. No tool can reach these either.

`GET /dashboard/overview`, `/dashboard/receipts`, `/dashboard/forecast` and
`/dashboard/credit` are the owner's dashboard (`ena/dashboard.py`): read only, they pay,
approve and write nothing, and add nothing to a chat turn. A day with no forecast is
answered 409.

`POST /jcc/callback` takes JCC's form-encoded webhook (docs/jcc-notes.md §0, §7 step 4):

1. The checksum is verified first; a missing or wrong one is refused with 403, books nothing
2. Only `operation=deposited` with `status=1` books; any other callback is answered 200
3. A sale already booked for the `mdOrder` is answered 200 and not booked again
4. `getOrderStatusExtended.do` must confirm it (`orderStatus` 2, the amount, EUR); a
   mismatch is refused with 409, books nothing, and JCC retries it
5. The `CardSale` is booked in the store and the callback answered 200

JCC treats any answer but 200 as a failure and retries every 30 s, up to 6 times.
The callback secret never appears in an answer.
"""

from __future__ import annotations

import html
from collections.abc import AsyncIterator
from typing import Annotated, Literal
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, SecretStr, StringConstraints, ValidationError

from ena.advances import AdvanceError, Advances
from ena.agent import CardEvent, Chat, card_event
from ena.connectors.jcc import (
    Callback,
    ChecksumError,
    DepositMismatch,
    JccClient,
    JccError,
    JccUnavailable,
    confirm_deposit,
    verify_checksum,
)
from ena.dashboard import Credit, Dashboard, ForecastView, Overview, Receipts
from ena.gate import Gate, GateError, PaymentResult
from ena.mcp_server import ToolError
from ena.store import Store

FORM = "application/x-www-form-urlencoded"

MAX_MESSAGE = 2_000
"""The longest owner message, in characters."""


class CallbackAnswer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    result: Literal["booked", "already_booked", "ignored"]
    jcc_order_id: str


class BankMode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    live: bool
    """True when the gate pays through the bocAPI sandbox, False for the stand-in."""


class ChatRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE)
    ]


def create_app(
    store: Store,
    jcc: JccClient,
    callback_secret: SecretStr,
    *,
    chat: Chat | None = None,
    gate: Gate | None = None,
    advances: Advances | None = None,
    dashboard: Dashboard | None = None,
) -> FastAPI:
    """The API, booking into `store` and confirming deposits through `jcc`.

    With `chat` it serves `POST /chat`; with `gate`, the owner's approve and decline; with
    `advances`, the owner's accept and decline of an event advance; with
    `dashboard`, the dashboard's reads.
    """
    if not callback_secret.get_secret_value():
        # An empty key would accept any callback signed with HMAC-SHA256 keyed with b""
        raise ValueError("the JCC callback secret must not be empty")
    app = FastAPI(title="Ena")

    if chat is not None:
        chat_turn = chat

        @app.post("/chat", response_class=EventSourceResponse)
        async def send(request: ChatRequest) -> AsyncIterator[ServerSentEvent]:
            async for event in chat_turn.send(request.text):
                yield ServerSentEvent(data=event.model_dump(mode="json"), event=event.kind)

    if gate is not None:
        owner_gate = gate

        @app.post("/approvals/{approval_id}/approve")
        async def approve(approval_id: str) -> CardEvent:
            try:
                return card_event(owner_gate.approve(approval_id))
            except GateError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

        @app.post("/approvals/{approval_id}/decline")
        async def decline(approval_id: str) -> CardEvent:
            try:
                return card_event(owner_gate.decline(approval_id))
            except GateError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

        @app.get("/bank")
        async def bank() -> BankMode:
            return BankMode(live=not owner_gate.simulated)

        @app.get("/consents/{consent_id}")
        async def consent(consent_id: str) -> CardEvent:
            try:
                return card_event(owner_gate.consent(consent_id))
            except GateError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error

        @app.post("/consents/{consent_id}/cancel")
        async def cancel_consent(consent_id: str) -> CardEvent:
            try:
                return card_event(owner_gate.cancel(consent_id))
            except GateError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

        @app.get("/boc/consent", response_class=HTMLResponse)
        @app.get("/boc/consent/", response_class=HTMLResponse, include_in_schema=False)
        async def boc_consent(
            code: str | None = None,
            error: str | None = None,
            error_description: str | None = None,
        ) -> HTMLResponse:
            try:
                if error is not None:
                    receipt = owner_gate.refuse(f"{error} {error_description or ''}".strip())
                elif code is not None and code.strip():
                    receipt = owner_gate.confirm(code)
                else:
                    return _page(400, "No confirmation", "The bank sent no code back.")
            except GateError as refused:
                return _page(409, "Nothing to confirm", str(refused))
            titles = {PaymentResult.PAID: "Paid", PaymentResult.UNKNOWN: "Check the bank"}
            title = titles.get(PaymentResult(receipt.result), "Not paid")
            paid = receipt.result == PaymentResult.PAID
            return _page(200, title, receipt.explanation, close=paid)

    if dashboard is not None:
        board = dashboard

        @app.get("/dashboard/overview")
        async def overview() -> Overview:
            try:
                return board.overview()
            except ToolError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

        @app.get("/dashboard/receipts")
        async def receipts() -> Receipts:
            return board.receipts()

        @app.get("/dashboard/forecast")
        async def forecast() -> ForecastView:
            try:
                return board.forecast()
            except ToolError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

        @app.get("/dashboard/credit")
        async def credit() -> Credit:
            try:
                return board.credit()
            except ToolError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

    if advances is not None:
        owner_advances = advances

        @app.post("/offers/{offer_id}/accept")
        async def accept_offer(offer_id: str) -> CardEvent:
            try:
                return card_event(owner_advances.accept(offer_id))
            except AdvanceError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

        @app.post("/offers/{offer_id}/decline")
        async def decline_offer(offer_id: str) -> CardEvent:
            try:
                return card_event(owner_advances.decline(offer_id))
            except AdvanceError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/jcc/callback")
    async def jcc_callback(request: Request) -> CallbackAnswer:
        fields = await _form_fields(request)
        try:
            verify_checksum(fields, callback_secret)
        except ChecksumError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        try:
            callback = Callback.model_validate(fields)
        except ValidationError as error:
            raise HTTPException(
                status_code=422, detail=error.errors(include_url=False, include_context=False)
            ) from error
        order = str(callback.md_order)
        if not callback.is_deposit:
            return CallbackAnswer(result="ignored", jcc_order_id=order)
        if store.card_sale_for(order) is not None:
            return CallbackAnswer(result="already_booked", jcc_order_id=order)

        try:
            status = await jcc.order_status(callback.md_order)
        except JccUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except JccError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        try:
            sale = confirm_deposit(callback, status)
        except DepositMismatch as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

        # A second callback for the order may have booked it while order status was awaited
        if store.card_sale_for(order) is not None:
            return CallbackAnswer(result="already_booked", jcc_order_id=order)
        store.add_card_sale(sale)
        return CallbackAnswer(result="booked", jcc_order_id=order)

    return app


def _page(status: int, title: str, message: str, *, close: bool = False) -> HTMLResponse:
    """The small page the owner's browser lands on after the bank's screen."""
    script = "<script>setTimeout(() => window.close(), 1500)</script>" if close else ""
    body = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Ena: {html.escape(title)}</title>"
        "<style>body{font:16px system-ui,sans-serif;margin:2rem 1rem;max-width:32rem}</style>"
        f"</head><body><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>"
        "<p><a href='/'>Back to Ena</a>: the chat shows the receipt.</p>"
        f"{script}</body></html>"
    )
    return HTMLResponse(body, status_code=status)


async def _form_fields(request: Request) -> dict[str, str]:
    """The callback's form fields; refuses another content type, a malformed body or a
    repeated field (the checksum covers each field once)."""
    content_type = request.headers.get("content-type", "")
    if content_type.split(";")[0].strip().lower() != FORM:
        raise HTTPException(status_code=415, detail=f"expected {FORM}, got {content_type!r}")
    raw = await request.body()
    try:
        pairs = parse_qsl(raw.decode("utf-8"), keep_blank_values=True, strict_parsing=True)
    except (UnicodeDecodeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=f"malformed form body: {error}") from error
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise HTTPException(status_code=400, detail="a form field is repeated")
    return fields
