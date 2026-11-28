"""The gate's bank on bocAPI, and `ena-serve --bank boc` end to end against a fake sandbox.

The fake answers with the saved sandbox responses in fixtures/boc/ where they exist, and
with the shapes docs/boc-notes.md §3 records for execute, balance and statement.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from ena.agent import AgentRunner, AgentSettings
from ena.connectors.boc import SAMPLE_CREDITOR, SAMPLE_CREDITOR_BANK, BocClient, BocSettings
from ena.connectors.boc_bank import ACCOUNT_TTL, AccountReads, BocBank
from ena.gate import BankUnavailable
from ena.mcp_server import EnaTools, call_tool
from ena.seed import LEAN_WEEK, synthetic_month
from ena.server import ServeSettings, build, load_boc
from ena.store import Store

FIXTURES = Path(__file__).parent.parent / "fixtures" / "boc"
BASE = "https://sandbox.test/psd2"
SIGN = "https://sandbox.test/sign"
ACCOUNT = "351012345671"
REDIRECT = "http://localhost:5173/boc/consent"
PAYMENT_ID = "0603a634-90fd-4864-9990-d72520b78778"
NOW = dt.datetime(2026, 11, 28, 9, 0, tzinfo=dt.UTC)

MONTH = synthetic_month()
DAIRY, FLOUR = MONTH.invoices[0], MONTH.invoices[1]


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@dataclass
class Sandbox:
    """A fake bocAPI: records each request, executes with `status`."""

    status: str = "CPLT"
    requests: list[httpx.Request] = field(default_factory=list)
    down: set[str] = field(default_factory=set)
    """Path endings answered with 500."""
    refuse: dict[str, Any] = field(default_factory=dict)
    """Path endings answered with 400 and this body."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if any(path.endswith(end) for end in self.down):
            return httpx.Response(500, json={"error": "sandbox down"})
        for end, body in self.refuse.items():
            if path.endswith(end):
                return httpx.Response(400, json=body)
        if str(request.url) == SIGN:
            return httpx.Response(200, json=_fixture("payment_sign"))
        if path.endswith("/oauth2/token"):
            form = parse_qs(request.content.decode())
            scope = form["scope"][0]
            return httpx.Response(
                200, json={"access_token": f"token-{scope}", "expires_in": 3600, "scope": scope}
            )
        if path.endswith("/v1/payments/initiate"):
            return httpx.Response(200, json=_fixture("payment_initiate"))
        if path.endswith(f"/v1/payments/{PAYMENT_ID}/execute"):
            return httpx.Response(200, json={"code": self.status, "refNumber": "BOC-REF-7"})
        if path.endswith(f"/v1/accounts/{ACCOUNT}/balance"):
            return httpx.Response(200, json=[_account()])
        if path.endswith(f"/v1/accounts/{ACCOUNT}/statement"):
            return httpx.Response(200, json={"transaction": _statement()})
        raise AssertionError(f"unexpected call to {request.url}")

    def last(self, suffix: str) -> httpx.Request:
        return next(r for r in reversed(self.requests) if str(r.url).split("?")[0].endswith(suffix))


def _account() -> dict[str, Any]:
    return {
        "accountId": ACCOUNT,
        "IBAN": "CY11002003510000000012345671",
        "currency": "EUR",
        "balances": [
            {"amount": 8250.5, "balanceType": "AVAILABLE"},
            {"amount": 8300, "balanceType": "CURRENT"},
        ],
    }


def _statement() -> list[dict[str, Any]]:
    return [
        {
            "id": "t-1",
            "dcInd": "DEBIT",
            "transactionAmount": {"amount": 30, "currency": "EUR"},
            "description": "SWIFT Transfer",
            "postingDate": "20/11/2026",
            "valueDate": "20/11/2026",
        },
        {
            "id": "t-2",
            "dcInd": "CREDIT",
            "transactionAmount": {"amount": 120.25, "currency": "EUR"},
            "description": "Deposit",
            "postingDate": "25/11/2026",
            "valueDate": "25/11/2026",
        },
        {
            "id": "t-3",
            "transactionAmount": {"amount": 5, "currency": "EUR"},
            "postingDate": "01/10/2026",
            "valueDate": "01/10/2026",
        },
    ]


