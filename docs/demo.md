# The stage demo

The talk has 4 minutes in all, so the live demo gets about a minute and a half. The demo runs on the hosted site, https://ena.cloudfall.dev, from the projector laptop's browser, and pays through the bocAPI sandbox: each payment is confirmed on Bank of Cyprus's own screen. The two-laptop setup below is the fallback if the hosted site is down, and the backup video is the fallback for both.

## The hosted demo

The site runs `ena-serve --bank boc` with the synthetic month as of 16 November (`deploy/README.md`). Nothing runs on the laptop: it needs only a browser, the deck and the backup video. Every question goes to the model live, since the hosted server runs no reply cache.

All visitors share one store. One visitor's approvals and receipts are what the next one sees, so a payment made before the talk changes the stage path: "Pay the flour invoice" then answers that it is already paid instead of showing the approval card.

### Before the talk

1. A person resets the store over SSH, with the step in `deploy/README.md` (stop `ena`, remove `/srv/apps/ena/web/shared/ena.db`, start `ena`)
2. Check the site from the laptop:

   ```bash
   curl -s https://ena.cloudfall.dev/bank
   ```

   ```bash
   curl -s https://ena.cloudfall.dev/dashboard/overview | jq '.pending, .unpaid_invoices, .unpaid_cents'
   ```

   `{"live":true}`, then `[]`, `2` and `142000` (the €1,240 flour and €180 dairy invoices) mean the site is ready. `{"live":false}` means the server runs the stand-in bank: the payment is simulated and no BoC screen appears
