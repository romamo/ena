"""Jinius integration API connector: read the buyer's supplier invoices and mark one paid.

The calls and the response shapes follow docs/jinius-notes.md §0 and the responses saved in
`fixtures/jinius/`. The buyer's client (`JINIUS_CLIENT_ID`) acts for exactly one legal entity
and reads the invoices sent to it (`GET /v3/invoices/payable`).

Mapping a Jinius invoice onto `SupplierInvoice`:

- `id` is the Jinius document id (a UUID), the id every invoice call takes
- `jinius_id` is the invoice number (`documentNumber`, such as `EFM-2026-001`), the number a
  payment's reference carries and books matching looks for (ena/rules/books.py)
- `supplier_iban` comes from a directory the caller passes, keyed by the supplier's VAT
  number: the invoice payload carries no IBAN
- `amount_cents` is `totalAmount` rounded half up to the cent: Jinius sends unrounded floats
  (`179.9994` for a €180.00 invoice)

When Jinius can't be reached, `read_supplier_invoices` serves the saved fixture and says so
(build-plan §11). Any other refusal fails loudly.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ena.models import InvoiceStatus, SupplierInvoice

PAGE_SIZE = 50
TOKEN_MARGIN_SECONDS = 60
"""A token with less than this left is replaced before a call (tokens last 300 s)."""

FIXTURE_PAYABLE = (
    Path(__file__).resolve().parents[2] / "fixtures" / "jinius" / "invoices_payable.json"
)

STATUSES: Mapping[str, InvoiceStatus] = {
    "WaitingApproval": InvoiceStatus.NEW,
    "Approved": InvoiceStatus.NEW,
    "Paid": InvoiceStatus.PAID,
    "Rejected": InvoiceStatus.DECLINED,
    "Voided": InvoiceStatus.DECLINED,
}
"""Jinius's buyer-side invoice statuses as Ena's. Ena's own steps (scheduled, awaiting the
owner's approval) start from `new`; a status not listed here is refused."""


class JiniusError(RuntimeError):
    """Jinius answered with something other than success."""


class JiniusUnavailable(JiniusError):
    """Jinius could not be reached, or failed on its side (5xx)."""


class JiniusSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JINIUS_", env_file=".env", extra="ignore")

    client_id: str = Field(min_length=1)
    client_secret: SecretStr
    base_url: str = "https://api.integration.platform.cy"
    """The API host. Its own `/oauth/token` issues the token: one from the portal's Keycloak
    realm is refused with 401 "Jwt issuer is not configured" (checked 2026-11-28)."""


# Value objects


@dataclass(frozen=True, slots=True)
class DocumentId:
    """A Jinius document id, as every invoice call takes it."""

    value: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", self.value):
            raise ValueError(f"not a Jinius document id: {self.value!r}")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class VatNumber:
    value: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9A-Z]{6,14}", self.value):
            raise ValueError(f"not a VAT number: {self.value!r}")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ManualPayment:
    """A payment made outside Jinius (our bocAPI payment), reported with `markAsPaid`."""

    amount_cents: int
    paid_on: dt.date
    reference: str
    """The bank's reference for the payment, shown to the supplier."""
    comment: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.amount_cents, bool) or not isinstance(self.amount_cents, int):
            raise TypeError(f"amount_cents must be an int, got {self.amount_cents!r}")
        if self.amount_cents <= 0:
            raise ValueError(f"amount must be positive, got {self.amount_cents} cents")
        if not self.reference.strip():
            raise ValueError("a manual payment needs the bank's reference")

    def to_jinius(self) -> dict[str, Any]:
        return {
            "paymentAmount": float(Decimal(self.amount_cents) / 100),
            "paymentMethod": "Other",
            "invoicePaymentDate": f"{self.paid_on.isoformat()}T00:00:00Z",
            "referenceNumber": self.reference,
            "comments": self.comment,
        }


# Response models: only the fields we use


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class AccessToken(_Model):
    access_token: SecretStr
    expires_in: int = Field(gt=0)

    def bearer(self) -> str:
        return f"Bearer {self.access_token.get_secret_value()}"


