"""The chat over SSE, the owner's approve and decline, the receipt rule and the reply cache.

The agent is a scripted fake that calls the real tools and writes fixed text: no model,
no network, no API key.
"""

import asyncio
import datetime as dt
import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from fastapi.testclient import TestClient

from ena.advances import Advances
from ena.agent import (
    SECRET_ENV,
    SYSTEM_PROMPT,
    UNCONFIRMED,
    AgentError,
    AgentSettings,
    Chat,
    ClaudeAgent,
    claims_payment,
    confirm,
    system_prompt,
)
from ena.api import create_app
from ena.cache import CachedReply, ReplyCache, normalize
from ena.connectors.jcc import JccClient, JccSettings
from ena.gate import Gate
from ena.mcp_server import EnaTools, Ledger, ReplayDay, allowed_tools, build_server, call_tool
from ena.models import Actor, Receipt
from ena.replay import DayStarted
from ena.seed import BOOKED_EVENT_ID, LEAN_WEEK, SEED, synthetic_month
from ena.server import from_values
from ena.store import Store

MONTH = synthetic_month()
DAIRY, FLOUR = MONTH.invoices
NOW = dt.datetime(2026, 11, 16, 9, 0, tzinfo=dt.UTC)


@dataclass(frozen=True)
class Call:
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Say:
    text: str


@dataclass(frozen=True)
class Fail:
    reason: str


Step = Call | Say | Fail


@dataclass
class ScriptedAgent:
    """Plays its steps: a tool call runs the real tool, a text is yielded, a failure raises."""

    tools: EnaTools
    steps: list[Step] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)

    async def run(self, prompt: str) -> AsyncIterator[str]:
        self.prompts.append(prompt)
        for step in self.steps:
            match step:
                case Call():
                    call_tool(self.tools, step.tool, step.arguments)
                case Say():
                    yield step.text
                case Fail():
                    raise AgentError(step.reason)


@dataclass
class FakeBank:
    """A real, bocAPI-shaped bank: its payments are not simulated."""

    calls: list[str] = field(default_factory=list)
    simulated: bool = False

    def pay(self, action: str, amount_cents: int, ref: str) -> str:
        self.calls.append(ref)
        return "CPLT"


@dataclass
class Api:
    http: TestClient
    store: Store
    bank: FakeBank
    agent: ScriptedAgent
    day: ReplayDay

    def chat(self, text: str, *steps: Step) -> list[tuple[str, dict[str, Any]]]:
        self.agent.steps = list(steps)
        response = self.http.post("/chat", json={"text": text})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        return sse(response.text)


def sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def build(day: dt.date, cache: ReplyCache | None) -> tuple[Api, TestClient]:
    store = Store(":memory:")
    bank = FakeBank()
    gate = Gate(MONTH.business, store, bank, lambda: NOW)
    today = ReplayDay(day)
    tools = EnaTools(Ledger(MONTH, store, today), store, gate, lambda: NOW)
    agent = ScriptedAgent(tools)
    settings = JccSettings(
        api_user="ena-api", api_password="pw", callback_secret="cb-secret", _env_file=None
    )
    jcc = JccClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(_no_network)))
    app = create_app(
        store,
        jcc,
        settings.callback_secret,
        chat=Chat(tools, agent, today, cache),
        gate=gate,
        advances=Advances(store, gate, MONTH.events, lambda: NOW),
    )
    http = TestClient(app)
    return Api(http, store, bank, agent, today), http


def _no_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected call to {request.url}")


@pytest.fixture
def api() -> Iterator[Api]:
    ena, http = build(FLOUR.issue_date, cache=None)
    with http:
        yield ena
    ena.store.close()


def kinds(events: list[tuple[str, dict[str, Any]]]) -> list[str]:
    return [name for name, _ in events]


# The chat turn


