### Added

- Ena's MCP tools (`ena/mcp_server.py`): balance, transactions, unpaid invoices, books, the 8-week forecast, receipts, `pay_invoice` through the gate, and the reserve and simulated loan offers; money tools take ids, never amounts, and no tool approves or changes the limit (#15)
- The agent (`ena/agent.py`) on the Claude Agent SDK with only Ena's tools and the answer rules in its prompt; the model comes from `ENA_MODEL` (default `claude-opus-5-5`) (#15)
- Chat over SSE (`POST /chat`) with approval, receipt, forecast and offer cards, and the owner's tap (`POST /approvals/{id}/approve` and `/decline`), which calls the gate, never the agent (#15)
- No "paid" without a receipt: a reply that claims a payment no tool returned a paid receipt for in that turn is marked unconfirmed (#15)
- A reply cache for the demo questions, keyed on the seed, the day, the ledger's state and the question, that never caches a reply that moved money or offered a loan (`ena/cache.py`) (#15)
