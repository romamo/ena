### Added

- `ena-serve --bank boc` pays through the bocAPI sandbox: a payment the gate makes is initiated in the sandbox and shown as a "Confirm in Bank of Cyprus" card; BoC's screen sends the browser back to `GET /boc/consent`, which executes it and writes a receipt labelled "BoC sandbox payment". `GET /consents/{id}`, `POST /consents/{id}/cancel` and `GET /bank` serve the page
- The agent's `get_bank_account` tool and the page's account card show the sandbox account's live balance and statement, apart from the synthetic month
- `BOC_ACCOUNT_ID`, `BOC_CREDITOR_ACCOUNT` and `BOC_CREDITOR_BANK` settings

### Fixed

- The page's dev server proxies `/offers`, so Accept on an event advance reaches the API
