"""SQLite store for receipts and approval requests (docs/build-plan.md §4, §6).

Receipts are append-only: the store can insert and read them, never change or delete one.
At most one receipt exists per ref, so a payment can never be recorded twice.
An approval request is inserted pending and decided once, in the same transaction as
its next write (the attempt to pay, or the declined receipt); its ref is kept beside it.
A payment attempt is recorded for a ref before the bank is called, also append-only,
so a payment whose outcome is unknown is never sent twice.
Card sales booked from JCC are append-only too, at most one per JCC order id, so a
repeated callback can never book a sale twice.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ena.models import ApprovalRequest, ApprovalStatus, CardSale, Receipt

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

    def record_approval(self, approved: ApprovalRequest, ref: str, attempt_at: dt.datetime) -> None:
        """Approve a pending request and record the attempt to pay it, in one transaction.

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
