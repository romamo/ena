import datetime as dt
import json
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from ena.connectors.jinius import (
    Document,
    DocumentId,
    DocumentPage,
    JiniusClient,
    JiniusDesk,
    JiniusError,
    JiniusNotConfigured,
    JiniusSettings,
    JiniusUnavailable,
    ManualPayment,
    NotApproved,
    VatNumber,
    read_payable,
    read_supplier_invoices,
    to_supplier_invoice,
)
from ena.models import InvoiceStatus

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "jinius"
BASE = "https://api.jinius.test"
TOKEN_URL = f"{BASE}/oauth/token"
FLOUR_MILL_VAT = VatNumber("99900100X")
FLOUR_MILL_IBAN = "CY80002001950000357072811260"
IBANS = {FLOUR_MILL_VAT: FLOUR_MILL_IBAN}
INVOICE_180 = DocumentId("3b1b38a9-e0d2-484d-b43a-6fd641c8ac27")


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def settings() -> JiniusSettings:
    return JiniusSettings(
        client_id="buyer-id",
        client_secret="buyer-secret",
        base_url=BASE,
        _env_file=None,
    )


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Api:
    """A fake Jinius: answers token requests and routes the rest to `routes`."""

    def __init__(self, routes: dict[tuple[str, str], list[httpx.Response]]) -> None:
        self.routes = routes
        self.tokens = 0
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            self.tokens += 1
            return httpx.Response(
                200, json={"access_token": f"tok-{self.tokens}", "expires_in": 300}
            )
        self.seen.append(request)
        return self.routes[(request.method, request.url.path)].pop(0)


def client(api: Api, clock: Clock | None = None) -> JiniusClient:
    return JiniusClient(
        settings(), httpx.Client(transport=httpx.MockTransport(api)), clock or Clock()
    )


# The saved responses


@pytest.mark.parametrize("name", sorted(p.name for p in FIXTURES.glob("*.json")))
def test_every_saved_response_parses(name: str) -> None:
    data = fixture(name)
    if "pageResponse" in data:
        page = DocumentPage.model_validate(data)
        assert page.page.total_items == len(page.documents)
    else:
        Document.model_validate(data)


def test_payable_fixture_maps_onto_supplier_invoices() -> None:
    page = DocumentPage.model_validate(fixture("invoices_payable.json"))

    invoices = {i.jinius_id: i for i in (to_supplier_invoice(d, IBANS) for d in page.documents)}

    small = invoices["EFM-2026-001"]
    assert small.id == str(INVOICE_180)
    assert small.supplier_name == "Ena Flour Mill Ltd"
    assert small.supplier_iban == FLOUR_MILL_IBAN
    assert small.amount_cents == 18000  # totalAmount 179.9994
    assert small.issue_date == dt.date(2026, 11, 28)
    assert small.due_date == dt.date(2026, 12, 11)
    assert small.status is InvoiceStatus.NEW  # WaitingApproval
    assert invoices["EFM-2026-002"].amount_cents == 124000  # totalAmount 1240.0038


def test_credit_note_has_no_due_date_and_is_not_an_invoice() -> None:
    note = Document.model_validate(fixture("credit_note_EFM-CN-2026-001.json"))

    assert note.due_date is None
    assert note.amount_cents == 11900
    with pytest.raises(JiniusError, match="CreditNote, not an invoice"):
        to_supplier_invoice(note, IBANS)


def invoice_data(**changes: Any) -> dict[str, Any]:
    return {**fixture("invoice_EFM-2026-001.json"), **changes}


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"status": "PartiallyPaid"}, "unknown status 'PartiallyPaid'"),
        ({"currency": "USD"}, "in USD, not EUR"),
        ({"issuerVatNumber": "11111111X"}, "no IBAN for supplier Ena Flour Mill Ltd"),
    ],
)
def test_mapping_refuses_what_it_cannot_place(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(JiniusError, match=message):
        to_supplier_invoice(Document.model_validate(invoice_data(**changes)), IBANS)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("Approved", InvoiceStatus.NEW),
        ("Paid", InvoiceStatus.PAID),
        ("Voided", InvoiceStatus.DECLINED),
    ],
)
def test_status_mapping(status: str, expected: InvoiceStatus) -> None:
    document = Document.model_validate(invoice_data(status=status))
    assert to_supplier_invoice(document, IBANS).status is expected


@pytest.mark.parametrize(
    ("field", "value"),
    [("dueDate", "2026-12-11T13:45:00Z"), ("dueDate", "11/12/2026"), ("totalAmount", "180.00")],
)
def test_document_refuses_malformed_fields(field: str, value: str) -> None:
    with pytest.raises(ValueError, match=field):
        Document.model_validate(invoice_data(**{field: value}))