3. Check Jinius: `curl -s https://ena.cloudfall.dev/jinius/invoices | jq '.source, [.invoices[].number]'` answers as in [Check Jinius before the talk](#check-jinius-before-the-talk)
4. The app in the BoC sandbox portal (My Apps) has `https://ena.cloudfall.dev/boc/consent` as its redirect URI; without it BoC's screen never sends the browser back to Ena
5. Don't use the hosted chat after the reset. A rehearsal on it pays the invoices for real in the sandbox; reset the store again after one

Open https://ena.cloudfall.dev in a phone-sized window (the browser's device mode at about 400 pixels wide), zoomed so the back row can read it, and keep the BoC test login at hand: `999999` / `112233`, OTP `123456`.

### The hosted path on stage

1. Dashboard → **Jinius** → **Read invoices from Jinius**: EFM-2026-002, €1,240 from Ena Flour Mill Ltd, "Waiting approval", under a green "Live" line; back to the chat with "Back to chat"
2. "Pay the flour invoice": the €1,240 approval card, above the €500 limit
3. Approve: the "Confirm in Bank of Cyprus" card (BoC sandbox, €1,240.00). Nothing is paid until the bank confirms it
4. **Confirm in Bank of Cyprus**: BoC's own screen opens in a new tab. Log in as `999999` / `112233`, pick the account, enter OTP `123456` and confirm. Say "this is the bank's own consent screen, not ours"
5. BoC sends the browser back to `https://ena.cloudfall.dev/boc/consent`, which executes the payment: the receipt, labelled "BoC sandbox payment", bank status `CPLT`
6. "Will I be short of cash this month?": the forecast flags the week of 16 November
7. "Can I get an advance for the Georgiou wedding's costs?": the advance offer

The consent adds about 30 seconds, so say the rest in words. Only one confirmation waits at a time: if an earlier one is still waiting, tap Cancel on its card before step 2. A consent's code works once, so a failed return means a new payment, not a retry. If BoC's screen errors or doesn't send the browser back, say so and switch to the backup video.

The card payment through JCC works on the hosted site without a tunnel: the JCC portal's webhook points at `https://ena.cloudfall.dev/jcc/callback` (`deploy/README.md`). Run the steps in [The card payment through JCC](#the-card-payment-through-jcc-optional) from step 1; there's no terminal to watch, so check the booking with step 4's books question.

## The fallback: two laptops

One laptop runs Ena (the API, the agent and the chat page); the other is plugged into the projector and only opens a browser. If the agent laptop goes down, the projector laptop still has the deck and the backup video.

| Laptop | Runs | Needs |
|---|---|---|
| Agent | `ena-serve` on port 8000, the chat page's dev server on port 5173 | This repository after `uv sync` and `npm ci` in `web/`; `ANTHROPIC_API_KEY` in the shell; the Jinius buyer's client (`JINIUS_CLIENT_ID`, `JINIUS_CLIENT_SECRET`) in `.env` for the Jinius tab; the JCC settings in `.env` if the card payment is shown |
| Projector | A browser at the agent laptop's address, the deck, the backup video | Nothing from this repository |

## The network

Both laptops join one network the team controls: a phone's hotspot. Venue Wi-Fi often keeps its clients from reaching each other, and on an open network anyone on it could reach the chat and its Approve button. Check before the talk that the projector laptop opens the page; if it doesn't, use the tunnel below.

## Start the agent laptop

In the first terminal, start the API with the reply cache filled at the rehearsal:

```bash
uv run ena-serve --cache tmp/demo-cache.json
```

In a second terminal, start the chat page for the network:

```bash
cd web
npm run dev -- --host
```

The page's dev server proxies the API's routes (`/chat`, `/approvals`, `/dashboard`, `/jinius` and the rest, `web/vite.config.ts`) to `ena-serve` on the same laptop, so the API itself keeps listening on `127.0.0.1` only. Find the laptop's address on the hotspot:

```bash
ipconfig getifaddr en0
```

## Open it on the projector laptop

Open `http://<agent laptop's address>:5173` in a phone-sized window (the browser's device mode at about 400 pixels wide is enough), zoomed so the back row can read it. Vite accepts a request addressed by IP without any change to its config.

## Check Jinius before the talk

The Jinius tab reads the invoices sent to DUMMY, the buyer's legal entity, live from the Jinius integration environment each time the button is tapped; nothing is cached. On the agent laptop, with `ena-serve` running:

```bash
curl -s http://127.0.0.1:8000/jinius/invoices | jq '.source, [.invoices[].number]'
```

`"live"` and the two invoice numbers (`EFM-2026-001`, `EFM-2026-002`) mean the tab will show them live. Anything else is fixed before the talk, not on stage:

| Answer | Meaning | Fix |
|---|---|---|
| `"fixture"` | Jinius couldn't be reached; the tab shows the saved responses, marked "Saved data" | Check the hotspot reaches `api.integration.platform.cy`; if it can't, say "saved data" on stage |
| 502, "403 with no body" | DUMMY's plan lacks the invoice API | Settings → Subscription in the portal: Enterprise (`docs/jinius-notes.md`, section 0) |
| 503, "Jinius is not configured" | No `JINIUS_CLIENT_ID` in `.env` | Copy the Client ID and Secret from Settings → Developer, restart `ena-serve` |

## What the reply cache covers

`--cache` answers a demo question the same way every time and at once, but only a reply that moved no money is cached: a reply with an approval or a payment always goes to the model (`ena/cache.py`). So "Pay the flour invoice" and its Approve call the model live on stage, and the forecast, reserve, loan and advance questions come from the cache once a rehearsal has asked them on the same day and ledger state. Fill the cache by running the demo path once with the same `--cache` file and the same `--day`, then keep the file.

## The two-laptop path on stage

1. Dashboard → **Jinius** → **Read invoices from Jinius**: EFM-2026-002, €1,240 from Ena Flour Mill Ltd, "Waiting approval", under a green "Live" line. This is the one step that shows a partner API working live; back to the chat with "Back to chat"
2. "Pay the flour invoice": the €1,240 approval card, above the €500 limit
3. Approve: the receipt, marked simulated
4. "Will I be short of cash this month?": the forecast flags the week of 16 November
5. "Can I get an advance for the Georgiou wedding's costs?": the advance offer

Say the rest in words. In step 1, say "this is the feed Ena reads from Jinius", not "the invoice Ena pays": the chat runs on the synthetic month, whose flour invoice comes from Mesaoria Mills, and approving it changes nothing in Jinius. The page's "Synthetic demo" banner stays visible above the Jinius tab, so say that the tab is the live part.

## The card payment through JCC (optional)

About 20 seconds after the advance offer, if the time allows. It needs the JCC keys and `JCC_RETURN_URL` in `.env`, and a tunnel to port 8000 that JCC's callback reaches:

```bash
ngrok http 8000
```

In the test Merchant Portal, set the webhook (Settings → Merchant → Webhooks) to Static, POST, symmetric, operation Deposited, URL `https://<ngrok host>/jcc/callback`, and save it: the sandbox sent no callback for `dynamicCallbackUrl` alone (`docs/jcc-notes.md`, section 0). Rehearse it once on the day.

1. "Make a €15 pay link for two loaves and a coffee": the pay link card with its QR code
2. Scan it with a phone and pay with the test card `4444 5555 1111 3333`, CVC `123`, expiry `12/26` (no 3D Secure)
3. About 10 seconds later the `ena-serve` terminal logs `POST /jcc/callback` with 200: the sale is booked as `jcc-<order id>`
4. "Show my books from 16 to 30 November": the €15.00 sale, unmatched until JCC settles it to the account

A live sale is dated the real day, not the demo's 16 November, so it shows in the books but not in the forecast. If no callback comes within about 20 seconds, say so and move on. Set `JCC_RETURN_URL` to the public address followed by `/jcc/paid` (the tunnel's, or `https://ena.cloudfall.dev/jcc/paid` on the hosted demo): after paying, the phone shows Ena's thank-you page for the customer, which claims no result and links nowhere into Ena. The booking doesn't depend on it; JCC's callback books the sale.

## If the laptops can't see each other

Expose the page through a tunnel on the agent laptop:

```bash
cloudflared tunnel --url http://localhost:5173
```

Vite refuses a request for a host name it doesn't know, such as `*.trycloudflare.com`, so the tunnel needs that host in `server.allowedHosts` in `web/vite.config.ts` first. The tunnel's address is public: anyone who has it can chat and approve, and every question spends the Anthropic key, so close the tunnel after the talk.

## If it fails on stage

Switch the projector to the backup video and keep talking; don't debug on stage.