class LegalEntityDetails(_Model):
    registered_business_name: str = Field(alias="registeredBusinessName", min_length=1)


class Document(_Model):
    """An invoice or credit note as `GET /v3/invoices/...` and `/v3/creditNotes/...` return it."""

    kind: Literal["Invoice", "CreditNote"]
    id: str
    document_number: str = Field(alias="documentNumber", min_length=1)
    currency: str
    status: str
    issue_date: dt.date = Field(alias="issueDate")
    due_date: dt.date | None = Field(alias="dueDate")
    issuer_vat_number: str = Field(alias="issuerVatNumber")
    issuer: LegalEntityDetails | None = Field(alias="issuerLegalEntityDetails")
    """Null on `GET /v3/creditNotes/{id}` (fixtures/jinius/credit_note_*.json)."""
    total_amount: Decimal = Field(alias="totalAmount")

    @field_validator("issue_date", "due_date", mode="before")
    @classmethod
    def _midnight_date(cls, value: object) -> object:
        """Jinius sends dates as UTC midnight (`2026-12-11T00:00:00Z`); none as year 1."""
        if not isinstance(value, str):
            raise ValueError(f"a Jinius date must be a string, got {value!r}")
        if value.startswith("0001-01-01"):
            return None
        match = re.fullmatch(r"([0-9]{4}-[0-9]{2}-[0-9]{2})T00:00:00Z?", value)
        if match is None:
            raise ValueError(f"not a Jinius date at midnight: {value!r}")
        return match.group(1)

    @field_validator("total_amount", mode="before")
    @classmethod
    def _exact_decimal(cls, value: object) -> object:
        """Read a JSON float through its shortest repr, so 179.9994 stays 179.9994."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"an amount must be a JSON number, got {value!r}")
        return Decimal(repr(value))

    @property
    def amount_cents(self) -> int:
        cents = (self.total_amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        return int(cents)


class PageResponse(_Model):
    current_page: int = Field(alias="currentPage", ge=1)
    total_pages: int = Field(alias="totalPages", ge=0)
    total_items: int = Field(alias="totalItems", ge=0)


class DocumentPage(_Model):
    documents: tuple[Document, ...]
    page: PageResponse = Field(alias="pageResponse")


# Mapping


def to_supplier_invoice(document: Document, ibans: Mapping[VatNumber, str]) -> SupplierInvoice:
    """Turn a payable Jinius invoice into Ena's `SupplierInvoice`."""
    if document.kind != "Invoice":
        raise JiniusError(f"{document.document_number} is a {document.kind}, not an invoice")
    if document.currency != "EUR":
        raise JiniusError(f"{document.document_number} is in {document.currency}, not EUR")
    if document.due_date is None:
        raise JiniusError(f"{document.document_number} has no due date")
    status = STATUSES.get(document.status)
    if status is None:
        raise JiniusError(f"{document.document_number} has an unknown status {document.status!r}")
    if document.issuer is None:
        raise JiniusError(f"{document.document_number} names no supplier")
    supplier = document.issuer.registered_business_name
    vat = VatNumber(document.issuer_vat_number)
    iban = ibans.get(vat)
    if iban is None:
        raise JiniusError(
            f"no IBAN for supplier {supplier} (VAT {vat}): add it to the supplier directory"
        )
    return SupplierInvoice(
        id=str(DocumentId(document.id)),
        supplier_name=supplier,
        supplier_iban=iban,
        amount_cents=document.amount_cents,
        issue_date=document.issue_date,
        due_date=document.due_date,
        status=status,
        jinius_id=document.document_number,
    )


# Client