@pytest.mark.parametrize(("total", "cents"), [(179.9994, 18000), (0.005, 1), (1240.0038, 124000)])
def test_amount_rounds_half_up_to_the_cent(total: float, cents: int) -> None:
    assert Document.model_validate(invoice_data(totalAmount=total)).amount_cents == cents


# The client


def test_payable_invoices_reads_every_page_with_one_token() -> None:
    first = fixture("invoices_payable.json")
    one, two = first["documents"]
    api = Api(
        {
            ("GET", "/v3/invoices/payable"): [
                httpx.Response(
                    200,
                    json={
                        "documents": [one],
                        "pageResponse": {"currentPage": 1, "totalPages": 2, "totalItems": 2},
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "documents": [two],
                        "pageResponse": {"currentPage": 2, "totalPages": 2, "totalItems": 2},
                    },
                ),
            ]
        }
    )

    documents = client(api).payable_invoices()

    assert [d.document_number for d in documents] == [one["documentNumber"], two["documentNumber"]]
    assert [parse_qs(r.url.query.decode())["page"] for r in api.seen] == [["1"], ["2"]]
    assert all(r.headers["Authorization"] == "Bearer tok-1" for r in api.seen)
    assert api.tokens == 1


def test_empty_payables_stop_after_one_page() -> None:
    api = Api(
        {
            ("GET", "/v3/invoices/payable"): [
                httpx.Response(
                    200,
                    json={
                        "documents": [],
                        "pageResponse": {"currentPage": 1, "totalPages": 1, "totalItems": 0},
                    },
                )
            ]
        }
    )
    assert client(api).payable_invoices() == []


def test_token_request_posts_client_credentials() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 300})
        return httpx.Response(200, json=fixture("invoice_EFM-2026-001.json"))

    JiniusClient(settings(), httpx.Client(transport=httpx.MockTransport(handle))).invoice(
        INVOICE_180
    )

    assert parse_qs(seen[0].content.decode()) == {
        "grant_type": ["client_credentials"],
        "client_id": ["buyer-id"],
        "client_secret": ["buyer-secret"],
    }
    assert str(seen[1].url) == f"{BASE}/v3/invoices/{INVOICE_180}"


def test_token_is_replaced_a_minute_before_it_expires() -> None:
    invoice = fixture("invoice_EFM-2026-001.json")
    api = Api({("GET", f"/v3/invoices/{INVOICE_180}"): [httpx.Response(200, json=invoice)] * 3})
    clock = Clock()
    jinius = client(api, clock)

    jinius.invoice(INVOICE_180)
    clock.now += 239
    jinius.invoice(INVOICE_180)
    clock.now += 1
    jinius.invoice(INVOICE_180)

    assert [r.headers["Authorization"] for r in api.seen] == [
        "Bearer tok-1",
        "Bearer tok-1",
        "Bearer tok-2",
    ]


def test_a_401_refreshes_the_token_once_then_fails() -> None:
    path = f"/v3/invoices/{INVOICE_180}"
    api = Api({("GET", path): [httpx.Response(401), httpx.Response(401)]})

    with pytest.raises(JiniusError, match="401"):
        client(api).invoice(INVOICE_180)

    assert api.tokens == 2
    assert len(api.seen) == 2


def test_a_403_with_no_body_names_the_plan() -> None:
    api = Api({("GET", "/v3/invoices/payable"): [httpx.Response(403)]})

    with pytest.raises(JiniusError, match="plan lacks this API"):
        client(api).payable_invoices()


def test_approve_and_mark_paid_send_the_documented_requests() -> None:
    paid = invoice_data(status="Approved")
    api = Api(
        {
            ("POST", f"/v3/invoices/{INVOICE_180}/approve"): [httpx.Response(200, json=paid)],
            ("POST", f"/v3/invoices/{INVOICE_180}/markAsPaid"): [httpx.Response(200, json=paid)],
        }
    )
    jinius = client(api)

    jinius.approve(INVOICE_180)
    jinius.mark_paid(
        INVOICE_180,
        ManualPayment(18000, dt.date(2026, 12, 1), "BOC-REF-123", "Paid by Ena via bocAPI"),
    )

    approve, mark = api.seen
    assert approve.content == b""
    assert json.loads(mark.content) == {
        "paymentAmount": 180.0,
        "paymentMethod": "Other",
        "invoicePaymentDate": "2026-12-01T00:00:00Z",
        "referenceNumber": "BOC-REF-123",
        "comments": "Paid by Ena via bocAPI",
    }


