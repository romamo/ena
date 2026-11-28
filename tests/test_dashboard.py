"""The owner's dashboard: read only, figures computed in code, nothing added to the chat.

The gate pays through a fake bank; the dashboard's routes are driven through `TestClient`.
"""

import datetime as dt
from collections.abc import Iterator
from dataclasses import dataclass, field

import httpx
import pytest
from fastapi.testclient import TestClient

from ena.api import create_app
from ena.connectors.jcc import JccClient, JccSettings
from ena.dashboard import Dashboard
from ena.gate import Gate
from ena.mcp_server import EnaTools, Ledger, ReplayDay
from ena.models import ApprovalRequest, Receipt
from ena.rules.loan import loan_offer
from ena.rules.reserve import reserve_offer
from ena.seed import LEAN_WEEK, synthetic_month
from ena.store import Store

MONTH = synthetic_month()
DAIRY, FLOUR = MONTH.invoices
NOW = dt.datetime(2026, 11, 16, 9, 0, tzinfo=dt.UTC)


@dataclass
class FakeBank:
    calls: list[str] = field(default_factory=list)
    simulated: bool = False

    def pay(self, action: str, amount_cents: int, ref: str) -> str:
        self.calls.append(ref)
        return "CPLT"


@dataclass
class Board:
    http: TestClient
    store: Store
    gate: Gate
    ledger: Ledger
    tools: EnaTools
    bank: FakeBank

    def pay(self, invoice_id: str) -> Receipt | ApprovalRequest:
        invoice = self.ledger.invoice(invoice_id)
        return self.gate.request_payment(f"Pay {invoice.id}", invoice.amount_cents, invoice.id)

    def get(self, route: str) -> dict:  # type: ignore[type-arg]
        response = self.http.get(f"/dashboard/{route}")
        assert response.status_code == 200, response.text
        return response.json()  # type: ignore[no-any-return]


def _no_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected call to {request.url}")


@pytest.fixture
def board() -> Iterator[Board]:
    store = Store(":memory:")
    bank = FakeBank()
    gate = Gate(MONTH.business, store, bank, lambda: NOW)
    ledger = Ledger(MONTH, store, ReplayDay(LEAN_WEEK))
    tools = EnaTools(ledger, store, gate, lambda: NOW)
    settings = JccSettings(
        api_user="ena-api", api_password="pw", callback_secret="cb-secret", _env_file=None
    )
    jcc = JccClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(_no_network)))
    app = create_app(
        store, jcc, settings.callback_secret, gate=gate, dashboard=Dashboard(ledger, store)
    )
    with TestClient(app) as http:
        yield Board(http, store, gate, ledger, tools, bank)
    store.close()


def test_the_overview_before_anything_is_paid(board: Board) -> None:
    overview = board.get("overview")
    cash = board.ledger.cash_forecast()
    lean = next(w for w in cash.forecast.weeks if w.week_start == cash.forecast.lean_week)
    assert overview["as_of"] == LEAN_WEEK.isoformat()
    assert overview["synthetic"] is True
    assert overview["limit_cents"] == MONTH.business.auto_pay_limit_cents
    assert overview["balance_cents"] == board.ledger.balance_cents()
    assert overview["pending"] == []
    assert (overview["paid_on_its_own"], overview["stepped_in"], overview["paid_out_cents"]) == (
        0,
        0,
        0,
    )
    assert overview["unpaid_invoices"] == 2
    assert overview["unpaid_cents"] == DAIRY.amount_cents + FLOUR.amount_cents
    assert overview["lean_week"] == cash.forecast.lean_week.isoformat()  # type: ignore[union-attr]
    assert overview["lean_week_closing_cents"] == lean.closing_cents


def test_the_overview_counts_what_ena_paid_and_when_the_owner_stepped_in(board: Board) -> None:
    board.pay(DAIRY.id)
    approval = board.pay(FLOUR.id)
    assert isinstance(approval, ApprovalRequest)

    overview = board.get("overview")
    assert [p["id"] for p in overview["pending"]] == [approval.id]
    assert overview["paid_on_its_own"] == 1
    assert overview["paid_on_its_own_cents"] == DAIRY.amount_cents
    assert overview["stepped_in"] == 0
    assert overview["unpaid_invoices"] == 1

    board.http.post(f"/approvals/{approval.id}/approve")
    overview = board.get("overview")
    assert overview["pending"] == []
    assert overview["stepped_in"] == 1
    assert overview["paid_out_cents"] == DAIRY.amount_cents + FLOUR.amount_cents
    assert overview["unpaid_invoices"] == 0


