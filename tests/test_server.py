"""`ena-serve`'s app, built from a settings object: the chat through a scripted fake, the
error event without a model credential, and the callback refused without JCC settings.

No model, no network, no port: the app is driven through `TestClient`.
"""

import datetime as dt
import json
import logging
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ena.agent import AgentRunner, AgentSettings
from ena.connectors.jcc import JccSettings
from ena.gate import PaymentBank
from ena.mcp_server import EnaTools, call_tool
from ena.seed import FIRST_DAY, LAST_DAY, LEAN_WEEK, synthetic_month
from ena.server import (
    DEFAULT_DAY,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_STORE,
    NO_CREDENTIAL,
    NO_JCC,
    SIMULATED_PAYMENTS,
    Server,
    ServeSettings,
    build,
    has_model_credential,
    load_settings,
    parser,
)

FORM = {"Content-Type": "application/x-www-form-urlencoded"}


@dataclass
class ScriptedAgent:
    """Reads the balance through the real tool, then says it."""

    tools: EnaTools
    prompts: list[str] = field(default_factory=list)

    async def run(self, prompt: str) -> AsyncIterator[str]:
        self.prompts.append(prompt)
        balance = json.loads(call_tool(self.tools, "get_balance", {}))
        yield f"Your balance is {balance['balance']} on {balance['as_of']}."


@dataclass
class Agents:
    made: list[ScriptedAgent] = field(default_factory=list)

    def __call__(self, tools: EnaTools) -> AgentRunner:
        agent = ScriptedAgent(tools)
        self.made.append(agent)
        return agent


def _no_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected call to {request.url}")


def settings(**changes: Any) -> ServeSettings:
    base: dict[str, Any] = {
        "store": None,
        "day": LEAN_WEEK,
        "cache": None,
        "agent": AgentSettings(_env_file=None),
        "jcc": None,
        "model_credential": False,
    }
    return ServeSettings(**(base | changes))


def sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        events.append((fields["event"], json.loads(fields["data"])))
    return events


@pytest.fixture
def served() -> Iterator[tuple[Server, TestClient, Agents]]:
    agents = Agents()
    server = build(settings(), runner=agents)
    with TestClient(server.app) as http:
        yield server, http, agents
    server.close()


def test_the_chat_runs_the_agent_on_the_month_as_of_the_day(
    served: tuple[Server, TestClient, Agents],
) -> None:
    _, http, agents = served
    response = http.post("/chat", json={"text": "What's my balance?"})
    assert response.status_code == 200
    events = sse(response.text)
    assert [name for name, _ in events] == ["text", "done"]
    assert events[-1][1]["text"].endswith(f"on {LEAN_WEEK}.")
    assert [agent.prompts for agent in agents.made] == [["What's my balance?"]]


def test_without_a_model_credential_the_chat_answers_with_an_error_event() -> None:
    server = build(settings())
    with TestClient(server.app) as http:
        response = http.post("/chat", json={"text": "What's my balance?"})
    server.close()
    assert response.status_code == 200
    events = sse(response.text)
    assert [name for name, _ in events] == ["error"]
    assert NO_CREDENTIAL in events[0][1]["message"]


def test_without_jcc_settings_every_callback_is_refused(
    served: tuple[Server, TestClient, Agents],
) -> None:
    server, http, _ = served
    for path in ("/jcc/callback", "/jcc/callback/"):
        answer = http.post(path, content="a=1", headers=FORM, follow_redirects=False)
        assert answer.status_code == 503
        assert answer.json() == {"detail": NO_JCC}
    assert server.store.card_sales() == []


def test_with_jcc_settings_the_callback_checks_its_checksum() -> None:
    jcc = JccSettings(
        api_user="ena-api", api_password="pw", callback_secret="cb-secret", _env_file=None
    )
    server = build(
        settings(jcc=jcc), runner=Agents(), jcc_transport=httpx.MockTransport(_no_network)
    )
    with TestClient(server.app) as http:
        answer = http.post("/jcc/callback", content="a=1", headers=FORM)
    server.close()
    assert answer.status_code == 403


def test_the_dashboard_reads_the_month_as_of_the_day(
    served: tuple[Server, TestClient, Agents],
) -> None:
    _, http, _ = served
    overview = http.get("/dashboard/overview")
    assert overview.status_code == 200
    assert overview.json()["as_of"] == DEFAULT_DAY.isoformat()
    for route in ("receipts", "forecast", "credit"):
        assert http.get(f"/dashboard/{route}").status_code == 200


def test_the_owner_taps_reach_the_gate(served: tuple[Server, TestClient, Agents]) -> None:
    _, http, _ = served
    assert http.post("/approvals/approval-nope/approve").status_code == 409


