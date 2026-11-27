# JCC payment gateway: integration notes

Read 2026-11-27 from JCC's public developer docs, before any live call. Nothing here has been tried against the sandbox yet; anything the docs do not state is marked UNKNOWN.

Sources:
- Portal index: https://gateway.jcc.com.cy/developer
- REST API guide (authentication, `register.do`, callbacks, signatures, refunds): https://gateway.jcc.com.cy/developer/en/integration/api/rest.html
- Order status, refund, reversal: https://gateway.jcc.com.cy/developer/en/integration/api/scripts.html
- Pay by Link (invoices): https://gateway.jcc.com.cy/developer/en/integration/structure/pay-by-link.html
- Permanent payment links: https://gateway.jcc.com.cy/developer/en/integration/structure/permanent-payment-links.html
- Merchant Portal guide (webhook settings, API users): https://gateway.jcc.com.cy/developer/en/integration/mportal3/mp3.html
- Test cards: https://gateway.jcc.com.cy/developer/en/integration/structure/test-cards.html
- Test to production: https://gateway.jcc.com.cy/developer/en/integration/structure/test-to-production.html

## 1. Access

- **Sandbox sign-up:** the portal links "Create Sandbox" to `https://gateway.jcc.com.cy/developer/cabinet/index.html#/` (a JavaScript app; its public page shows no fields, terms or description). What a sign-up issues is UNKNOWN; the docs imply a test merchant with a Personal Area (operator) login and an API user
- **Test Personal Area (Merchant Portal):** `gateway-portal-test.jcc.com.cy`
- **API credentials:** either `userName` + `password` of the merchant's API user (its login ends in `-api`; the password is generated in the Personal Area under "API users", needs 2FA and is shown once), or a `token` issued by JCC support. Never both in one request
- **Callback key:** a symmetric signing secret, generated or entered in Settings > Merchant > Webhooks (or obtained from support)
- **Base URLs:**
  - test: `https://gateway-test.jcc.com.cy/payment/rest/`
  - production: `https://gateway.jcc.com.cy/payment/rest/` (the docs say to confirm it with support; not needed for the demo)

For `.env.example`:

```
JCC_BASE_URL=https://gateway-test.jcc.com.cy/payment/rest/
JCC_API_USER=
JCC_API_PASSWORD=
JCC_CALLBACK_SECRET=
JCC_RETURN_URL=
JCC_CALLBACK_URL=
```

## 2. Create a payment link

The docs describe **no API for Pay by Link invoices, permanent payment links or QR codes**: all three are created by hand in the Merchant Portal UI.
- **Invoices ("Pay by Link"):** Invoices > Create invoice; a due date limits payment; optional email/SMS to the customer; CSV batch upload
- **Payment links (permanent):** Payment links > Create; fixed or "Arbitrary" (customer-entered) amount; valid one month by default or "Indefinite"; a QR code downloads as PNG, SVG or JPEG
- **Universal QR:** not found anywhere in the public docs. UNKNOWN

For code, the equivalent is **order registration**: the response's `formUrl` is a hosted payment page URL that works as a one-off pay link and can be rendered as a QR by us.

- **Method and path:** `POST {JCC_BASE_URL}register.do` (`registerPreAuth.do` for two-phase; not needed)
- **Body:** `application/x-www-form-urlencoded` (JSON is for signed requests). Parameters go in the body, not the URL
- **Auth:** `userName` and `password` in the body
- **Key request fields:**
  - `orderNumber`: mandatory, unique per merchant, up to 36 chars; a duplicate returns `errorCode` 1
  - `amount`: mandatory, integer in **minor units** (cents), so it maps to our `amountCents`
  - `currency`: ISO 4217 numeric, `978` for EUR; the merchant default applies if omitted
  - `returnUrl`: mandatory, absolute URL with protocol (a relative one gives `errorCode` 4); `failUrl` optional
  - `description`: optional, up to 598 chars, no personal or card data
  - `dynamicCallbackUrl`: optional per-order callback address for payment events (may be mandatory depending on merchant settings)
  - `sessionTimeoutSecs` (default 1200 s, 20 minutes) or `expirationDate` (`yyyy-MM-ddTHH:mm:ss`): how long the link stays payable
  - `language`, `email`, `jsonParams` (for example `backToShopUrl`): optional