class JiniusClient:
    def __init__(
        self,
        settings: JiniusSettings,
        http: httpx.Client,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.http = http
        self.clock = clock
        self._token: AccessToken | None = None
        self._token_until = 0.0

    def payable_invoices(self) -> list[Document]:
        """Every invoice sent to our legal entity, all pages."""
        documents: list[Document] = []
        page = 1
        while True:
            data = self._call(
                "GET",
                "/v3/invoices/payable",
                params={"page": str(page), "pageSize": str(PAGE_SIZE)},
            )
            result = DocumentPage.model_validate(data)
            documents.extend(result.documents)
            if page >= result.page.total_pages:
                return documents
            page += 1

    def invoice(self, document_id: DocumentId) -> Document:
        return Document.model_validate(self._call("GET", f"/v3/invoices/{document_id}"))

    def approve(self, document_id: DocumentId) -> Document:
        """Approve an invoice as its recipient; Jinius marks paid only approved invoices."""
        return Document.model_validate(self._call("POST", f"/v3/invoices/{document_id}/approve"))

    def mark_paid(self, document_id: DocumentId, payment: ManualPayment) -> Document:
        """Report a payment made outside Jinius. It reaches Paid once the supplier accepts."""
        data = self._call(
            "POST", f"/v3/invoices/{document_id}/markAsPaid", json_body=payment.to_jinius()
        )
        return Document.model_validate(data)

    # Plumbing

    def _bearer(self) -> str:
        token = self._token
        if token is None or self.clock() >= self._token_until:
            token = self._fetch_token()
        return token.bearer()

    def _fetch_token(self) -> AccessToken:
        response = self._send(
            "POST",
            f"{self.settings.base_url}/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.settings.client_id,
                "client_secret": self.settings.client_secret.get_secret_value(),
            },
        )
        if response.is_error:
            raise JiniusError(f"token request -> {response.status_code}: {response.text[:300]}")
        token = AccessToken.model_validate(response.json())
        self._token = token
        self._token_until = self.clock() + token.expires_in - TOKEN_MARGIN_SECONDS
        return token

    def _call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: Any = None,
    ) -> Any:
        response = self._request(method, path, params, json_body)
        if response.status_code == 401:
            # Token expired or revoked early: refresh once, then fail loudly (§11)
            self._fetch_token()
            response = self._request(method, path, params, json_body)
        if response.is_error:
            raise _error(response)
        return response.json()

    def _request(
        self, method: str, path: str, params: dict[str, str] | None, json_body: Any
    ) -> httpx.Response:
        return self._send(
            method,
            f"{self.settings.base_url}{path}",
            params=params,
            json=json_body,
            headers={"Authorization": self._bearer(), "Accept": "application/json"},
        )

    def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self.http.request(method, url, **kwargs)
        except httpx.TransportError as error:
            raise JiniusUnavailable(f"{method} {url}: {error}") from error
        if response.status_code >= 500:
            raise JiniusUnavailable(
                f"{method} {httpx.URL(url).path} -> {response.status_code}: {response.text[:300]}"
            )
        return response


def _error(response: httpx.Response) -> JiniusError:
    where = f"{response.request.method} {response.request.url.path}"
    if response.status_code == 403 and not response.content:
        return JiniusError(
            f"{where} -> 403 with no body: the legal entity's plan lacks this API "
            "(invoices need Enterprise; docs/jinius-notes.md §0)"
        )
    return JiniusError(f"{where} -> {response.status_code}: {response.text[:500]}")


# Reading with the fixture fallback


@dataclass(frozen=True, slots=True)
class InvoiceFeed:
    invoices: tuple[SupplierInvoice, ...]
    source: Literal["live", "fixture"]
    note: str
    """What the UI shows about where the invoices came from."""


def read_supplier_invoices(
    client: JiniusClient,
    ibans: Mapping[VatNumber, str],
    fixture: Path = FIXTURE_PAYABLE,
) -> InvoiceFeed:
    """Read the supplier invoices live, or from the saved fixture when Jinius is unreachable."""
    try:
        documents = client.payable_invoices()
    except JiniusUnavailable as error:
        page = DocumentPage.model_validate(json.loads(fixture.read_text(encoding="utf-8")))
        return InvoiceFeed(
            invoices=tuple(to_supplier_invoice(doc, ibans) for doc in page.documents),
            source="fixture",
            note=f"Jinius sandbox offline, showing saved data ({error})",
        )
    return InvoiceFeed(
        invoices=tuple(to_supplier_invoice(doc, ibans) for doc in documents),
        source="live",
        note="Jinius integration environment, live",
    )
