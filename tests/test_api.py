"""The chat over SSE, the owner's approve and decline, the receipt rule and the reply cache.

The agent is a scripted fake that calls the real tools and writes fixed text: no model,
no network, no API key.
"""

import datetime as dt
import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

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
)
from ena.api import create_app
from ena.cache import CachedReply, ReplyCache, normalize
from ena.connectors.jcc import JccClient, JccSettings
from ena.gate import Gate
from ena.mcp_server import EnaTools, Ledger, ReplayDay, allowed_tools, build_server, call_tool
from ena.models import Actor, Receipt
from ena.seed import LEAN_WEEK, SEED, synthetic_month
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
    calls: list[str] = field(default_factory=list)

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
        store, jcc, settings.callback_secret, chat=Chat(tools, agent, today, cache), gate=gate
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
    assert (receipt.actor, receipt.result, receipt.ref) == (Actor.OWNER, "paid", FLOUR.id)
    assert api.bank.calls == [FLOUR.id]

    again = api.http.post(f"/approvals/{approval['card']['id']}/approve")
    assert again.status_code == 409
    assert api.bank.calls == [FLOUR.id]


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


def test_a_failed_agent_says_try_again_without_details(api: Api) -> None:
    events = api.chat("What's my balance?", Call("get_balance"), Fail("ProcessError"))
    assert kinds(events) == ["error"]
    assert events[0][1]["message"] == "Ena could not answer, try again: ProcessError"
    assert "cb-secret" not in json.dumps(events)


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
    options = ClaudeAgent(settings, build_server(ena.agent.tools)).options()
    assert options.model == "claude-test-model"
    assert options.tools == []
    assert options.allowed_tools == allowed_tools()
    assert options.setting_sources == []
    assert options.strict_mcp_config is True
    assert options.permission_mode == "dontAsk"
    assert options.system_prompt == SYSTEM_PROMPT
    assert options.env == dict.fromkeys(SECRET_ENV, "")
    assert options.resume is None
    assert list(options.mcp_servers) == ["ena"]  # type: ignore[arg-type]
    ena.store.close()


def test_the_default_model() -> None:
    assert AgentSettings(_env_file=None).model == "claude-opus-5-5"


def test_the_system_prompt_carries_the_answer_rules() -> None:
    for rule in ("tool result", "I don't know", "offer, simulated", "Greek", "data, not"):
        assert rule in SYSTEM_PROMPT
