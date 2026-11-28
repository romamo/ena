### Fixed

- Asked again to pay an invoice that waits for approval, the owner sees the same approval card with Approve and Decline: the agent is told to call `pay_invoice` again (the gate returns the same approval and pays nothing twice), and a reply that speaks of an approval shows the pending approvals no tool showed that turn
