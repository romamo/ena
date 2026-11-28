"""The chat test script (docs/build-plan.md §13): 11 owner questions on the seed month.

`chat_questions.json` holds each question in English and Greek, the day of November it
is asked on, the invoices paid before it (through the gate, the owner approving any above
the limit), the tools that answer it, and the euro figures a right answer gives. Those
figures are derived from the seed in `test_the_expected_figures_follow_the_seed`, from its
constants and records and its docstring's numbers, never from the tools.

The default run asks each question through `Chat` with a scripted agent that calls the
listed tools and restates the euro figures they returned, as the answer rules say: no
model, no network, no API key. So it checks that the tools give every expected figure on
that day, and that a question no tool answers gets "I don't know" (§11).

The stage rehearsal asks the same questions through the real agent, and checks that every
expected figure is in the reply and every figure in the reply came from the listed tools.
It runs only when `ANTHROPIC_API_KEY` is set (the Claude Agent SDK reads it).
"""

import asyncio
import datetime as dt
import json
import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal, Self

import pytest
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from ena.agent import (
    AgentSettings,
    CardEvent,
    CardKind,
    Chat,
    ChatEvent,
    ClaudeAgent,
    DoneEvent,
    ErrorEvent,
)
from ena.gate import Gate
from ena.mcp_server import TOOLS, EnaTools, Ledger, ReplayDay, build_server, call_tool
from ena.models import EventStatus
from ena.seed import AMOUNTS, BOOKED_EVENT_ID, LEAN_WEEK, LOAN_DAY, RENT_DAY, synthetic_month
from ena.store import Store

MONTH = synthetic_month()
DAIRY, FLOUR = MONTH.invoices
NOW = dt.datetime(2026, 11, 16, 9, 0, tzinfo=dt.UTC)
QUESTIONS_FILE = Path(__file__).with_name("chat_questions.json")

# The numbers `ena/seed.py`'s docstring documents, in cents
LEAN_WEEK_CLOSING = 34_969
"""Run on its Monday, the forecast closes the lean week at €349.69."""
WEEKLY_CARD_SALES = 72_050
"""One week of expected card sales, €720.50."""
LOWEST_BALANCE = 9_088
"""With both invoices paid on arrival, the month's lowest balance, after the rent."""

LOAN_STEP = 50_000
"""A loan offer is the gap rounded up to the next €500 (docs/build-plan.md §7)."""

FIGURE = re.compile(r"-?€(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?")
"""A euro figure as the tools write it (`€1,240.00`, `-€400.00`), or in whole euros."""

GREEK = re.compile(r"[Ά-ώ]")
"""A Greek letter, with or without its accent (U+0386 to U+03CE)."""
DONT_KNOW = re.compile(
    r"\bI don't know\b|\bI do not know\b|δεν (?:το )?(?:ξέρω|γνωρίζω)",
    re.IGNORECASE,
)

Language = Literal["en", "el"]
LANGUAGES: tuple[Language, ...] = ("en", "el")


class ToolCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    tool: str
    arguments: dict[str, str | int]


