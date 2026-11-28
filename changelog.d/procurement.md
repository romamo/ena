### Added

- Procurement:
  - Ena drafts requests for quotation (`request_quotes`) and compares the suppliers' quotations: complete, on time and still valid, then the cheapest
  - The comparison card recommends one, and the owner orders from any quotation that still holds with one tap (`POST /quotes/{id}/award`), which issues the purchase order and the owner's receipt
  - Jinius has no API for requests for quotation or purchase orders, so both are sent outside Jinius and labelled simulated (docs/jinius-notes.md §9)
- Pay against the order: an invoice that names a purchase order is checked against it first. A different supplier or amount is held (`pay_invoice` returns `held`) and never reaches the gate
- The synthetic month's flour invoice now bills an order placed before Ena. A second request, for the Georgiou wedding's supplies, has three quotations to compare on the lean Monday, and the chat offers that question as a chip