- **Response:** `orderId` (gateway id, up to 36 chars; our `jccOrderId`), `formUrl` (the link), `errorCode` (`0` or absent = success, `1`-`99` = error) and `errorMessage` (do not match on its text)
- A successful registration is not a payment: confirm through the callback or order status

## 3. Callback

- **Configuration:** Personal Area, Settings > Merchant > Webhooks: enable, webhook URL (several, comma-separated), type static or dynamic, method GET or POST, signing type symmetric or asymmetric, extra parameters, and which operations notify. The section may be hidden for some merchants (then ask support). A `dynamicCallbackUrl` on `register.do` overrides the address for that order's payment events
- **Transport:** HTTPS on port 443 only. GET puts the fields in the query string, POST in the body
- **Fields:** `mdOrder` (gateway order id), `orderNumber` (ours), `operation`, `status`, `checksum`; examples also carry `callbackCreationDate`; extras (such as `paymentState`, `panMasked`) only if configured
  - `operation`: `approved`, `deposited`, `reversed`, `refunded`, `declinedByTimeout`, `bindingCreated`, `bindingActivityChanged`
  - `status`: `1` success, `0` failure (failed operations notify too)
  - For a one-phase payment, the operation that books our CardSale is presumably `deposited` with `status=1`; ASSUMED until the first real callback
- **Checksum (symmetric):** drop `checksum` and `sign_alias`; sort the remaining fields by name ascending; join as `name;value;` for each (for example `mdOrder;<id>;operation;deposited;orderNumber;<n>;status;1;`); HMAC-SHA256 with the shared secret; hex, uppercase; compare with `checksum` (constant-time). Checksums may need enabling by support. Asymmetric mode signs with JCC's private key; the public key is downloadable from the console; not needed for the demo
- **Retries:** anything other than HTTP 200 is a failure; the gateway retries every 30 seconds, up to 6 consecutive failures. So: verify, store idempotently by `mdOrder` + `operation`, answer 200 fast
- **Custom headers** (for example `Authorization`) can be added by support
- **Sender IP addresses:** UNKNOWN
- **Whether payments through portal invoices or permanent links fire the merchant webhook:** UNKNOWN (likely, since webhooks are merchant-level, but not stated)
- **Local demo:** the callback must reach HTTPS 443, so expose the local API through a tunnel (for example `cloudflared tunnel --url http://localhost:8000` gives a public `https://*.trycloudflare.com`) and pass that URL as `dynamicCallbackUrl` (no portal change per tunnel). If the tunnel or callback fails, poll order status instead (section 4) after the customer lands on `returnUrl`, or on a short timer

## 4. Order status

- **Method and path:** `POST {JCC_BASE_URL}getOrderStatusExtended.do` with `userName`, `password` and `orderId` (or `orderNumber`; `orderId` wins if both are sent), optional `language`
- **Response fields:** `errorCode`, `errorMessage`, `orderNumber`, `orderStatus`, `actionCode`, `actionCodeDescription`, `amount`, `currency`, `date`, `depositedDate`, `orderDescription`, `paymentAmountInfo.paymentState` (`CREATED`, `APPROVED`, `DEPOSITED`, `DECLINED`, `REVERSED`, `REFUNDED`), `attributes` and more
- **`orderStatus`:**
  - `0` registered, not paid
  - `1` pre-authorized, funds on hold (two-phase; needs capture)
  - `2` authorized and captured: **paid**
  - `3` authorization cancelled
  - `4` refunded
  - `5` 3-D Secure authorization started by the issuer's ACS
  - `6` authorization declined
  - `7` payment pending
  - `8` intermediate completion (multiple partial captures)
- Paid means `orderStatus` 1 or 2; with one-phase `register.do` expect 2
- `actionCode` explains a decline (codes at `/developer/en/integration/api/action_codes.html`)