class Question(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    id: Annotated[str, Field(pattern=r"^[a-z][a-z-]*$")]
    en: Annotated[str, Field(min_length=1)]
    el: Annotated[str, Field(min_length=1)]
    day: Annotated[int, Field(ge=1, le=30)]
    """The day of November 2026 it is asked on."""
    paid_before: tuple[str, ...]
    tools: tuple[ToolCall, ...]
    figures: tuple[str, ...]
    cards: tuple[CardKind, ...]
    dont_know: bool

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if GREEK.search(self.en) or not GREEK.search(self.el):
            raise ValueError(f"{self.id}: en must be English and el Greek")
        if self.dont_know != (not self.tools):
            raise ValueError(f"{self.id}: exactly the questions no tool answers are dont_know")
        if self.dont_know and (self.figures or self.cards):
            raise ValueError(f"{self.id}: an 'I don't know' answer has no figures or cards")
        if not self.dont_know and not self.figures:
            raise ValueError(f"{self.id}: an answered question has expected figures")
        for figure in self.figures:
            if not FIGURE.fullmatch(figure) or "." not in figure:
                raise ValueError(f"{self.id}: {figure!r} is not a €1,234.56 figure")
        return self

    @property
    def date(self) -> dt.date:
        return MONTH.first_day.replace(day=self.day)

    def text(self, language: Language) -> str:
        return self.en if language == "en" else self.el


QUESTIONS = TypeAdapter(tuple[Question, ...]).validate_json(QUESTIONS_FILE.read_bytes())
BY_ID = {question.id: question for question in QUESTIONS}
CASES = [
    pytest.param(question, language, id=f"{question.id}-{language}")
    for question in QUESTIONS
    for language in LANGUAGES
]


def cents(figure: str) -> int:
    """A euro figure's absolute amount in cents: `-€1,800.00` is 180_000."""
    match = FIGURE.fullmatch(figure)
    if match is None:
        raise ValueError(f"not a euro figure: {figure!r}")
    whole, part = match.groups()
    return int(whole.replace(",", "")) * 100 + int(part or "0")


def figures_in(text: str) -> list[str]:
    return [match.group(0) for match in FIGURE.finditer(text)]


# The seed's own numbers


def balance_through(day: dt.date) -> int:
    """The opening balance plus the statement lines dated from the 1st to `day`."""
    posted = sum(tx.amount_cents for tx in MONTH.transactions if MONTH.first_day <= tx.date <= day)
    return MONTH.account.balance_cents + posted


def paid_on_arrival_by(day: dt.date) -> int:
    return sum(i.amount_cents for i in MONTH.invoices if i.issue_date <= day)


def test_the_expected_figures_follow_the_seed() -> None:
    rent, instalment = AMOUNTS.rent_cents, AMOUNTS.loan_instalment_cents
    dairy, flour = AMOUNTS.dairy_invoice_cents, AMOUNTS.flour_invoice_cents
    assert (DAIRY.amount_cents, FLOUR.amount_cents) == (dairy, flour)
    rent_day = MONTH.first_day.replace(day=RENT_DAY)

    # The lean week opens at the balance before its Monday: the docstring's closing,
    # less one week of card sales, plus both invoices and the rent going out that week
    opening = balance_through(LEAN_WEEK - dt.timedelta(days=1))
    assert opening + WEEKLY_CARD_SALES - dairy - flour - rent == LEAN_WEEK_CLOSING
    assert balance_through(LEAN_WEEK) == opening  # nothing settles on a Monday

    # With both invoices paid on arrival, the lowest balance is after the rent
    days = [MONTH.first_day + dt.timedelta(days=n) for n in range(MONTH.last_day.day)]
    paid_on_arrival = {day: balance_through(day) - paid_on_arrival_by(day) for day in days}
    assert paid_on_arrival[rent_day] == LOWEST_BALANCE == min(paid_on_arrival.values())

    # The loan covers the gap below the threshold: the instalment plus a buffer of one
    # week of the history window's outflows (October's rent and instalment)
    window = [tx for tx in MONTH.transactions if LEAN_WEEK - dt.timedelta(weeks=4) <= tx.date]
    out = -sum(tx.amount_cents for tx in window if tx.date < LEAN_WEEK and tx.amount_cents < 0)
    assert out == rent + instalment
    gap = instalment + out // 4 - LEAN_WEEK_CLOSING
    assert gap > instalment  # the reserve alone does not cover it
    loan = -(-gap // LOAN_STEP) * LOAN_STEP

    # The event advance on the lean Monday: the costs fall due 7 days before the wedding
    # (the most any past event took), after the lean week, which closes below the
    # threshold, so there is no spare cash and the advance is the costs less the deposit,
    # repaid from the final payment
    wedding = next(event for event in MONTH.events if event.id == BOOKED_EVENT_ID)
    completed = [event for event in MONTH.events if event.status is EventStatus.COMPLETED]
    costs_before = max(
        (event.event_date - event.costs_paid_date).days
        for event in completed
        if event.costs_paid_date is not None
    )
    assert costs_before == 7
    assert LEAN_WEEK <= wedding.event_date - dt.timedelta(days=costs_before)
    assert LEAN_WEEK_CLOSING < instalment + out // 4  # the lean week: spare cash is 0
    advance = wedding.costs_cents - wedding.deposit_cents

    expected = {
        "balance": [paid_on_arrival[rent_day]],
        "rent-friday": [balance_through(LEAN_WEEK), LEAN_WEEK_CLOSING],
        "lean-week": [LEAN_WEEK_CLOSING, instalment],
        "reserve": [instalment],
        "loan": [loan],
        "event-advance": [advance, wedding.final_payment_cents],
        "unpaid-invoices": [dairy, flour, dairy + flour],
        "pay-flour": [flour, AMOUNTS.auto_pay_limit_cents],
        "unmatched-payments": [rent, instalment],
        "receipts": [flour, dairy],
        "unknown-vat": [],
    }
    assert {q.id: [cents(f) for f in q.figures] for q in QUESTIONS} == expected
    assert BY_ID["balance"].day == RENT_DAY
    assert BY_ID["rent-friday"].date == LEAN_WEEK
    assert MONTH.loan.next_due_date.day == LOAN_DAY


def test_the_script_has_eleven_questions_on_real_tools_and_invoices() -> None:
    assert len(QUESTIONS) == 11
    assert len(BY_ID) == len(QUESTIONS)
    names = {spec.name for spec in TOOLS}
    invoice_ids = {invoice.id for invoice in MONTH.invoices}
    for question in QUESTIONS:
        assert {call.tool for call in question.tools} <= names, question.id
        assert set(question.paid_before) <= invoice_ids, question.id
    assert sum(question.dont_know for question in QUESTIONS) == 1


# Asking a question


@dataclass
class FakeBank:
    calls: list[str] = field(default_factory=list)

    def pay(self, action: str, amount_cents: int, ref: str) -> str:
        self.calls.append(ref)
        return "CPLT"


@dataclass
class Setup:
    day: ReplayDay
    store: Store
    gate: Gate
    tools: EnaTools


def setup(question: Question) -> Setup:
    """The month on the question's day, with its invoices paid before it is asked."""
    store = Store(":memory:")
    gate = Gate(MONTH.business, store, FakeBank(), lambda: NOW)
    day = ReplayDay(question.date)
    tools = EnaTools(Ledger(MONTH, store, day), store, gate, lambda: NOW)
    for invoice_id in question.paid_before:
        approval = tools.pay_invoice(invoice_id).approval
        if approval is not None:
            gate.approve(approval.id)
    return Setup(day, store, gate, tools)


def tool_figures(question: Question) -> set[int]:
    """Every euro figure the question's tools return on its day, in cents."""
    s = setup(question)
    results = [call_tool(s.tools, call.tool, dict(call.arguments)) for call in question.tools]
    s.store.close()
    return {cents(figure) for text in results for figure in figures_in(text)}


def ask(chat: Chat, text: str) -> list[ChatEvent]:
    async def collect() -> list[ChatEvent]:
        return [event async for event in chat.send(text)]

    return asyncio.run(collect())


def reply_problems(question: Question, reply: str, allowed: set[int]) -> list[str]:
    """What is wrong with a reply: a missing figure, a figure no tool gave, no "I don't know"."""
    said = figures_in(reply)
    problems = []
    if question.dont_know:
        if not DONT_KNOW.search(reply):
            problems.append("no 'I don't know'")
        if said:
            problems.append(f"a figure with no tool: {said}")
        return problems
    missing = [f for f in question.figures if cents(f) not in {cents(s) for s in said}]
    if missing:
        problems.append(f"missing {missing}")
    unsourced = [f for f in said if cents(f) not in allowed]
    if unsourced:
        problems.append(f"not from a tool {unsourced}")
    return problems


@dataclass
class RestatingAgent:
    """Calls the question's tools and restates the euro figures they returned, in order;
    with no tool to call it says "I don't know", in the owner's language."""

    tools: EnaTools
    calls: tuple[ToolCall, ...]

    async def run(self, prompt: str) -> AsyncIterator[str]:
        results = [call_tool(self.tools, call.tool, dict(call.arguments)) for call in self.calls]
        if not results:
            yield "Δεν ξέρω." if GREEK.search(prompt) else "I don't know."
            return
        said = dict.fromkeys(figure for text in results for figure in figures_in(text))
        yield " ".join(said)


@pytest.mark.parametrize(("question", "language"), CASES)
def test_the_tools_answer_each_question_on_its_day(question: Question, language: Language) -> None:
    s = setup(question)
    chat = Chat(s.tools, RestatingAgent(s.tools, question.tools), s.day, cache=None)
    events = ask(chat, question.text(language))
    s.store.close()
    done = events[-1]
    assert isinstance(done, DoneEvent), events
    assert done.unconfirmed is False
    kinds = [event.card_kind for event in events if isinstance(event, CardEvent)]
    assert kinds == list(question.cards)
    assert reply_problems(question, done.text, tool_figures(question)) == []


def test_the_reply_check_catches_a_wrong_or_invented_figure() -> None:
    lean = BY_ID["lean-week"]
    allowed = tool_figures(lean)
    assert reply_problems(lean, "The week closes at €349.69, below the €400.00.", allowed) == []
    assert reply_problems(lean, "The week closes at €349.69.", allowed) == ["missing ['€400.00']"]
    assert reply_problems(lean, "€349.69, €400.00 and about €350", allowed) == [
        "not from a tool ['€350']"
    ]
    vat = BY_ID["unknown-vat"]
    assert reply_problems(vat, "Δεν το ξέρω.", set()) == []
    assert reply_problems(vat, "About €900.", set()) == [
        "no 'I don't know'",
        "a figure with no tool: ['€900']",
    ]


def test_the_flour_question_moves_no_money() -> None:
    s = setup(BY_ID["pay-flour"])
    chat = Chat(s.tools, RestatingAgent(s.tools, BY_ID["pay-flour"].tools), s.day, cache=None)
    ask(chat, BY_ID["pay-flour"].en)
    assert s.store.receipts() == []
    s.store.close()


# The stage rehearsal: the real agent

LIVE = bool(os.environ.get("ANTHROPIC_API_KEY"))


@pytest.mark.skipif(
    not LIVE, reason="the stage rehearsal calls the real agent; set ANTHROPIC_API_KEY to run it"
)
@pytest.mark.parametrize(("question", "language"), CASES)
def test_the_real_agent_answers_each_question(question: Question, language: Language) -> None:
    s = setup(question)
    agent = ClaudeAgent(AgentSettings(), build_server(s.tools), s.day)
    events = ask(Chat(s.tools, agent, s.day, cache=None), question.text(language))
    s.store.close()
    errors = [event.message for event in events if isinstance(event, ErrorEvent)]
    assert errors == []
    done = events[-1]
    assert isinstance(done, DoneEvent), events
    assert done.unconfirmed is False, done.text
    kinds = {event.card_kind for event in events if isinstance(event, CardEvent)}
    assert set(question.cards) <= kinds, done.text
    if language == "el":
        assert GREEK.search(done.text), done.text
    problems = reply_problems(question, done.text, tool_figures(question))
    assert problems == [], json.dumps({"reply": done.text, "problems": problems})
