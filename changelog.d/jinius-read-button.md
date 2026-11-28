### Added

- The dashboard's Jinius tab has a "Read invoices from Jinius" button: `GET /jinius/invoices` reads the invoices sent to the business from the Jinius integration environment and shows each one's supplier, amount, dates and Jinius status, marked live or saved data. It only reads; without `JINIUS_CLIENT_ID` in `.env` it answers 503 and names the missing settings