def test_an_action_succeeds_whatever_its_answer_body() -> None:
    api = Api({("POST", f"/v3/invoices/{INVOICE_180}/approve"): [httpx.Response(204)]})

    client(api).approve(INVOICE_180)

    assert [r.url.path for r in api.seen] == [f"/v3/invoices/{INVOICE_180}/approve"]


def test_mark_paid_refusal_is_raised_with_its_detail() -> None:
    api = Api(
        {
            ("POST", f"/v3/invoices/{INVOICE_180}/markAsPaid"): [
                httpx.Response(
                    400,
                    json={
                        "StatusCode": "InvalidArgument",
                        "Detail": "Invalid status change requested",
                    },
                )
            ]
        }
    )

    with pytest.raises(JiniusError, match="Invalid status change requested"):
        client(api).mark_paid(INVOICE_180, ManualPayment(18000, dt.date(2026, 12, 1), "REF"))


@pytest.mark.parametrize(
    ("args", "error"),
    [((0, "REF"), ValueError), ((18000, " "), ValueError), ((180.0, "REF"), TypeError)],
)
def test_manual_payment_refuses_bad_input(args: tuple[Any, str], error: type[Exception]) -> None:
    amount, reference = args
    with pytest.raises(error):
        ManualPayment(amount, dt.date(2026, 12, 1), reference)


def test_document_id_refuses_an_invoice_number() -> None:
    with pytest.raises(ValueError, match="not a Jinius document id"):
        DocumentId("EFM-2026-001")


# The fixture fallback


def test_read_supplier_invoices_live() -> None:
    api = Api(
        {
            ("GET", "/v3/invoices/payable"): [
                httpx.Response(200, json=fixture("invoices_payable.json"))
            ]
        }
    )

    feed = read_supplier_invoices(client(api), IBANS)

    assert feed.source == "live"
    assert {i.jinius_id for i in feed.invoices} == {"EFM-2026-001", "EFM-2026-002"}


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("no route to host"), httpx.Response(503, text="maintenance")],
)
def test_read_supplier_invoices_falls_back_when_jinius_is_down(
    failure: httpx.ConnectError | httpx.Response,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if isinstance(failure, Exception):
            raise failure
        return failure

    jinius = JiniusClient(settings(), httpx.Client(transport=httpx.MockTransport(handle)))

    feed = read_supplier_invoices(jinius, IBANS)

    assert feed.source == "fixture"
    assert "sandbox offline, showing saved data" in feed.note
    assert sum(i.amount_cents for i in feed.invoices) == 18000 + 124000


def test_read_supplier_invoices_does_not_hide_a_refusal() -> None:
    api = Api({("GET", "/v3/invoices/payable"): [httpx.Response(403)]})

    with pytest.raises(JiniusError) as caught:
        read_supplier_invoices(client(api), IBANS)

    assert not isinstance(caught.value, JiniusUnavailable)


# The owner's read: no IBAN directory needed


def test_read_payable_live_shows_each_invoice_as_jinius_sent_it() -> None:
    api = Api(
        {
            ("GET", "/v3/invoices/payable"): [
                httpx.Response(200, json=fixture("invoices_payable.json"))
            ]
        }
    )

    feed = read_payable(client(api))

    assert (feed.source, feed.note) == ("live", "Jinius integration environment, live")
    by_number = {i.number: i for i in feed.invoices}
    assert set(by_number) == {"EFM-2026-001", "EFM-2026-002"}
    flour = by_number["EFM-2026-002"]
    assert flour.supplier == "Ena Flour Mill Ltd"
    assert flour.amount_cents == 124000
    assert flour.status == "WaitingApproval"
    assert flour.due_date is not None


def test_read_payable_falls_back_to_the_saved_invoices_when_jinius_is_down() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    jinius = JiniusClient(settings(), httpx.Client(transport=httpx.MockTransport(handle)))

    feed = read_payable(jinius)

    assert feed.source == "fixture"
    assert "sandbox offline, showing saved data" in feed.note
    assert sorted(i.amount_cents for i in feed.invoices) == [18000, 124000]


def test_read_payable_does_not_hide_a_refusal() -> None:
    api = Api({("GET", "/v3/invoices/payable"): [httpx.Response(403)]})

    with pytest.raises(JiniusError, match="plan lacks this API"):
        read_payable(client(api))


def test_read_payable_refuses_an_invoice_in_another_currency() -> None:
    page = fixture("invoices_payable.json")
    page["documents"][0]["currency"] = "USD"
    api = Api({("GET", "/v3/invoices/payable"): [httpx.Response(200, json=page)]})

    with pytest.raises(JiniusError, match="is in USD, not EUR"):
        read_payable(client(api))


def test_concurrent_reads_fetch_one_token() -> None:
    page = fixture("invoices_payable.json")
    token_asked = threading.Event()
    release = threading.Event()
    tokens = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal tokens
        if str(request.url) == TOKEN_URL:
            tokens += 1
            token_asked.set()
            release.wait(timeout=5)
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 300})
        return httpx.Response(200, json=page)

    jinius = JiniusClient(settings(), httpx.Client(transport=httpx.MockTransport(handle)), Clock())
    first = threading.Thread(target=jinius.payable_invoices)
    second = threading.Thread(target=jinius.payable_invoices)
    first.start()
    assert token_asked.wait(timeout=5)
    second.start()  # waits on the lock while the first thread's token is in flight
    time.sleep(0.1)  # let the second thread reach the token check before the first ends
    release.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert tokens == 1


