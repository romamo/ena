"""The reply cache for the demo questions (docs/build-plan.md §8, §11).

A reply is cached under the seed, the business day, the ledger's state (a digest of the
receipts, the invoices' statuses and the booked card sales, so a payment or an approval
earlier that day is never answered from before it) and the question, so the stage run
answers the demo questions the same way every time, and still answers them when the
model is slow or down. The question is compared case-insensitively, with runs of
whitespace collapsed and trailing punctuation dropped.

Only a reply that moved no money is cached (`cacheable`): a cached reply never says
"paid" and never shows an approval, since replaying it does not call the gate. The cache
lives in memory and, given a path, in a JSON file that a rehearsal fills and the stage
run reads.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

from ena.models import (
    ApprovalRequest,
    BankAccount,
    BankConsent,
    Card,
    Offer,
    OfferKind,
    Receipt,
)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


Question = Annotated[str, StringConstraints(min_length=1)]


class CachedReply(_Frozen):
    text: Annotated[str, StringConstraints(min_length=1)]
    cards: tuple[Card, ...] = ()


class _Entry(_Frozen):
    seed: Annotated[int, Field(strict=True)]
    day: dt.date
    state: Annotated[str, StringConstraints(min_length=1)]
    question: Question
    reply: CachedReply


_ENTRIES = TypeAdapter(list[_Entry])


def normalize(question: str) -> str:
    """The question as the cache compares it; refuses an empty one."""
    text = re.sub(r"[\s?!.;,]+$", "", " ".join(question.split()).casefold())
    if not text:
        raise ValueError(f"not a question: {question!r}")
    return text


def cacheable(reply: CachedReply) -> bool:
    """Whether a reply may be cached: it shows no approval, no bank consent, no receipt,
    and no offer that writes a receipt (the loan, the event advance), since a replay
    writes none; nor the live bank account, which a replay would show stale."""
    return not any(
        isinstance(card, (ApprovalRequest, BankConsent, Receipt, BankAccount))
        or (isinstance(card, Offer) and card.kind is not OfferKind.RESERVE)
        for card in reply.cards
    )


class ReplyCache:
    """Replies by (seed, day, state, question), in memory and in `path` when given."""

    def __init__(self, seed: int, path: Path | None = None) -> None:
        self._seed = seed
        self._path = path
        self._entries: dict[tuple[int, dt.date, str, str], _Entry] = {}
        if path is not None and path.exists():
            for entry in _ENTRIES.validate_json(path.read_bytes()):
                self._entries[(entry.seed, entry.day, entry.state, entry.question)] = entry

    def get(self, question: str, day: dt.date, state: str) -> CachedReply | None:
        entry = self._entries.get((self._seed, day, state, normalize(question)))
        return None if entry is None else entry.reply

    def put(self, question: str, day: dt.date, state: str, reply: CachedReply) -> None:
        if not cacheable(reply):
            raise ValueError("a reply that moved money is never cached")
        entry = _Entry(
            seed=self._seed, day=day, state=state, question=normalize(question), reply=reply
        )
        self._entries[(entry.seed, entry.day, entry.state, entry.question)] = entry
        if self._path is not None:
            self._path.write_bytes(_ENTRIES.dump_json(list(self._entries.values()), indent=2))
