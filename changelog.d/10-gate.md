### Added

- The gate in `ena/gate.py`, the one door for money: at or below the business's auto-pay limit it pays with a receipt by Ena, above it it creates a pending approval that only the owner's approve (pays once) or decline (pays nothing) settles; every settled payment has exactly one receipt, a duplicate trigger gets the same receipt or approval back, and a status other than `CPLT` writes a failed receipt with no retry. `ena/store.py` keeps receipts (append-only, one per ref) and approvals in SQLite (#10)