def test_a_payment_within_the_limit_streams_its_receipt_card(api: Api) -> None:
    events = api.chat(
        "Pay the dairy invoice",
        Call("pay_invoice", {"invoice_id": DAIRY.id}),
        Say("Paid Kyrenia Dairy (synthetic) €180.00."),
    )
    assert kinds(events) == ["card", "text", "done"]
    card = events[0][1]
    assert card["card_kind"] == "receipt"
    assert card["card"]["result"] == "paid"
    assert card["card"]["ref"] == DAIRY.id
    assert card["card"]["simulated"] is False
    assert events[1][1]["text"] == "Paid Kyrenia Dairy (synthetic) €180.00."
    assert events[2][1] == {
        "kind": "done",
        "text": "Paid Kyrenia Dairy (synthetic) €180.00.",
        "unconfirmed": False,
        "cached": False,
    }
    assert api.bank.calls == [DAIRY.id]
    assert api.agent.prompts == ["Pay the dairy invoice"]


def test_paid_without_a_receipt_is_marked_unconfirmed(api: Api) -> None:
    events = api.chat("Did you pay the flour?", Say("Yes, I paid the flour invoice."))
    assert events[0][1]["text"] == UNCONFIRMED + "Yes, I paid the flour invoice."
    assert events[-1][1]["unconfirmed"] is True
    assert api.bank.calls == []


def test_a_listed_old_receipt_does_not_confirm_a_new_payment_claim(api: Api) -> None:
    paid_now = api.chat(
        "Pay the dairy invoice",
        Call("list_receipts", {"limit": 5}),
        Call("pay_invoice", {"invoice_id": DAIRY.id}),
        Say("Paid Kyrenia Dairy (synthetic) €180.00."),
    )
    assert paid_now[-1][1]["unconfirmed"] is False

    events = api.chat(
        "Did you pay the flour?",
        Call("list_receipts", {"limit": 5}),
        Say("Yes, I paid the flour invoice."),
    )
    assert kinds(events) == ["text", "done"]
    assert events[0][1]["text"] == UNCONFIRMED + "Yes, I paid the flour invoice."
    assert events[-1][1]["unconfirmed"] is True
    assert api.bank.calls == [DAIRY.id]


def test_a_past_payment_cited_by_its_listed_receipt_passes(api: Api) -> None:
    api.chat("Pay the dairy invoice", Call("pay_invoice", {"invoice_id": DAIRY.id}), Say("Paid."))
    cited = f"The dairy invoice was paid on 16 Nov, receipt receipt-{DAIRY.id}."
    events = api.chat("Was the dairy paid?", Call("list_receipts", {"limit": 5}), Say(cited))
    assert events[0][1]["text"] == cited
    assert events[-1][1]["unconfirmed"] is False

    # A citation backs its own sentence only, not a new claim beside it
    both = cited + " I paid the flour invoice too."
    events = api.chat("And the flour?", Call("list_receipts", {"limit": 5}), Say(both))
    assert events[-1][1]["unconfirmed"] is True
    # Without list_receipts this turn, the id alone confirms nothing
    events = api.chat("Was the dairy paid, really?", Say(cited))
    assert events[-1][1]["unconfirmed"] is True
    assert api.bank.calls == [DAIRY.id]


def test_a_payment_above_the_limit_is_an_approval_card_until_the_owner_taps(api: Api) -> None:
    events = api.chat(
        "Pay the flour invoice",
        Call("pay_invoice", {"invoice_id": FLOUR.id}),
        Say("The €1,240.00 flour invoice is not paid yet: it waits for your approval."),
    )
    assert kinds(events) == ["card", "text", "done"]
    approval = events[0][1]
    assert approval["card_kind"] == "approval"
    assert approval["card"]["status"] == "pending"
    assert events[-1][1]["unconfirmed"] is False
    assert api.bank.calls == []

    answer = api.http.post(f"/approvals/{approval['card']['id']}/approve")
    assert answer.status_code == 200
    receipt = Receipt.model_validate(answer.json()["card"])
    assert answer.json()["card_kind"] == "receipt"
    assert answer.json()["card"]["simulated"] is False
    assert (receipt.actor, receipt.result, receipt.ref) == (Actor.OWNER, "paid", FLOUR.id)
    assert api.bank.calls == [FLOUR.id]

    again = api.http.post(f"/approvals/{approval['card']['id']}/approve")
    assert again.status_code == 409
    assert api.bank.calls == [FLOUR.id]