# The owner's taps: approve, then mark paid, each answered by the invoice read again

TODAY = dt.date(2026, 11, 28)


def desk(api: Api) -> JiniusDesk:
    return JiniusDesk(client(api), lambda: TODAY)


def test_approve_answers_with_the_invoice_read_again() -> None:
    api = Api(
        {
            ("POST", f"/v3/invoices/{INVOICE_180}/approve"): [httpx.Response(200, json={})],
            ("GET", f"/v3/invoices/{INVOICE_180}"): [
                httpx.Response(200, json=invoice_data(status="Approved"))
            ],
        }
    )

    invoice = desk(api).approve(INVOICE_180)

    assert (invoice.id, invoice.status) == (str(INVOICE_180), "Approved")
    assert [r.method for r in api.seen] == ["POST", "GET"]


def test_mark_paid_reports_the_full_amount_today_by_the_invoice_number() -> None:
    api = Api(
        {
            ("GET", f"/v3/invoices/{INVOICE_180}"): [
                httpx.Response(200, json=invoice_data(status="Approved")),
                httpx.Response(200, json=invoice_data(status="PendingPaymentAcceptance")),
            ],
            ("POST", f"/v3/invoices/{INVOICE_180}/markAsPaid"): [httpx.Response(200)],
        }
    )

    invoice = desk(api).mark_paid(INVOICE_180)

    mark = next(r for r in api.seen if r.method == "POST")
    assert json.loads(mark.content) == {
        "paymentAmount": 180.0,
        "paymentMethod": "Other",
        "invoicePaymentDate": "2026-11-28T00:00:00Z",
        "referenceNumber": "ENA-EFM-2026-001",
        "comments": "Marked paid by Ena, a hackathon demo: no money moved",
    }
    assert invoice.status == "PendingPaymentAcceptance"


def test_mark_paid_refuses_an_invoice_not_yet_approved_and_sends_nothing() -> None:
    api = Api(
        {
            ("GET", f"/v3/invoices/{INVOICE_180}"): [
                httpx.Response(200, json=invoice_data(status="WaitingApproval"))
            ]
        }
    )

    with pytest.raises(
        NotApproved, match="WaitingApproval: only an approved invoice"
    ):
        desk(api).mark_paid(INVOICE_180)

    assert [r.method for r in api.seen] == ["GET"]


def test_without_a_client_the_desk_sends_nothing() -> None:
    idle = JiniusDesk(None, lambda: TODAY, "not configured here")

    with pytest.raises(JiniusNotConfigured, match="not configured here"):
        idle.approve(INVOICE_180)


def test_two_concurrent_mark_paid_taps_send_one_mark_paid() -> None:
    approved = invoice_data(status="Approved")
    pending = invoice_data(status="PendingPaymentAcceptance")
    status = {"now": approved}
    marks = 0
    first_mark = threading.Event()
    release = threading.Event()

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal marks
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 300})
        if request.method == "POST":
            marks += 1
            first_mark.set()
            release.wait(timeout=5)
            status["now"] = pending
            return httpx.Response(200)
        return httpx.Response(200, json=status["now"])

    jinius = JiniusDesk(
        JiniusClient(settings(), httpx.Client(transport=httpx.MockTransport(handle)), Clock()),
        lambda: TODAY,
    )
    refused: list[Exception] = []

    def second_tap() -> None:
        try:
            jinius.mark_paid(INVOICE_180)
        except NotApproved as error:
            refused.append(error)

    first = threading.Thread(target=jinius.mark_paid, args=(INVOICE_180,))
    second = threading.Thread(target=second_tap)
    first.start()
    assert first_mark.wait(timeout=5)
    second.start()
    time.sleep(0.1)  # let the second tap reach the desk while the first mark is in flight
    release.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert marks == 1
    assert len(refused) == 1
