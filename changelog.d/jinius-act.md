### Added

- The Jinius tab acts on a live invoice on the owner's tap: **Approve on Jinius** on an invoice waiting approval, then **Mark paid on Jinius**, which reports it paid in full today with the reference `ENA-<invoice number>` and says no money moved. `POST /jinius/invoices/{id}/approve` and `/mark-paid` answer with the invoice read again from Jinius; mark paid before approval is refused with 409. No agent tool reaches them, and saved data offers no tap
