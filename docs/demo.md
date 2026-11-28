# The stage demo: two laptops

The talk has 4 minutes in all, so the live demo gets about a minute and a half. One laptop runs Ena (the API, the agent and the chat page); the other is plugged into the projector and only opens a browser. If the agent laptop goes down, the projector laptop still has the deck and the backup video.

| Laptop | Runs | Needs |
|---|---|---|
| Agent | `ena-serve` on port 8000, the chat page's dev server on port 5173 | This repository after `uv sync` and `npm ci` in `web/`; `ANTHROPIC_API_KEY` in the shell; the JCC settings in `.env` if the card payment is shown |
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

The page's dev server proxies `/chat` and `/approvals` to `ena-serve` on the same laptop, so the API itself keeps listening on `127.0.0.1` only. Find the laptop's address on the hotspot:

```bash
ipconfig getifaddr en0
```

## Open it on the projector laptop

Open `http://<agent laptop's address>:5173` in a phone-sized window (the browser's device mode at about 400 pixels wide is enough), zoomed so the back row can read it. Vite accepts a request addressed by IP without any change to its config.

## What the reply cache covers

`--cache` answers a demo question the same way every time and at once, but only a reply that moved no money is cached: a reply with an approval or a payment always goes to the model (`ena/cache.py`). So "Pay the flour invoice" and its Approve call the model live on stage, and the forecast, reserve, loan and advance questions come from the cache once a rehearsal has asked them on the same day and ledger state. Fill the cache by running the demo path once with the same `--cache` file and the same `--day`, then keep the file.

## The path on stage

1. "Pay the flour invoice": the €1,240 approval card, above the €500 limit
2. Approve: the receipt, marked simulated
3. "Will I be short of cash this month?": the forecast flags the week of 16 November
4. "Can I get an advance for the Georgiou wedding's costs?": the advance offer

Say the rest in words. The card payment through JCC is optional: its callback reaches the agent laptop only through a tunnel to port 8000 (`docs/jcc-notes.md`, section 3).

## If the laptops can't see each other

Expose the page through a tunnel on the agent laptop:

```bash
cloudflared tunnel --url http://localhost:5173
```

Vite refuses a request for a host name it doesn't know, such as `*.trycloudflare.com`, so the tunnel needs that host in `server.allowedHosts` in `web/vite.config.ts` first. The tunnel's address is public: anyone who has it can chat and approve, and every question spends the Anthropic key, so close the tunnel after the talk.

## If it fails on stage

Switch the projector to the backup video and keep talking; don't debug on stage.
