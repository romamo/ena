### Added

- JCC pay links: the agent's `create_pay_link` registers a JCC order (`register.do`) for the amount the owner names and the chat shows it as a card with a QR code and the payment page; it moves no money, writes a receipt that Ena created it, and the sale is booked only when JCC's callback confirms the deposit. It needs `JCC_RETURN_URL` next to the JCC keys in `.env` (#86)