def test_the_stand_in_bank_says_payments_are_simulated(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="ena.server"):
        build(settings(), runner=Agents()).close()
    assert SIMULATED_PAYMENTS in caplog.messages


DAIRY, FLOUR = synthetic_month().invoices


@dataclass
class Payer:
    """Pays the invoice whose id is the prompt, through the real tool."""

    tools: EnaTools

    async def run(self, prompt: str) -> AsyncIterator[str]:
        call_tool(self.tools, "pay_invoice", {"invoice_id": prompt})
        yield "Done."


@dataclass
class RealBank:
    """A bocAPI-shaped bank: its payments are not simulated."""

    simulated: bool = False

    def pay(self, action: str, amount_cents: int, ref: str) -> str:
        return "CPLT"


def _receipt_cards(bank: PaymentBank | None) -> list[dict[str, Any]]:
    """The receipt cards the owner sees: the dairy paid in the chat, the flour approved."""
    server = build(settings(), runner=Payer, bank=bank)
    with TestClient(server.app) as http:
        paid = sse(http.post("/chat", json={"text": DAIRY.id}).text)
        asked = sse(http.post("/chat", json={"text": FLOUR.id}).text)
        approved = http.post(f"/approvals/{asked[0][1]['card']['id']}/approve").json()
    server.close()
    assert [paid[0][1]["card_kind"], approved["card_kind"]] == ["receipt", "receipt"]
    return [paid[0][1]["card"], approved["card"]]


def test_a_stand_in_payment_shows_as_simulated_in_the_chat() -> None:
    cards = _receipt_cards(None)

    assert [(card["result"], card["simulated"]) for card in cards] == [("paid", True)] * 2
    assert all("simulated" in card["explanation"] for card in cards)


def test_a_payment_through_a_real_bank_does_not_show_as_simulated() -> None:
    cards = _receipt_cards(RealBank())

    assert [(card["result"], card["simulated"]) for card in cards] == [("paid", False)] * 2
    assert not any("simulated" in card["explanation"] for card in cards)


def test_the_store_file_is_created_under_its_folder(tmp_path: Path) -> None:
    path = tmp_path / "data" / "ena.db"
    server = build(settings(store=path), runner=Agents())
    server.close()
    assert path.exists()


# The flags and the settings


def test_the_defaults() -> None:
    args = parser().parse_args([])
    assert (args.host, args.port, args.day) == (DEFAULT_HOST, DEFAULT_PORT, DEFAULT_DAY)
    assert (args.store, args.memory, args.cache) == (DEFAULT_STORE, False, None)
    assert (DEFAULT_HOST, DEFAULT_PORT, DEFAULT_DAY) == ("127.0.0.1", 8000, dt.date(2026, 11, 16))


def test_store_and_memory_exclude_each_other() -> None:
    with pytest.raises(SystemExit):
        parser().parse_args(["--memory", "--store", "x.db"])


@pytest.mark.parametrize("day", [FIRST_DAY - dt.timedelta(days=1), LAST_DAY + dt.timedelta(days=1)])
def test_a_day_outside_the_month_is_refused(day: dt.date, tmp_path: Path) -> None:
    args = parser().parse_args(["--memory", "--day", day.isoformat()])
    with pytest.raises(SystemExit, match="the day must be from"):
        load_settings(args, {}, tmp_path / "missing.env")


def test_settings_from_an_env_file(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("ENA_MODEL=claude-test\nJCC_API_USER=\nJCC_CALLBACK_SECRET=\n", encoding="utf-8")
    loaded = load_settings(parser().parse_args(["--memory"]), {}, env)
    assert (loaded.store, loaded.jcc, loaded.model_credential) == (None, None, False)
    assert loaded.agent.model == "claude-test"

    env.write_text(
        "JCC_API_USER=ena-api\nJCC_API_PASSWORD=pw\nJCC_CALLBACK_SECRET=cb\n", encoding="utf-8"
    )
    keyed = load_settings(parser().parse_args([]), {"ANTHROPIC_API_KEY": "sk-test"}, env)
    assert keyed.jcc is not None
    assert keyed.jcc.api_user == "ena-api"
    assert keyed.model_credential is True
    assert keyed.store == DEFAULT_STORE


def test_a_partial_jcc_section_fails_at_start(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("JCC_CALLBACK_SECRET=cb\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="JccSettings api_user"):
        load_settings(parser().parse_args([]), {}, env)


@pytest.mark.parametrize(
    ("environ", "found"),
    [
        ({}, False),
        ({"ANTHROPIC_API_KEY": " "}, False),
        ({"ANTHROPIC_API_KEY": "sk-test"}, True),
        ({"CLAUDE_CODE_OAUTH_TOKEN": "token"}, True),
    ],
)
def test_the_model_credential(environ: dict[str, str], found: bool) -> None:
    assert has_model_credential(environ) is found