## 5. Refund (not in the demo)

`POST {JCC_BASE_URL}refund.do` with `userName`, `password`, `orderId` (or `orderNumber`), `amount` in minor units (`0` = full); optional `externalRefundId`, `currency`. Works on deposited orders, repeatable up to the captured amount. `reverse.do` releases a hold; `decline.do` cancels an unpaid order. The callback then sends `operation=refunded`.

## 6. Test cards and scenarios

| Card | CVC | Expiry | Flow | Result |
|---|---|---|---|---|
| 4111 1111 1111 1111 | 123 | 12/26 | 3DS2 frictionless | Success |
| 4444 5555 1111 3333 | 123 | 12/26 | SSL, no 3DS | Success |
| 5555 5555 5555 5599 | 123 | 12/34 | 3DS2 full challenge | Success |
| 4000 0011 1111 1118 | 123 | 12/30 | 3DS2 attempt | Success |
| 5168 4948 9505 5780 | 123 | 12/26 | 3DS2 frictionless | Failure |

- **Success:** any success card with the right data. 4444 5555 1111 3333 (no 3DS) is the quickest on stage
- **Decline:** the failure card, or a wrong CVC or expiry (action code 71015, "Decline. Input error")
- 3DS challenge password for 5555 5555 5555 5599: UNKNOWN
- Expiry 12/26 is one month away from the hackathon; fine for now

## 7. Recommended minimal integration (2 days)

1. Sign up for the sandbox, get the test merchant, generate the API user password, set the webhook secret (Settings > Merchant > Webhooks, POST, symmetric, operations `deposited` and `declinedByTimeout`)
2. `connectors/jcc.py`: `register(order_number, amount_cents, description) -> (order_id, form_url)` and `status(order_id) -> orderStatus`; save raw responses to `fixtures/jcc/`
3. Tool `create_pay_link(amount, description)` calls `register.do` with `dynamicCallbackUrl` = tunnel URL + `/jcc/callback`; the UI shows `formUrl` as a link and a QR (generated by us)
4. `api.py` `/jcc/callback`: verify the checksum, then on `deposited` + `status=1` call `getOrderStatusExtended.do` to confirm amount and `orderStatus=2`, book the CardSale (`jccOrderId=mdOrder`), feed the forecast, return 200. Idempotent on `mdOrder`
5. Demo: pay with 4444 5555 1111 3333 on a phone; the callback books it live

**Fallback** (no tunnel, no webhook, or webhooks hidden): after payment the customer lands on our `returnUrl` (which can be `http://localhost` only if the paying browser is the demo laptop; UNKNOWN whether http is accepted), or the server polls `getOrderStatusExtended.do` every few seconds for the open order; `orderStatus=2` books the sale. Last resort: replay a saved callback and status response from `fixtures/jcc/`, and the UI says "sandbox offline, showing saved data".

## 8. Open questions for JCC mentors

1. What does the self-service sandbox issue: an API user (`-api` login), Personal Area access, the Webhooks section, and a webhook secret? Any approval delay?
2. Is there an API to create Pay by Link invoices or permanent payment links, or is `register.do` + `formUrl` the intended route for software?
3. What is "Universal QR" (named in the hackathon brief) and is it in the sandbox? Is there an API?
4. Do payments made through a portal invoice or permanent link fire the merchant webhook, and with which `orderNumber`?
5. Is `dynamicCallbackUrl` allowed in the sandbox, and does it accept a tunnel host such as `*.trycloudflare.com`?
6. Is the callback checksum on by default in the sandbox, or does support have to enable it?
7. Which `operation` does a one-phase sandbox payment send: `approved`, `deposited`, or both?
8. Sender IPs of callbacks, if we want to allowlist them
9. 3DS challenge password for the full-3DS test card
10. Settlement: when and how does a card sale reach the merchant's BoC account (delay, batching, fees), so the books can match the deposit (build plan assumes 3 days)? Is there a settlement report API?