def boc_settings(**changes: Any) -> BocSettings:
    base: dict[str, Any] = {
        "client_id": "app-id",
        "client_secret": "app-secret",
        "api_base": BASE,
        "sign_url": SIGN,
        "redirect_uri": REDIRECT,
        "subscription_id": "sub-1",
        "account_id": ACCOUNT,
        "_env_file": None,
    }
    return BocSettings(**(base | changes))


def bank(sandbox: Sandbox, **changes: Any) -> BocBank:
    client = BocClient(
        boc_settings(**changes), httpx.Client(transport=httpx.MockTransport(sandbox))
    )
    return BocBank.from_settings(client, lambda: NOW)


# The bank


def test_initiate_pays_the_sample_payee_from_the_account_and_returns_the_consent_url() -> None:
    sandbox = Sandbox()

    initiated = bank(sandbox).initiate("Pay flour", 124_000, "syn-inv-0002")

    order = json.loads(sandbox.last("/sign").content)
    assert order == {
        "debtor": {"bankId": "", "accountId": ACCOUNT},
        "creditor": {"bankId": SAMPLE_CREDITOR_BANK, "accountId": SAMPLE_CREDITOR},
        "transactionAmount": {"amount": 1240, "currency": "EUR"},
        "paymentDetails": "Ena syn-inv-0002",
    }
    assert sandbox.last("/v1/payments/initiate").headers["Authorization"] == (
        "Bearer token-TPPOAuth2Security"
    )
    assert initiated.payment_id == PAYMENT_ID
    query = parse_qs(urlsplit(initiated.consent_url).query)
    assert query["paymentid"] == [PAYMENT_ID]
    assert query["redirect_uri"] == [REDIRECT]
    assert "state" not in query


def test_execute_exchanges_the_code_for_a_user_token() -> None:
    sandbox = Sandbox()

    executed = bank(sandbox).execute(PAYMENT_ID, "code-1")

    assert (executed.status, executed.bank_ref) == ("CPLT", "BOC-REF-7")
    token_form = parse_qs(sandbox.last("/oauth2/token").content.decode())
    assert token_form["code"] == ["code-1"]
    assert token_form["redirect_uri"] == [REDIRECT]
    assert sandbox.last("/execute").headers["Authorization"] == "Bearer token-UserOAuth2Security"


