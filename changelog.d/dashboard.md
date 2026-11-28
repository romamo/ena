### Added

- The owner's dashboard, opened from the chat page's header: Overview (the balance, what needs the owner's OK, the lean week, what Ena paid on its own and how often the owner stepped in), Receipts, the cash Forecast drawn against its safe line, and Loans, which says whether the forecast's lean week needs a loan, the owner's own reserve or nothing, and shows the gap. Read only, from `GET /dashboard/overview`, `/receipts`, `/forecast` and `/credit` (`ena/dashboard.py`): every figure is computed by the API from the ledger and the existing reserve and loan rules, no route pays, approves or writes a receipt, and a pending payment is still decided on its approval card
- The chat and the dashboard link both ways: every card in the chat opens the dashboard on its tab, the dashboard's questions are sent to the chat, and approving on the dashboard reads the overview again

### Changed

- The chat page's buttons and chips are square-cornered
