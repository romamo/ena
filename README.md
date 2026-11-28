# Ena

Ena is an AI back-office agent that pays a micro-business's supplier invoices from its Bank of Cyprus account within the owner's limit, keeps its books, forecasts its cash and offers a reserve or a loan before money runs short.

Built at [BoC Fintech Hackathon 7.0](https://bochackathon.net), Nicosia, 27 to 29 November 2026.

> This is the team's working copy. The final version is submitted to the organisers separately.

Ena is for micro-businesses and the self-employed who bank with Bank of Cyprus. A barber or a bakery owner pays supplier invoices from a phone, does the books at night and finds out cash is short when a payment bounces. Ena takes that work off them in one loop: it receives supplier invoices through Jinius, pays them from the BoC account, keeps the books from bocAPI and JCC card data, forecasts cash for the coming weeks and, when a lean month is ahead, sets money aside for loan installments or offers a BoC Digital Business Loan for working capital. The owner sets a limit: Ena pays on its own below it, asks for approval above it and leaves a receipt for every action.

## The loop

| Step | What Ena does | API | Code | Runs on today |
|---|---|---|---|---|
| Supplier invoice | Reads the invoices sent to the business and marks one paid | Jinius (`GET /v3/invoices/payable`) | [ena/connectors/jinius.py](ena/connectors/jinius.py) | The Jinius integration environment with credentials; without them, or when Jinius can't be reached, the saved responses in [fixtures/jinius/](fixtures/jinius/) |
| Payment | Pays at or below the owner's limit, asks for approval above it, and writes a receipt for every payment | bocAPI payments (sign, initiate, consent, execute to `CPLT`) | The gate [ena/gate.py](ena/gate.py) with [ena/store.py](ena/store.py); the bank calls in [ena/connectors/boc.py](ena/connectors/boc.py), the gate's bocAPI bank in [ena/connectors/boc_bank.py](ena/connectors/boc_bank.py) | A stand-in bank by default; the bocAPI sandbox with `ena-serve --bank boc` (below), where the owner confirms each payment in BoC's own screen |
| Books | Creates a card pay link with a QR code when the owner asks, and matches statement lines to paid invoices and to card sales | bocAPI statement; JCC card payments (`register.do`, callback, `getOrderStatusExtended.do`) | [ena/rules/books.py](ena/rules/books.py); the statement call in [ena/connectors/boc.py](ena/connectors/boc.py); the agent's `create_pay_link` in [ena/mcp_server.py](ena/mcp_server.py); the JCC callback `POST /jcc/callback` in [ena/api.py](ena/api.py) with [ena/connectors/jcc.py](ena/connectors/jcc.py), which books a confirmed card payment once | The synthetic month. The JCC callback runs in the tests on the saved sandbox responses in [fixtures/jcc/](fixtures/jcc/); see [docs/jcc-notes.md](docs/jcc-notes.md) for the live sandbox |
| Cash forecast | Projects 8 weeks of cash and flags the first lean week | bocAPI balance and statement | [ena/rules/forecast.py](ena/rules/forecast.py) | The synthetic month: week 3 (16 November) is lean |
| Credit offer | Sets money aside for the loan instalment, pre-fills a Digital Business Loan, or offers a short-term advance against a booked event's costs, which the owner accepts on its card and the gate repays from the event's final payment | None: the loan and advance offers are simulated, since the sandbox has no lending API | [ena/rules/reserve.py](ena/rules/reserve.py), [ena/rules/loan.py](ena/rules/loan.py), [ena/rules/events.py](ena/rules/events.py), [ena/advances.py](ena/advances.py) | The forecast's numbers; the synthetic catering events |

The synthetic month ([ena/seed.py](ena/seed.py)) is a bakery's November 2026 built from a fixed seed: a €180 dairy invoice under the €500 limit, a €1,240 flour invoice above it, card sales six days a week, the rent on 20 November and the loan instalment on 25 November. The bakery also caters events: 12 completed and 1 cancelled since January 2025, and the Georgiou wedding booked for 19 December, whose €4,800 of costs fall due before its €6,000 final payment arrives. [ena/replay.py](ena/replay.py) plays it as a time-ordered stream of events.

The owner sees all of it in one chat page ([web/](web/)): Ena's replies stream in with the approval, receipt, forecast, offer and pay link cards, and the owner approves or declines a payment above the limit with one tap.

The demo is hosted at https://ena.cloudfall.dev: the synthetic month as of 16 November, payments through the bocAPI sandbox (test money only), the page open to anyone with the address. [deploy/](deploy/README.md) declares its server and deploys a new release.

## Run the demo

Needs only [uv](https://docs.astral.sh/uv/getting-started/installation/) and the sample data in this repository: no credentials and no `.env`. uv installs the Python version the project pins (3.15) if it is missing.

```bash
git clone https://github.com/romamo/ena.git
cd ena
uv sync
uv run pytest
```

The tests cover the rules, the gate, the bocAPI, Jinius and JCC connectors, the JCC callback, the synthetic month and its replay, using hand-built cases and the saved sandbox responses; none of them calls a sandbox. The last line reports every test passed and none failed.

Then replay the synthetic month, 1 to 30 November, and print each event:

```bash
uv run python -c "
import datetime as dt
from ena.replay import replay
from ena.seed import synthetic_month

def show(event):
    record = getattr(event, 'transaction', None) or getattr(event, 'invoice', None) or getattr(event, 'sale', None)
    detail = '' if record is None else f'{record.id:<22} {record.amount_cents / 100:>9.2f}'
    print(f'{event.at:%a %d %b %H:%M}  {event.kind:<18}  {detail}')

events = replay(synthetic_month(), show, duration=dt.timedelta(0))
print(len(events), 'events, 1 to 30 November 2026')
"
```

`duration=dt.timedelta(0)` plays the month at once; leave it out and the month takes a minute of real time. The dairy invoice arrives on 2 November, the flour invoice on 4 November, the rent goes out on 20 November and the instalment on 25 November. The output ends with:

```text
Sun 29 Nov 06:00  transaction_posted  syn-tx-0148                42.28
Mon 30 Nov 00:00  day_started         
Mon 30 Nov 08:00  card_sale_received  syn-sale-20261130-1        41.27
Mon 30 Nov 10:00  card_sale_received  syn-sale-20261130-2        37.93
Mon 30 Nov 12:00  card_sale_received  syn-sale-20261130-3        35.42
184 events, 1 to 30 November 2026
```

Then start the API on http://127.0.0.1:8000 (the chat, the owner's approve and decline, the JCC callback), with the synthetic month as of 16 November and the store in `tmp/ena.db`; `uv run ena-serve --help` lists the flags. Its payments are simulated: a stand-in bank completes each one and no money moves, so each receipt is labelled simulated (`simulated: true`, its explanation ending "(simulated: no money moved)"). Without `ANTHROPIC_API_KEY` (or `CLAUDE_CODE_OAUTH_TOKEN`) in the shell `/chat` answers with an error event, and without the JCC settings in `.env` the callback refuses every call:

```bash
uv run ena-serve
```

### The chat page

In a second terminal, with [Node.js](https://nodejs.org) 22 or newer, install the page's packages from the lockfile and start it on http://localhost:5173:

```bash
cd web
npm ci
npm run dev
```

The page talks to the API through the dev server, so `ena-serve` must be running on port 8000 (set `ENA_API` to point the dev server elsewhere). Open http://localhost:5173 on a phone-sized window and tap a suggested question: "Pay the flour invoice" shows the €1,240 approval card, and Approve settles it through the stand-in bank and shows the receipt. A banner and a label on each card mark the data as synthetic, the payments as simulated and the loan as an offer, simulated. Without an Anthropic credential in `ena-serve`'s shell, each question answers with the error that names the missing key.

`web/.npmrc` turns off package install scripts. `npm test` runs the page's tests and `npm run build` type-checks it and builds it into `web/dist/`.

### The demo on the bocAPI sandbox

With `--bank boc` the gate pays through the bocAPI sandbox instead of the stand-in: Approve (or a payment within the limit) initiates the payment in the sandbox and shows a "Confirm in Bank of Cyprus" card. It opens BoC's own screen (test login `999999` / `112233`, OTP `123456`); after the confirmation BoC sends the browser back to Ena's `/boc/consent`, which executes the payment and writes the receipt, labelled "BoC sandbox payment". Every invoice is paid from `BOC_ACCOUNT_ID` to BoC's sample payee, since the synthetic suppliers have no sandbox account. A "Show my Bank of Cyprus sandbox account" chip reads that account's live balance and statement, shown on its own card and never added to the synthetic month's figures. The sandbox allows only a few such reads a day, so Ena keeps a read for 6 hours and shows the last copy, labelled, when the sandbox refuses a new one. One confirmation waits at a time; Cancel on its card stops waiting.

It needs an active subscription (`ena-boc subscribe` and `activate`, below) and, in `.env`:

```text
BOC_SUBSCRIPTION_ID=<from ena-boc activate>
BOC_ACCOUNT_ID=351012345671
BOC_REDIRECT_URI=http://localhost:5173/boc/consent
```

Set the same redirect URI on the app in the sandbox portal (My Apps), since BoC sends the browser only to the registered one. `ena-serve --bank boc` refuses to start without these. Then:

```bash
uv run ena-serve --bank boc
```

### On the sandboxes, with credentials

Not needed for the stand-in demo. To call the bocAPI sandbox, copy `.env.example` to `.env` and fill in the bocAPI app's client id and secret, then:

```bash
uv run ena-boc token
```

`uv run ena-boc --help` lists the steps from account consent to a payment that reaches `CPLT`. The Jinius and JCC variables in `.env.example` are for their sandboxes; [docs/boc-notes.md](docs/boc-notes.md), [docs/jinius-notes.md](docs/jinius-notes.md) and [docs/jcc-notes.md](docs/jcc-notes.md) record what each sandbox answered.

## Team

| Area | Owner |
|---|---|
| Software | Roman |
| Pitch on stage | Youssef |
| Presentation, idea work, customer interviews | Together |
| Demo | Keith |
| Submission and organisers | Youssef |

Members: Roman, Youssef and Keith.

## Repository

- [docs/product.md](docs/product.md): the mission, goals, non-goals and quality bar
- [docs/plan.md](docs/plan.md): the team plan: decisions, roles, weekend, pitch hook, the one number, open tasks
- [docs/build-plan.md](docs/build-plan.md): proposed architecture, data model, tools, rules, tasks and timeline
- [docs/business-model-canvas.md](docs/business-model-canvas.md): the business model canvas: who pays, the channels, what Bank of Cyprus gains
- [docs/mockups/](docs/mockups/README.md): mockups of the owner's screens: the approval above the limit, receipts, the cash forecast, the credit offer and the month end, and screenshots of the running chat page
- [docs/pitch/](docs/pitch/README.md): the pitch deck of 5 slides with speaker notes
- [docs/demo.md](docs/demo.md): the stage demo on two laptops: one runs Ena, the other shows it on the projector
- [docs/hackathon.md](docs/hackathon.md): the hackathon reference: APIs, links, rules, how the jury scores
- [docs/interviews/](docs/interviews/guide.md): the customer interview guide (English, Russian, Greek)
- [ena/models.py](ena/models.py): the data model of the build plan's §4: amounts in integer euro cents, dates as ISO `YYYY-MM-DD`, frozen models that refuse unknown fields
- [ena/](ena/): the connectors, the rules, the gate, the synthetic month and its replay, as the loop table above lists
- [fixtures/](fixtures/): saved sandbox responses from bocAPI, Jinius and JCC

Every team member can edit these documents: change them in a pull request or push to `main`, and date each decision.

## License

Everything in this repository is published under [CC BY-SA 4.0](LICENSE), as the hackathon rules require.