def test_asked_again_to_pay_a_pending_invoice_the_same_approval_card_is_shown(api: Api) -> None:
    first = api.chat("Pay the flour invoice", Call("pay_invoice", {"invoice_id": FLOUR.id}))
    approval = first[0][1]["card"]

    again = api.chat(
        "Pay the flour invoice",
        Call("pay_invoice", {"invoice_id": FLOUR.id}),
        Say("It still waits for your approval."),
    )
    assert kinds(again) == ["card", "text", "done"]
    assert again[0][1]["card"] == approval

    recalled = api.chat(
        "Pay the flour invoice",
        Say("The flour invoice is already waiting for your approval in the card."),
    )
    assert kinds(recalled) == ["card", "text", "done"]
    assert (recalled[0][1]["card_kind"], recalled[0][1]["card"]) == ("approval", approval)
    assert api.bank.calls == []

    assert api.http.post(f"/approvals/{approval['id']}/approve").status_code == 200
    assert api.bank.calls == [FLOUR.id]
    after = api.chat("Pay the flour invoice", Say("It needs no approval: it is paid."))
    assert kinds(after) == ["text", "done"]


@pytest.mark.parametrize(
    "reply",
    [
        "Yes, you approved the dairy payment.",
        "Ναι, η πληρωμή εγκρίθηκε.",  # noqa: RUF001 - "Yes, the payment was approved."
    ],
)
def test_a_reply_about_a_past_approval_shows_no_pending_card(api: Api, reply: str) -> None:
    api.chat("Pay the flour invoice", Call("pay_invoice", {"invoice_id": FLOUR.id}))
    assert kinds(api.chat("Did I approve the dairy payment?", Say(reply))) == ["text", "done"]


@pytest.mark.parametrize(
    "reply",
    [
        "Pay it? It needs your approval first.",
        "Tap Approve on its card.",
        "Χρειάζεται την έγκρισή σας.",  # "It needs your approval."
        "Πατήστε για να την εγκρίνετε.",  # noqa: RUF001 - "Tap to approve it."
        "Εκκρεμούν οι εγκρίσεις σας.",  # noqa: RUF001 - "Your approvals are pending."
        "ΕΓΚΡΙΝΕΤΕ ΤΗΝ ΠΛΗΡΩΜΗ.",  # noqa: RUF001 - "APPROVE THE PAYMENT."
    ],
)
def test_a_reply_asking_for_an_approval_shows_the_pending_card(api: Api, reply: str) -> None:
    api.chat("Pay the flour invoice", Call("pay_invoice", {"invoice_id": FLOUR.id}))
    assert kinds(api.chat("Pay the flour invoice", Say(reply))) == ["card", "text", "done"]


def test_the_owner_declines_and_nothing_is_paid(api: Api) -> None:
    events = api.chat("Pay the flour invoice", Call("pay_invoice", {"invoice_id": FLOUR.id}))
    approval_id = events[0][1]["card"]["id"]
    answer = api.http.post(f"/approvals/{approval_id}/decline")
    assert answer.status_code == 200
    assert answer.json()["card"]["result"] == "declined"
    assert api.bank.calls == []
    assert api.http.post(f"/approvals/{approval_id}/approve").status_code == 409


def test_an_unknown_approval_is_refused(api: Api) -> None:
    assert api.http.post("/approvals/approval-nope/approve").status_code == 409
    assert api.http.post("/approvals/approval-nope/decline").status_code == 409


def test_the_owner_accepts_an_event_advance_on_its_card(api: Api) -> None:
    api.day.observe(DayStarted(at=NOW, day=LEAN_WEEK))
    events = api.chat(
        "Can I get an advance for the Georgiou wedding's costs?",
        Call("propose_event_advance", {"event_id": BOOKED_EVENT_ID}),
        Say("Offer, simulated: €2,800.00."),
    )
    offer = events[0][1]
    assert (offer["card_kind"], offer["card"]["kind"]) == ("offer", "event_advance")
    answer = api.http.post(f"/offers/{offer['card']['id']}/accept")
    assert answer.status_code == 200
    assert answer.json()["card_kind"] == "receipt"
    assert answer.json()["card"]["result"] == "accepted"
    assert answer.json()["card"]["actor"] == "owner"
    assert api.bank.calls == []
    assert api.http.post(f"/offers/{offer['card']['id']}/accept").status_code == 409
    assert api.http.post(f"/offers/{offer['card']['id']}/decline").status_code == 409


