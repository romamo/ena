### Added

- JCC callback: `POST /jcc/callback` verifies the checksum, confirms a `deposited` payment with `getOrderStatusExtended.do` and books one `CardSale` per JCC order in the store (#9)
