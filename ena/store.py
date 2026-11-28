"""SQLite store for receipts and approval requests (docs/build-plan.md §4, §6).

Receipts are append-only: the store can insert and read them, never change or delete one.
At most one receipt exists per ref, so a payment can never be recorded twice.
An approval request is inserted pending and decided once, in the same transaction as
its next write (the attempt to pay, or the declined receipt); its ref is kept beside it.
A payment attempt is recorded for a ref before the bank is called, also append-only,
so a payment whose outcome is unknown is never sent twice.
Card sales booked from JCC are append-only too, at most one per JCC order id, so a
repeated callback can never book a sale twice.
An event advance offer is kept with its event's id, inserted offered once per offer id,
and decided once (accepted or declined) in the same transaction as its receipt; an event
has at most one accepted advance.
A bank consent (a payment the bank holds until the owner confirms it there) is kept with
the bank's payment id and the reason the gate paid; it is open until its receipt is
written, closed in the same transaction, and at most one is open at a time, since the
bank's redirect after a confirmation names no payment (docs/boc-notes.md §4).
The last read of the bank account is kept, one copy that each read replaces, so a
restart doesn't spend one of the few reads the bank allows a day.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ena.models import (
    AdvanceStatus,
    ApprovalRequest,
    ApprovalStatus,
    BankAccount,
    BankConsent,
    CardSale,
    Offer,
    Receipt,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    ref TEXT NOT NULL UNIQUE,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    ref TEXT NOT NULL,
    status TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_approval_per_ref
    ON approvals (ref) WHERE status = 'pending';
CREATE TABLE IF NOT EXISTS attempts (
    ref TEXT PRIMARY KEY,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS advances (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id TEXT NOT NULL UNIQUE,
    event_id TEXT NOT NULL,
    status TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_accepted_advance_per_event
    ON advances (event_id) WHERE status = 'accepted';
CREATE TABLE IF NOT EXISTS consents (
    id TEXT PRIMARY KEY,
    ref TEXT NOT NULL UNIQUE,
    payment_id TEXT NOT NULL,
    why TEXT NOT NULL,
    open INTEGER NOT NULL,
    body TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_consent ON consents (open) WHERE open = 1;
CREATE TABLE IF NOT EXISTS bank_account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS card_sales (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    jcc_order_id TEXT NOT NULL UNIQUE,
    body TEXT NOT NULL
);
"""


class StoreError(RuntimeError):
    """The store was asked for something its rules forbid."""


@dataclass(frozen=True)
class StoredApproval:
    """An approval request and the ref of the payment it is for."""

    request: ApprovalRequest
    ref: str


@dataclass(frozen=True)
class StoredAdvance:
    """An event advance offer, the event it is for, and what the owner did with it."""

    offer: Offer
    event_id: str
    status: AdvanceStatus


@dataclass(frozen=True)
class StoredConsent:
    """A bank consent, the bank's id for its payment, and why the gate paid."""

    consent: BankConsent
    payment_id: str
    why: str