def test_an_unknown_offer_is_refused(api: Api) -> None:
    assert api.http.post("/offers/advance-nope/accept").status_code == 409
    assert api.http.post("/offers/advance-nope/decline").status_code == 409


def test_a_failed_agent_says_try_again_without_details(api: Api) -> None:
    events = api.chat("What's my balance?", Call("get_balance"), Fail("ProcessError"))
    assert kinds(events) == ["error"]
    assert events[0][1]["message"] == "Ena could not answer, try again: ProcessError"
    assert "cb-secret" not in json.dumps(events)


def test_a_reply_cut_off_by_a_late_failure_is_still_shown(api: Api) -> None:
    events = api.chat("What's my balance?", Say("Your balance is shown."), Fail("ProcessError"))
    assert kinds(events) == ["text", "error"]


def test_an_empty_reply_is_an_error(api: Api) -> None:
    events = api.chat("Hello", Call("get_balance"))
    assert kinds(events) == ["error"]


@pytest.mark.parametrize("body", [{"text": "   "}, {"text": "x" * 2_001}, {"message": "hi"}])
def test_a_malformed_message_is_refused(api: Api, body: dict[str, str]) -> None:
    assert api.http.post("/chat", json=body).status_code == 422
    assert api.agent.prompts == []


def test_the_jcc_callback_is_still_served(api: Api) -> None:
    answer = api.http.post(
        "/jcc/callback",
        content="a=1",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert answer.status_code == 403


# The receipt rule


@pytest.mark.parametrize(
    ("text", "claims"),
    [
        ("I paid the dairy invoice.", True),
        ("The payment was made this morning.", True),
        ("Πληρώθηκε το τιμολόγιο.", True),
        ("Το τιμολόγιο δεν πληρώθηκε ακόμα.", False),  # noqa: RUF001 - Greek is meant
        ("It is not paid yet: it waits for your approval.", False),
        ("You have two unpaid invoices.", False),
        ("I haven't paid it.", False),
        ("Your balance is €1,120.00.", False),
    ],
)
def test_claims_payment(text: str, claims: bool) -> None:
    assert claims_payment(text) is claims


def test_a_paid_receipt_confirms_a_claim() -> None:
    receipt = Receipt(
        id="receipt-x",
        at=NOW,
        actor=Actor.ENA,
        action="Pay",
        amount_cents=100,
        ref="x",
        result="paid",
        explanation="test",
    )
    failed = receipt.model_copy(update={"result": "failed"})
    assert confirm("Paid.", [receipt]) == ("Paid.", False)
    assert confirm("Paid.", [failed]) == (UNCONFIRMED + "Paid.", True)
    assert confirm("Paid.", []) == (UNCONFIRMED + "Paid.", True)
    # A read receipt backs only a sentence citing its id, and only when it was paid
    assert confirm("Paid, receipt-x.", [], [receipt]) == ("Paid, receipt-x.", False)
    assert confirm("Paid.", [], [receipt]) == (UNCONFIRMED + "Paid.", True)
    assert confirm("Paid, receipt-x.", [], [failed]) == (UNCONFIRMED + "Paid, receipt-x.", True)


# The reply cache


def test_a_read_only_reply_is_cached_by_question_and_day(tmp_path: Path) -> None:
    path = tmp_path / "replies.json"
    ena, http = build(LEAN_WEEK, ReplyCache(SEED, path))
    with http:
        first = ena.chat(
            "Will I be short this month?", Call("get_forecast"), Say("The week of 16 Nov is lean.")
        )
        assert kinds(first) == ["card", "text", "done"]
        again = ena.chat("will i be  SHORT this month", Say("not used"))
        assert [e for e in again if e[0] != "done"] == [e for e in first if e[0] != "done"]
        assert again[-1][1]["cached"] is True
        assert len(ena.agent.prompts) == 1
    state = ena.agent.tools.state()
    question = "Will I be short this month?"
    stored = ReplyCache(SEED, path).get(question, LEAN_WEEK, state)
    assert stored is not None
    assert stored.text == "The week of 16 Nov is lean."
    assert ReplyCache(SEED, path).get(question, MONTH.last_day, state) is None
    assert ReplyCache(SEED + 1, path).get(question, LEAN_WEEK, state) is None
    assert ReplyCache(SEED, path).get(question, LEAN_WEEK, "another state") is None
    ena.store.close()


def test_a_cached_reply_is_not_replayed_after_money_moved_the_same_day() -> None:
    ena, http = build(FLOUR.issue_date, ReplyCache(SEED))
    with http:
        ena.chat("What's my balance?", Call("get_balance"), Say("The balance before."))
        ena.chat("Pay the dairy", Call("pay_invoice", {"invoice_id": DAIRY.id}), Say("Paid."))
        after = ena.chat("What's my balance?", Call("get_balance"), Say("The balance after."))
        assert after[-1][1] == {
            "kind": "done",
            "text": "The balance after.",
            "unconfirmed": False,
            "cached": False,
        }
        again = ena.chat("What's my balance?", Say("not used"))
        assert again[-1][1]["text"] == "The balance after."
        assert again[-1][1]["cached"] is True
    ena.store.close()


def test_a_loan_offer_is_not_cached_so_the_stage_run_writes_its_receipt(tmp_path: Path) -> None:
    path = tmp_path / "replies.json"
    rehearsal, http = build(LEAN_WEEK, ReplyCache(SEED, path))
    with http:
        rehearsal.chat("Can I get a loan?", Call("propose_loan"), Say("Offer, simulated."))
    rehearsal.store.close()
    stage, http = build(LEAN_WEEK, ReplyCache(SEED, path))
    with http:
        stage.chat("Can I get a loan?", Call("propose_loan"), Say("Offer, simulated."))
        assert [receipt.result for receipt in stage.store.receipts()] == ["offered"]
    stage.store.close()


def test_a_reply_that_moved_money_or_was_marked_is_not_cached(tmp_path: Path) -> None:
    cache = ReplyCache(SEED)
    ena, http = build(FLOUR.issue_date, cache)
    with http:
        before = ena.agent.tools.state()
        ena.chat(
            "Pay the dairy invoice", Call("pay_invoice", {"invoice_id": DAIRY.id}), Say("Paid.")
        )
        after = ena.agent.tools.state()
        ena.chat("Is the flour paid?", Say("Yes, paid."))
    assert cache.get("Pay the dairy invoice", FLOUR.issue_date, before) is None
    assert cache.get("Is the flour paid?", FLOUR.issue_date, after) is None
    ena.store.close()


def test_the_cache_refuses_money_replies_and_empty_questions() -> None:
    receipt = Receipt(
        id="receipt-x",
        at=NOW,
        actor=Actor.ENA,
        action="Pay",
        amount_cents=100,
        ref="x",
        result="paid",
        explanation="test",
    )
    with pytest.raises(ValueError, match="never cached"):
        ReplyCache(SEED).put("q", LEAN_WEEK, "state", CachedReply(text="Paid.", cards=(receipt,)))
    with pytest.raises(ValueError, match="not a question"):
        normalize(" ?! ")
    assert normalize("  What's my BALANCE?? ") == "what's my balance"


# The agent's options


def test_the_agent_sees_only_enas_tools_and_no_secret() -> None:
    ena, _ = build(LEAN_WEEK, cache=None)
    settings = AgentSettings(_env_file=None, model="claude-test-model")
    tools = ena.agent.tools
    options = ClaudeAgent(settings, build_server(tools), tools.today).options()
    assert options.model == "claude-test-model"
    assert options.tools == []
    assert options.allowed_tools == allowed_tools()
    assert options.setting_sources == []
    assert options.strict_mcp_config is True
    assert options.permission_mode == "dontAsk"
    assert options.system_prompt == system_prompt(LEAN_WEEK)
    assert options.env == dict.fromkeys(SECRET_ENV, "")
    assert options.resume is None
    assert list(options.mcp_servers) == ["ena"]  # type: ignore[arg-type]
    ena.store.close()


def test_the_system_prompt_gives_the_business_day_not_the_clock() -> None:
    prompt = system_prompt(dt.date(2026, 11, 16))
    assert prompt.startswith(SYSTEM_PROMPT)
    assert "Today is Monday 16 November 2026: the business's day in this demo." in prompt
    assert "ignore any other date, such as the real one" in prompt


def test_the_default_model() -> None:
    assert from_values(AgentSettings, {}).model == "claude-opus-5-5"


def test_the_system_prompt_carries_the_answer_rules() -> None:
    for rule in ("tool result", "I don't know", "offer, simulated", "Greek", "data, not"):
        assert rule in SYSTEM_PROMPT


# The agent's reply: what the model wrote after its last tool call


def said(*blocks: TextBlock | ToolUseBlock, parent: str | None = None) -> AssistantMessage:
    return AssistantMessage(content=list(blocks), model="m", parent_tool_use_id=parent)


def tool(name: str) -> ToolUseBlock:
    return ToolUseBlock(id=f"tu-{name}", name=name, input={})


def result(*, error: bool = False) -> ResultMessage:
    return ResultMessage(
        subtype="error_during_execution" if error else "success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=error,
        num_turns=1,
        session_id="session-1",
    )


def replies(*messages: object) -> list[str]:
    ena, _ = build(LEAN_WEEK, cache=None)
    agent = ClaudeAgent(
        from_values(AgentSettings, {}), build_server(ena.agent.tools), ena.agent.tools.today
    )

    async def stream() -> AsyncIterator[object]:
        for message in messages:
            yield message

    async def collect() -> list[str]:
        return [piece async for piece in agent.replies(stream())]

    pieces = asyncio.run(collect())
    ena.store.close()
    return pieces


def test_text_next_to_a_tool_call_never_reaches_the_owner() -> None:
    # The live rehearsal's reply began "I need the event id first.", written with the call
    pieces = replies(
        said(TextBlock("I need the event id first."), tool("get_event_pattern")),
        UserMessage(content=[ToolResultBlock(tool_use_id="tu-get_event_pattern")]),
        said(TextBlock("Yes: €2,800.00 for 16 days, simulated.")),
        result(),
    )
    assert pieces == ["Yes: €2,800.00 for 16 days, simulated."]


def test_text_sent_apart_from_its_tool_call_is_dropped_too() -> None:
    # The SDK may send the text block and its tool call as two messages
    pieces = replies(
        said(TextBlock("Let me check the forecast.")),
        said(tool("get_forecast")),
        said(TextBlock("The week of 16 Nov is lean.")),
        said(TextBlock("It closes at €349.69.")),
        result(),
    )
    assert pieces == ["The week of 16 Nov is lean.", "It closes at €349.69."]


def test_a_reply_without_tools_and_a_subagents_text() -> None:
    pieces = replies(
        said(TextBlock("I don't know."), parent=None),
        said(TextBlock("sub-agent notes"), parent="tu-x"),
        said(TextBlock("   ")),
        result(),
    )
    assert pieces == ["I don't know."]


def test_the_stream_is_read_to_its_end_after_the_result() -> None:
    # A message after the result (a follow-up turn) is read, never sent to the owner
    seen: list[object] = []
    messages = [
        said(TextBlock("The reply.")),
        result(),
        said(TextBlock("A later turn's text.")),
    ]

    class Recording:
        def __aiter__(self) -> AsyncIterator[object]:
            return self.stream()

        async def stream(self) -> AsyncIterator[object]:
            for message in messages:
                seen.append(message)
                yield message

    ena, _ = build(LEAN_WEEK, cache=None)
    agent = ClaudeAgent(
        from_values(AgentSettings, {}), build_server(ena.agent.tools), ena.agent.tools.today
    )

    async def collect() -> list[str]:
        return [piece async for piece in agent.replies(Recording())]

    assert asyncio.run(collect()) == ["The reply."]
    assert seen == messages
    ena.store.close()


def test_a_turn_with_no_result_says_nothing_and_raises() -> None:
    with pytest.raises(AgentError, match="without a result"):
        replies(said(TextBlock("Half an answer")))


def test_a_failed_turn_says_nothing_and_raises() -> None:
    with pytest.raises(AgentError, match="error_during_execution"):
        replies(said(TextBlock("Half an answer")), result(error=True))
