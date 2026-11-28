### Fixed

- `ena-serve` reads the Jinius, JCC, bocAPI and agent settings from `.env` and the environment it is given, so a variable exported in the developer's shell no longer changes what the tests load
- The Jinius tab refuses an invoice in a currency other than euro, or a credit note, instead of showing its amount with a euro sign
- A Jinius answer that arrives after the owner switched tabs is kept, and each invoice in it is checked before it is shown
- Two concurrent Jinius reads fetch one token, not two