class Store:
    """Receipts and approval requests in one SQLite database (`:memory:` in tests)."""

    def __init__(self, path: Path | str) -> None:
        # The API's event loop runs on its own thread (ena/api.py), not the one that opened
        # the store; it is still one thread at a time, so sharing the connection is safe
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    # Receipts: insert and read only

    def add_receipt(self, receipt: Receipt) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO receipts (id, ref, body) VALUES (?, ?, ?)",
                (receipt.id, receipt.ref, receipt.model_dump_json()),
            )

    def receipt_for(self, ref: str) -> Receipt | None:
        row = self._db.execute("SELECT body FROM receipts WHERE ref = ?", (ref,)).fetchone()
        return None if row is None else Receipt.model_validate_json(row[0])

    def receipts(self) -> list[Receipt]:
        """Every receipt, oldest first."""
        rows = self._db.execute("SELECT body FROM receipts ORDER BY seq").fetchall()
        return [Receipt.model_validate_json(body) for (body,) in rows]

    # Payment attempts: insert and read only

    def add_attempt(self, ref: str, at: dt.datetime) -> None:
        """Record that the bank is about to be called for `ref`; fails if it already was."""
        with self._db:
            self._db.execute("INSERT INTO attempts (ref, at) VALUES (?, ?)", (ref, at.isoformat()))

    def has_attempt(self, ref: str) -> bool:
        row = self._db.execute("SELECT 1 FROM attempts WHERE ref = ?", (ref,)).fetchone()
        return row is not None

    # Card sales: insert and read only

    def add_card_sale(self, sale: CardSale) -> None:
        """Book a card sale; fails if its id or its JCC order id is already booked."""
        with self._db:
            self._db.execute(
                "INSERT INTO card_sales (id, jcc_order_id, body) VALUES (?, ?, ?)",
                (sale.id, sale.jcc_order_id, sale.model_dump_json()),
            )

    def card_sale_for(self, jcc_order_id: str) -> CardSale | None:
        row = self._db.execute(
            "SELECT body FROM card_sales WHERE jcc_order_id = ?", (jcc_order_id,)
        ).fetchone()
        return None if row is None else CardSale.model_validate_json(row[0])

    def card_sales(self) -> list[CardSale]:
        """Every booked card sale, oldest first."""
        rows = self._db.execute("SELECT body FROM card_sales ORDER BY seq").fetchall()
        return [CardSale.model_validate_json(body) for (body,) in rows]

    # Event advance offers

    def add_advance(self, offer: Offer, event_id: str) -> None:
        """Keep an advance offer, offered; an offer id already kept stays as it is (the id
        carries the amount, so it is the same offer)."""
        with self._db:
            self._db.execute(
                "INSERT INTO advances (offer_id, event_id, status, body) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (offer_id) DO NOTHING",
                (offer.id, event_id, AdvanceStatus.OFFERED.value, offer.model_dump_json()),
            )

    def advance(self, offer_id: str) -> StoredAdvance | None:
        row = self._db.execute(
            "SELECT body, event_id, status FROM advances WHERE offer_id = ?", (offer_id,)
        ).fetchone()
        return None if row is None else _stored_advance(row)

    def accepted_advance_for(self, event_id: str) -> StoredAdvance | None:
        row = self._db.execute(
            "SELECT body, event_id, status FROM advances WHERE event_id = ? AND status = ?",
            (event_id, AdvanceStatus.ACCEPTED.value),
        ).fetchone()
        return None if row is None else _stored_advance(row)

    def record_advance_decision(
        self, offer_id: str, status: AdvanceStatus, receipt: Receipt
    ) -> None:
        """Accept or decline an offered advance and write its receipt, in one transaction;
        fails when the offer is not offered, or its event already has an accepted one."""
        if status is AdvanceStatus.OFFERED:
            raise StoreError(f"advance {offer_id}: a decision is accepted or declined")
        with self._db:
            cursor = self._db.execute(
                "UPDATE advances SET status = ? WHERE offer_id = ? AND status = ?",
                (status.value, offer_id, AdvanceStatus.OFFERED.value),
            )
            if cursor.rowcount != 1:
                raise StoreError(f"advance {offer_id} is not offered")
            self._db.execute(
                "INSERT INTO receipts (id, ref, body) VALUES (?, ?, ?)",
                (receipt.id, receipt.ref, receipt.model_dump_json()),
            )

    # Bank consents

    def add_consent(
        self, consent: BankConsent, payment_id: str, why: str, attempt_at: dt.datetime
    ) -> None:
        """Keep an open consent and record the attempt to pay its ref, in one transaction;
        fails when one is open already or its ref has one."""
        with self._db:
            self._db.execute(
                "INSERT INTO attempts (ref, at) VALUES (?, ?)",
                (consent.ref, attempt_at.isoformat()),
            )
            self._insert_consent(StoredConsent(consent, payment_id, why))

    def consent(self, consent_id: str) -> StoredConsent | None:
        row = self._db.execute(
            "SELECT body, payment_id, why FROM consents WHERE id = ?", (consent_id,)
        ).fetchone()
        return None if row is None else _stored_consent(row)

    def open_consent(self) -> StoredConsent | None:
        """The one consent waiting for the owner's confirmation in the bank, if any."""
        row = self._db.execute(
            "SELECT body, payment_id, why FROM consents WHERE open = 1"
        ).fetchone()
        return None if row is None else _stored_consent(row)

    def close_consent(self, consent_id: str, receipt: Receipt) -> None:
        """Close an open consent and write its receipt, in one transaction."""
        with self._db:
            cursor = self._db.execute(
                "UPDATE consents SET open = 0 WHERE id = ? AND open = 1", (consent_id,)
            )
            if cursor.rowcount != 1:
                raise StoreError(f"consent {consent_id} is not open")
            self._db.execute(
                "INSERT INTO receipts (id, ref, body) VALUES (?, ?, ?)",
                (receipt.id, receipt.ref, receipt.model_dump_json()),
            )

    # The bank account's last read

    def keep_bank_account(self, account: BankAccount) -> None:
        """Keep the latest read, replacing the one before."""
        with self._db:
            self._db.execute(
                "INSERT INTO bank_account (id, body) VALUES (1, ?)"
                " ON CONFLICT (id) DO UPDATE SET body = excluded.body",
                (account.model_dump_json(),),
            )

    def bank_account(self) -> BankAccount | None:
        row = self._db.execute("SELECT body FROM bank_account WHERE id = 1").fetchone()
        return None if row is None else BankAccount.model_validate_json(row[0])

    # Approval requests

    def add_approval(self, request: ApprovalRequest, ref: str) -> None:
        if request.status is not ApprovalStatus.PENDING:
            raise StoreError(f"approval {request.id} must be added pending, not {request.status}")
        with self._db:
            self._db.execute(
                "INSERT INTO approvals (id, ref, status, body) VALUES (?, ?, ?, ?)",
                (request.id, ref, request.status.value, request.model_dump_json()),
            )

    def approval(self, approval_id: str) -> StoredApproval | None:
        row = self._db.execute(
            "SELECT body, ref FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        if row is None:
            return None
        return StoredApproval(ApprovalRequest.model_validate_json(row[0]), row[1])

    def pending_approvals(self) -> list[ApprovalRequest]:
        """Every approval request still pending, oldest first."""
        rows = self._db.execute(
            "SELECT body FROM approvals WHERE status = ? ORDER BY rowid",
            (ApprovalStatus.PENDING.value,),
        ).fetchall()
        return [ApprovalRequest.model_validate_json(body) for (body,) in rows]

    def pending_approval_for(self, ref: str) -> ApprovalRequest | None:
        row = self._db.execute(
            "SELECT body FROM approvals WHERE ref = ? AND status = ?",
            (ref, ApprovalStatus.PENDING.value),
        ).fetchone()
        return None if row is None else ApprovalRequest.model_validate_json(row[0])

    def record_approval(
        self,
        approved: ApprovalRequest,
        ref: str,
        attempt_at: dt.datetime,
        consent: StoredConsent | None = None,
    ) -> None:
        """Approve a pending request and record the attempt to pay it, in one transaction,
        with the bank consent that waits for the owner when the bank asks for one.

        A crash can leave the request pending, or approved with its attempt on record,
        never approved with no attempt (a ref that could be neither paid nor retried).
        """
        if approved.status is not ApprovalStatus.APPROVED:
            raise StoreError(f"approval {approved.id}: expected approved, got {approved.status}")
        with self._db:
            self._decide(approved)
            self._db.execute(
                "INSERT INTO attempts (ref, at) VALUES (?, ?)", (ref, attempt_at.isoformat())
            )
            if consent is not None:
                if consent.consent.ref != ref:
                    raise StoreError(f"consent {consent.consent.id} is not for {ref}")
                self._insert_consent(consent)

    def _insert_consent(self, stored: StoredConsent) -> None:
        """Inside the caller's transaction: keep an open consent."""
        if not stored.payment_id:
            raise StoreError(f"consent {stored.consent.id} needs the bank's payment id")
        consent = stored.consent
        self._db.execute(
            "INSERT INTO consents (id, ref, payment_id, why, open, body) VALUES (?, ?, ?, ?, 1, ?)",
            (consent.id, consent.ref, stored.payment_id, stored.why, consent.model_dump_json()),
        )

    def record_decline(self, declined: ApprovalRequest, receipt: Receipt) -> None:
        """Decline a pending request and write its receipt, in one transaction."""
        if declined.status is not ApprovalStatus.DECLINED:
            raise StoreError(f"approval {declined.id}: expected declined, got {declined.status}")
        with self._db:
            self._decide(declined)
            self._db.execute(
                "INSERT INTO receipts (id, ref, body) VALUES (?, ?, ?)",
                (receipt.id, receipt.ref, receipt.model_dump_json()),
            )

    def _decide(self, decided: ApprovalRequest) -> None:
        """Inside the caller's transaction: record the decision on a pending request."""
        cursor = self._db.execute(
            "UPDATE approvals SET status = ?, body = ? WHERE id = ? AND status = ?",
            (
                decided.status.value,
                decided.model_dump_json(),
                decided.id,
                ApprovalStatus.PENDING.value,
            ),
        )
        if cursor.rowcount != 1:
            raise StoreError(f"approval {decided.id} is not pending")


def _stored_consent(row: tuple[str, str, str]) -> StoredConsent:
    body, payment_id, why = row
    return StoredConsent(BankConsent.model_validate_json(body), payment_id, why)


def _stored_advance(row: tuple[str, str, str]) -> StoredAdvance:
    body, event_id, status = row
    return StoredAdvance(Offer.model_validate_json(body), event_id, AdvanceStatus(status))
