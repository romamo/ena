### Fixed

- `get_books` matches an invoice the gate paid: each payment the gate made is a statement line, on the receipt's day, to the supplier, with the invoice number in its reference, unless the statement already pays the invoice; and a payment carrying the invoice number matches from the invoice's issue date to 7 days after its due date, so an invoice paid on arrival matches. The balance and the forecast are unchanged (#59)