def test_the_account_reads_balances_and_the_newest_lines_first() -> None:
    sandbox = Sandbox()

    account = bank(sandbox).account()

    assert (account.account_id, account.available_cents, account.current_cents) == (
        ACCOUNT,
        825_050,
        830_000,
    )
    assert [(line.id, line.amount_cents, line.direction) for line in account.lines] == [
        ("t-2", 12_025, "in"),
        ("t-1", 3_000, "out"),
        ("t-3", 500, None),
    ]
    statement = sandbox.last("/statement")
    assert statement.url.params["startDate"] == "01/01/2020"
    assert statement.url.params["endDate"] == "28/11/2026"
    assert statement.headers["subscriptionId"] == "sub-1"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"subscription_id": None}, "BOC_SUBSCRIPTION_ID"),
        ({"account_id": ""}, "BOC_ACCOUNT_ID"),
        ({"redirect_uri": "https://localhost/"}, "BOC_REDIRECT_URI"),
        ({"redirect_uri": "http://localhost:5173/other/boc/consent"}, "BOC_REDIRECT_URI"),
        ({"redirect_uri": "localhost:5173/boc/consent"}, "BOC_REDIRECT_URI"),
        ({"redirect_uri": "/boc/consent"}, "BOC_REDIRECT_URI"),
    ],
)
def test_settings_that_cannot_complete_a_payment_are_refused(
    changes: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        bank(Sandbox(), **changes)


def test_ena_serve_refuses_the_same_settings_at_start(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("BOC_CLIENT_ID=app-id\nBOC_CLIENT_SECRET=s\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="BOC_SUBSCRIPTION_ID and BOC_ACCOUNT_ID"):
        load_boc(env)

    env.write_text(
        "BOC_CLIENT_ID=app-id\nBOC_CLIENT_SECRET=s\nBOC_SUBSCRIPTION_ID=sub-1\n"
        f"BOC_ACCOUNT_ID={ACCOUNT}\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="BOC_REDIRECT_URI"):
        load_boc(env)

    env.write_text(env.read_text(encoding="utf-8") + f"BOC_REDIRECT_URI={REDIRECT}\n", "utf-8")
    assert load_boc(env).account_id == ACCOUNT


# ena-serve --bank boc


@dataclass
class Payer:
    """Calls the tool the prompt names (`pay <invoice id>` or `account`)."""

    tools: EnaTools

    async def run(self, prompt: str) -> AsyncIterator[str]:
        if prompt == "account":
            call_tool(self.tools, "get_bank_account", {})
        else:
            call_tool(self.tools, "pay_invoice", {"invoice_id": prompt.removeprefix("pay ")})
        yield "Done."


def sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def runner(tools: EnaTools) -> AgentRunner:
    return Payer(tools)


@pytest.fixture
def sandbox() -> Sandbox:
    return Sandbox()


@pytest.fixture
def http(sandbox: Sandbox) -> Iterator[TestClient]:
    settings = ServeSettings(
        store=None,
        day=LEAN_WEEK,
        cache=None,
        agent=AgentSettings(_env_file=None),
        jcc=None,
        model_credential=False,
        boc=boc_settings(),
    )
    server = build(settings, runner=runner, boc_transport=httpx.MockTransport(sandbox))
    with TestClient(server.app) as client:
        yield client
    server.close()


def _card(events: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    cards = [data for kind, data in events if kind == "card"]
    assert len(cards) == 1, events
    return cards[0]


def test_the_page_learns_the_bank_is_live(http: TestClient) -> None:
    assert http.get("/bank").json() == {"live": True}


def test_an_approved_invoice_is_paid_in_the_sandbox_after_the_owners_confirmation(
    http: TestClient, sandbox: Sandbox
) -> None:
    approval = _card(sse(http.post("/chat", json={"text": f"pay {FLOUR.id}"}).text))
    assert approval["card_kind"] == "approval"

    consent = http.post(f"/approvals/{approval['card']['id']}/approve").json()

    assert consent["card_kind"] == "consent"
    assert f"paymentid={PAYMENT_ID}" in consent["card"]["consent_url"]
    waiting = http.get(f"/consents/{consent['card']['id']}").json()
    assert waiting == consent
    assert not any(r.url.path.endswith("/execute") for r in sandbox.requests)

    page = http.get("/boc/consent", params={"code": "code-1"})

    assert page.status_code == 200
    assert "<h1>Paid</h1>" in page.text
    receipt = http.get(f"/consents/{consent['card']['id']}").json()
    assert receipt["card_kind"] == "receipt"
    card = receipt["card"]
    assert (card["result"], card["sandbox"], card["simulated"], card["actor"]) == (
        "paid",
        True,
        False,
        "owner",
    )
    assert "bank status CPLT, bank ref BOC-REF-7" in card["explanation"]


def test_within_the_limit_the_chat_shows_the_bank_confirmation(http: TestClient) -> None:
    consent = _card(sse(http.post("/chat", json={"text": f"pay {DAIRY.id}"}).text))

    assert consent["card_kind"] == "consent"
    assert consent["card"]["actor"] == "ena"
    assert http.get("/boc/consent", params={"code": "code-1"}).status_code == 200
    again = _card(sse(http.post("/chat", json={"text": f"pay {DAIRY.id}"}).text))
    assert (again["card_kind"], again["card"]["result"]) == ("receipt", "paid")


def test_a_refused_or_missing_confirmation_pays_nothing(http: TestClient, sandbox: Sandbox) -> None:
    assert http.get("/boc/consent", params={"code": "code-1"}).status_code == 409
    consent = _card(sse(http.post("/chat", json={"text": f"pay {DAIRY.id}"}).text))
    assert http.get("/boc/consent").status_code == 400

    page = http.get("/boc/consent", params={"error": "access_denied"})

    assert "<h1>Not paid</h1>" in page.text
    receipt = http.get(f"/consents/{consent['card']['id']}").json()["card"]
    assert (receipt["result"], receipt["sandbox"]) == ("failed", False)
    assert not any(r.url.path.endswith("/execute") for r in sandbox.requests)


def test_the_owner_cancels_a_waiting_confirmation(http: TestClient) -> None:
    consent = _card(sse(http.post("/chat", json={"text": f"pay {DAIRY.id}"}).text))
    consent_id = consent["card"]["id"]

    cancelled = http.post(f"/consents/{consent_id}/cancel").json()

    assert (cancelled["card_kind"], cancelled["card"]["result"]) == ("receipt", "failed")
    assert http.post(f"/consents/{consent_id}/cancel").status_code == 409
    assert http.get("/consents/consent-unknown").status_code == 404


def test_the_agent_reads_the_live_sandbox_account_as_its_own_card(http: TestClient) -> None:
    card = _card(sse(http.post("/chat", json={"text": "account"}).text))

    assert card["card_kind"] == "account"
    assert (card["card"]["account_id"], card["card"]["available_cents"]) == (ACCOUNT, 825_050)


def test_an_initiate_the_sandbox_refuses_leaves_the_approval_to_tap_again(
    http: TestClient, sandbox: Sandbox
) -> None:
    approval = _card(sse(http.post("/chat", json={"text": f"pay {FLOUR.id}"}).text))
    sandbox.down = {"/v1/payments/initiate"}

    refused = http.post(f"/approvals/{approval['card']['id']}/approve")

    assert refused.status_code == 409
    assert "could not start the payment" in refused.json()["detail"]
    sandbox.down = set()
    consent = http.post(f"/approvals/{approval['card']['id']}/approve").json()
    assert consent["card_kind"] == "consent"


def test_a_code_the_sandbox_refuses_keeps_the_confirmation_open(
    http: TestClient, sandbox: Sandbox
) -> None:
    consent = _card(sse(http.post("/chat", json={"text": f"pay {DAIRY.id}"}).text))
    sandbox.down = {"/oauth2/token"}

    page = http.get("/boc/consent", params={"code": "code-1"})

    assert page.status_code == 409
    assert "open the bank" in page.text
    assert not any(r.url.path.endswith("/execute") for r in sandbox.requests)
    assert http.get(f"/consents/{consent['card']['id']}").json()["card_kind"] == "consent"


def test_an_execute_that_breaks_off_says_check_the_bank_and_cancel_cannot_fail_it(
    http: TestClient, sandbox: Sandbox
) -> None:
    consent = _card(sse(http.post("/chat", json={"text": f"pay {DAIRY.id}"}).text))
    consent_id = consent["card"]["id"]
    sandbox.down = {"/execute"}

    page = http.get("/boc/consent", params={"code": "code-1"})

    assert page.status_code == 200
    assert "<h1>Check the bank</h1>" in page.text
    receipt = http.get(f"/consents/{consent_id}").json()["card"]
    assert receipt["result"] == "unknown"
    assert http.post(f"/consents/{consent_id}/cancel").status_code == 409


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"creditor_account": "not an account"}, "not a bank account id"),
        ({"creditor_bank": "citi"}, "not a BIC"),
    ],
)
def test_a_malformed_payee_is_refused(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        boc_settings(**changes)


def test_a_trailing_slash_confirms_without_a_redirect(http: TestClient) -> None:
    # A redirect would carry the API's own address, not the page's, and strand the browser
    consent = _card(sse(http.post("/chat", json={"text": f"pay {DAIRY.id}"}).text))

    page = http.get("/boc/consent/", params={"code": "code-1"}, follow_redirects=False)

    assert page.status_code == 200
    assert "<h1>Paid</h1>" in page.text
    assert "<a href='/'>Back to Ena</a>" in page.text
    receipt = http.get(f"/consents/{consent['card']['id']}").json()["card"]
    assert receipt["result"] == "paid"


def test_a_blank_code_is_no_confirmation_and_keeps_it_open(http: TestClient) -> None:
    consent = _card(sse(http.post("/chat", json={"text": f"pay {DAIRY.id}"}).text))

    page = http.get("/boc/consent", params={"code": " "})

    assert page.status_code == 400
    assert "<h1>No confirmation</h1>" in page.text
    assert http.get(f"/consents/{consent['card']['id']}").json()["card_kind"] == "consent"


# The account's reads: few a day without the customer (error 037)

REFUSED_037 = {
    "fatalError": True,
    "error": {
        "code": "400",
        "additionalDetails": [
            {
                "errorCode": "037",
                "description": "The subscriptionId: sub-1 has reached the max number of requests"
                " on API GET /psd2accounts/v1/accounts/351012345671/statement without the user"
                " participation.",
            }
        ],
    },
}


@dataclass
class Clock:
    now: dt.datetime = NOW

    def __call__(self) -> dt.datetime:
        return self.now


@pytest.fixture
def store() -> Iterator[Store]:
    store = Store(":memory:")
    yield store
    store.close()


def reads(sandbox: Sandbox, store: Store, clock: Clock, **changes: Any) -> AccountReads:
    client = BocClient(
        boc_settings(**changes), httpx.Client(transport=httpx.MockTransport(sandbox))
    )
    return AccountReads(BocBank.from_settings(client, clock), store, clock)


def statements(sandbox: Sandbox) -> int:
    return sum(r.url.path.endswith("/statement") for r in sandbox.requests)


def test_a_read_is_served_again_until_it_is_old(store: Store) -> None:
    sandbox, clock = Sandbox(), Clock()
    first = reads(sandbox, store, clock).account()

    clock.now = NOW + ACCOUNT_TTL - dt.timedelta(minutes=1)
    # A restart reads the kept copy too: a new reader over the same store
    assert reads(sandbox, store, clock).account() == first
    assert statements(sandbox) == 1

    clock.now = NOW + ACCOUNT_TTL
    fresh = reads(sandbox, store, clock).account()
    assert statements(sandbox) == 2
    assert fresh.read_at == NOW + ACCOUNT_TTL
    assert store.bank_account() == fresh


def test_a_refused_statement_keeps_the_balance_fresh_and_the_last_lines(store: Store) -> None:
    sandbox, clock = Sandbox(), Clock()
    first = reads(sandbox, store, clock).account()
    clock.now = NOW + ACCOUNT_TTL
    sandbox.refuse = {"/statement": REFUSED_037}

    served = reads(sandbox, store, clock).account()

    assert (served.read_at, served.stale, served.lines_stale) == (NOW + ACCOUNT_TTL, False, True)
    assert served.lines == first.lines
    assert store.bank_account() == served


def test_a_refused_balance_serves_the_last_copy_marked_stale(store: Store) -> None:
    sandbox, clock = Sandbox(), Clock()
    first = reads(sandbox, store, clock).account()
    clock.now = NOW + ACCOUNT_TTL
    sandbox.refuse = {"/balance": REFUSED_037}

    assert reads(sandbox, store, clock).account() == first.model_copy(update={"stale": True})
    assert store.bank_account() == first


def test_a_first_read_with_a_refused_statement_shows_the_balance_and_no_lines(
    store: Store,
) -> None:
    sandbox = Sandbox(refuse={"/statement": REFUSED_037})

    served = reads(sandbox, store, Clock()).account()

    assert (served.available_cents, served.lines, served.lines_stale) == (825_050, (), True)


def test_a_refused_first_balance_read_fails_loudly(store: Store) -> None:
    sandbox = Sandbox(refuse={"/balance": REFUSED_037})

    with pytest.raises(BankUnavailable, match="037"):
        reads(sandbox, store, Clock()).account()
    assert store.bank_account() is None


def test_another_accounts_copy_is_never_served(store: Store) -> None:
    sandbox, clock = Sandbox(), Clock()
    other = reads(sandbox, store, clock).account().model_copy(update={"account_id": "351092345672"})
    store.keep_bank_account(other)

    assert reads(sandbox, store, clock).account().account_id == ACCOUNT
    assert statements(sandbox) == 2
