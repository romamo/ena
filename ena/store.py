"""SQLite store for receipts and approval requests (docs/build-plan.md §4, §6).

Receipts are append-only: the store can insert and read them, never change or delete one.
At most one receipt exists per ref, so a payment can never be recorded twice.
An approval request is inserted pending and decided once; its ref is kept beside it.
A payment attempt is recorded for a ref before the bank is called, also append-only,
so a payment whose outcome is unknown is never sent twice.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ena.models import ApprovalRequest, ApprovalStatus, Receipt

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
        self._db = sqlite3.connect(path)
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

    def pending_approval_for(self, ref: str) -> ApprovalRequest | None:
        row = self._db.execute(
            "SELECT body FROM approvals WHERE ref = ? AND status = ?",
            (ref, ApprovalStatus.PENDING.value),
        ).fetchone()
        return None if row is None else ApprovalRequest.model_validate_json(row[0])

    def decide_approval(self, decided: ApprovalRequest) -> None:
        """Record the decision on a pending request; fail when it isn't pending."""
        if decided.status is ApprovalStatus.PENDING:
            raise StoreError(f"approval {decided.id}: a decision cannot be pending")
        with self._db:
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