def test_a_decline_is_stepping_in_but_pays_nothing(board: Board) -> None:
    approval = board.pay(FLOUR.id)
    board.http.post(f"/approvals/{approval.id}/decline")
    overview = board.get("overview")
    assert (overview["stepped_in"], overview["paid_out_cents"]) == (1, 0)


def test_receipts_are_newest_first(board: Board) -> None:
    board.pay(DAIRY.id)
    approval = board.pay(FLOUR.id)
    board.http.post(f"/approvals/{approval.id}/approve")
    refs = [r["ref"] for r in board.get("receipts")["receipts"]]
    assert refs == [FLOUR.id, DAIRY.id]


def test_the_forecast_is_the_ledgers_with_its_safe_line(board: Board) -> None:
    cash = board.ledger.cash_forecast()
    assert board.get("forecast") == {
        "forecast": cash.forecast.model_dump(mode="json"),
        "threshold_cents": cash.threshold_cents,
    }


def test_credit_says_whether_the_lean_week_needs_a_loan(board: Board) -> None:
    cash = board.ledger.cash_forecast()
    credit = board.get("credit")
    assert cash.lean is not None
    gap = cash.threshold_cents - cash.lean.closing_cents
    assert credit["lean_week"] == cash.lean.week_start.isoformat()
    assert credit["lean_week_closing_cents"] == cash.lean.closing_cents
    assert credit["threshold_cents"] == cash.threshold_cents
    assert credit["gap_cents"] == gap > 0
    loan = loan_offer(cash)
    if loan is None:
        assert credit["verdict"] == "reserve_covers"
    else:
        assert credit["verdict"] == "loan_needed"
        assert loan.basis["gap_cents"] == gap


def test_credit_with_no_lean_week_needs_nothing(board: Board) -> None:
    rich = MONTH.model_copy(
        update={"account": MONTH.account.model_copy(update={"balance_cents": 10_000_000})}
    )
    ledger = Ledger(rich, board.store, ReplayDay(LEAN_WEEK))
    credit = Dashboard(ledger, board.store).credit()
    assert (credit.verdict, credit.lean_week, credit.gap_cents) == ("no_lean_week", None, None)
    assert (credit.reserve, credit.loan) == (None, None)


def test_credit_shows_the_instalment_and_the_rules_offers(board: Board) -> None:
    cash = board.ledger.cash_forecast()
    credit = board.get("credit")
    assert credit["instalment_cents"] == cash.instalment_cents
    assert credit["instalment_due"] == cash.instalment_due.isoformat()
    reserve, loan = reserve_offer(cash), loan_offer(cash)
    assert credit["reserve"] == (None if reserve is None else reserve.model_dump(mode="json"))
    assert credit["loan"] == (None if loan is None else loan.model_dump(mode="json"))
    if credit["loan"] is not None:
        assert credit["loan"]["simulated"] is True


def test_the_dashboard_writes_nothing_and_adds_nothing_to_the_chat(board: Board) -> None:
    board.pay(DAIRY.id)
    before = board.store.receipts()
    for route in ("overview", "receipts", "forecast", "credit"):
        board.get(route)
    assert board.store.receipts() == before
    assert board.tools.current_turn().cards == []
    assert board.bank.calls == [DAIRY.id]


def test_the_dashboard_has_no_way_to_pay_or_approve(board: Board) -> None:
    for route in ("overview", "receipts", "forecast", "credit"):
        assert board.http.post(f"/dashboard/{route}").status_code == 405


def test_a_day_with_no_forecast_is_refused(board: Board) -> None:
    class NoForecast(Ledger):
        def cash_forecast(self):  # type: ignore[no-untyped-def]
            from ena.mcp_server import ToolError

            raise ToolError("no forecast")

    ledger = NoForecast(MONTH, board.store, ReplayDay(LEAN_WEEK))
    settings = JccSettings(
        api_user="ena-api", api_password="pw", callback_secret="cb-secret", _env_file=None
    )
    jcc = JccClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(_no_network)))
    app = create_app(
        board.store, jcc, settings.callback_secret, dashboard=Dashboard(ledger, board.store)
    )
    with TestClient(app) as http:
        for route in ("overview", "forecast", "credit"):
            assert http.get(f"/dashboard/{route}").status_code == 409
        assert http.get("/dashboard/receipts").status_code == 200
