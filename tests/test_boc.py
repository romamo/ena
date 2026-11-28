import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from ena.connectors.boc import (
    AccessToken,
    AccountId,
    AuthCode,
    BocClient,
    BocError,
    BocSettings,
    Money,
    PaymentId,
    PaymentOrder,
    Recorder,
    SubscriptionId,
)

BASE = "https://sandbox.test/psd2"
SIGN = "https://sandbox.test/sign"


def settings() -> BocSettings:
    return BocSettings(
        client_id="app-id",
        client_secret="app-secret",
        api_base=BASE,
        sign_url=SIGN,
        _env_file=None,
    )


def client(handler: httpx.MockTransport, recorder: Recorder | None = None) -> BocClient:
    return BocClient(settings(), httpx.Client(transport=handler), recorder)


def token() -> AccessToken:
    return AccessToken.model_validate(
        {"access_token": "tok", "expires_in": 3600, "scope": "TPPOAuth2Security"}
    )


def test_client_token_posts_the_documented_form() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"access_token": "tok", "expires_in": 3600, "scope": "TPPOAuth2Security"}
        )

    result = client(httpx.MockTransport(handle)).client_token()

    form = parse_qs(seen[0].content.decode())
    assert str(seen[0].url) == f"{BASE}/oauth2/token"
    assert form == {
        "client_id": ["app-id"],
        "client_secret": ["app-secret"],
        "grant_type": ["client_credentials"],
        "scope": ["TPPOAuth2Security"],
    }
    assert result.bearer() == "Bearer tok"


def test_create_subscription_sends_consent_headers() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            201, json={"subscriptionId": "Sub1", "status": "PENDING", "selectedAccounts": []}
        )

    subscription = client(httpx.MockTransport(handle)).create_subscription(token())

    request = seen[0]
    assert request.headers["Authorization"] == "Bearer tok"
    assert request.headers["originUserId"] == "999999"
    assert request.headers["journeyId"]
    assert request.headers["timeStamp"].isdigit()
    assert json.loads(request.content)["accounts"]["balance"] is True
    assert subscription.subscription_id == "Sub1"


def test_consent_urls_carry_the_subscription_or_payment_and_no_state() -> None:
    # The sandbox answers a consent URL carrying `state` with `error=badstate` (#35)
    boc = client(httpx.MockTransport(lambda _: httpx.Response(500)))

    sub = parse_qs(urlsplit(boc.subscription_consent_url(SubscriptionId("Sub1"))).query)
    pay = parse_qs(urlsplit(boc.payment_consent_url(PaymentId("P1"))).query)

    common = {
        "response_type": ["code"],
        "redirect_uri": ["https://localhost/"],
        "scope": ["UserOAuth2Security"],
        "client_id": ["app-id"],
    }
    assert sub == {**common, "subscriptionid": ["Sub1"]}
    assert pay == {**common, "paymentid": ["P1"]}
    assert "state" not in sub
    assert "state" not in pay


def test_auth_code_is_read_from_the_redirect() -> None:
    assert AuthCode.from_redirect("https://localhost/?code=abc").value == "abc"
    with pytest.raises(ValueError, match="no `code`"):
        AuthCode.from_redirect("https://localhost/")
    with pytest.raises(BocError, match="consent refused: access_denied denied by user"):
        AuthCode.from_redirect(
            "https://localhost/?error=access_denied&error_description=denied+by+user"
        )
    with pytest.raises(BocError, match="consent refused: badstate"):
        AuthCode.from_redirect("https://localhost/?state=x&error=badstate")


def test_statement_converts_boc_dates_and_query() -> None:
    seen: list[httpx.Request] = []
    body = {
        "account": {"accountId": "351012345671"},
        "transaction": [
            {
                "id": "t1",
                "dcInd": "DEBIT",
                "transactionAmount": {"amount": 30.5, "currency": "EUR"},
                "description": "Flour",
                "postingDate": "22/08/2019",
                "valueDate": "23/08/2019",
            },
            {
                "id": "t2",
                "transactionAmount": {"amount": 10, "currency": "EUR"},
                "postingDate": "24/08/2019",
                "valueDate": "24/08/2019",
            },
        ],
    }

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=body)

    rows = client(httpx.MockTransport(handle)).statement(
        token(),
        SubscriptionId("Sub1"),
        AccountId("351012345671"),
        date(2019, 8, 1),
        date(2019, 8, 31),
    )

    assert seen[0].url.params["startDate"] == "01/08/2019"
    assert seen[0].headers["subscriptionId"] == "Sub1"
    assert rows[0].posting_date == date(2019, 8, 22)
    assert rows[0].transaction_amount.amount == Decimal("30.5")
    assert rows[1].dc_ind is None


def test_statement_refuses_a_reversed_range() -> None:
    boc = client(httpx.MockTransport(lambda _: httpx.Response(500)))
    with pytest.raises(ValueError, match="after end"):
        boc.statement(
            token(),
            SubscriptionId("S"),
            AccountId("351012345671"),
            date(2026, 2, 1),
            date(2026, 1, 1),
        )


def test_payment_order_matches_the_sign_sample() -> None:
    order = PaymentOrder(
        debtor=AccountId("351012345671"),
        creditor=AccountId("48193222324233"),
        creditor_bank_id="CITIUS33",
        amount=Money.parse("30"),
        details="SWIFT Transfer",
    )

    assert order.to_boc() == {
        "debtor": {"bankId": "", "accountId": "351012345671"},
        "creditor": {"bankId": "CITIUS33", "accountId": "48193222324233"},
        "transactionAmount": {"amount": 30, "currency": "EUR"},
        "paymentDetails": "SWIFT Transfer",
    }


def test_money_parse() -> None:
    assert Money.parse("1240.50").cents == 124050
    assert Money.parse("12.5").as_boc_amount() == 12.5
    with pytest.raises(ValueError, match="2 decimals"):
        Money.parse("1.234")
    with pytest.raises(ValueError, match="positive"):
        Money.parse("0")


@pytest.mark.parametrize("raw", ["abc", "", "Infinity", "-Infinity", "NaN", "sNaN"])
def test_money_parse_refuses_non_amounts_with_value_error(raw: str) -> None:
    # argparse turns only ValueError/TypeError into a usage error for `pay --amount`
    with pytest.raises(ValueError, match="not an amount"):
        Money.parse(raw)


def test_execute_reports_completion() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/psd2/v1/payments/P1/execute"
        return httpx.Response(200, json={"code": "CPLT", "description": ["ok"], "refNumber": "R9"})

    result = client(httpx.MockTransport(handle)).execute_payment(token(), PaymentId("P1"))

    assert result.completed
    assert result.ref_number == "R9"


def test_http_error_fails_loudly() -> None:
    def handle(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text='{"error":"invalid_client"}')

    with pytest.raises(BocError, match="401"):
        client(httpx.MockTransport(handle)).client_token()


def test_recorder_redacts_tokens(tmp_path: Path) -> None:
    def handle(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"access_token": "secret", "expires_in": 3600, "scope": "TPPOAuth2Security"}
        )

    client(httpx.MockTransport(handle), Recorder(tmp_path)).client_token()

    saved = json.loads((tmp_path / "token_client_credentials.json").read_text(encoding="utf-8"))
    assert saved["access_token"] == "REDACTED"
    assert saved["expires_in"] == 3600


def test_account_id_is_validated() -> None:
    with pytest.raises(ValueError, match="not a bank account id"):
        AccountId("12 34")
