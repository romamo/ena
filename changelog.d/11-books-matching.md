### Added

- Books matching in `ena/rules/books.py`: payments out are matched one to one to supplier invoices (equal amount, within 7 days of the due date, by the invoice number in the reference or the supplier as counterparty) and deposits to card sales (by the JCC order id, or by amount within 3 days after the sale), with everything else returned as unmatched (#11)
