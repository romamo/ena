"""Ena's HTTP API (docs/build-plan.md §5): the JCC callback that books a card sale.

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

from typing import Literal
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

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
from ena.store import Store

FORM = "application/x-www-form-urlencoded"


class CallbackAnswer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    result: Literal["booked", "already_booked", "ignored"]
    jcc_order_id: str


def create_app(store: Store, jcc: JccClient, callback_secret: SecretStr) -> FastAPI:
    """The API, booking into `store` and confirming deposits through `jcc`."""
    app = FastAPI(title="Ena")

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
