# The stage demo: two laptops

The talk has 4 minutes in all, so the live demo gets about a minute and a half. One laptop runs Ena (the API, the agent and the chat page); the other is plugged into the projector and only opens a browser. If the agent laptop goes down, the projector laptop still has the deck and the backup video.

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

## The path on stage

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
