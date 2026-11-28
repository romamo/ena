# Ena

Ena is an AI back-office agent that pays a micro-business's supplier invoices from its Bank of Cyprus account within the owner's limit, keeps its books, forecasts its cash and offers a reserve or a loan before money runs short.

Built at [BoC Fintech Hackathon 7.0](https://bochackathon.net), Nicosia, 27 to 29 November 2026.

> This is the team's working copy. The final version is submitted to the organisers separately.

Ena is for micro-businesses and the self-employed who bank with Bank of Cyprus. A barber or a bakery owner pays supplier invoices from a phone, does the books at night and finds out cash is short when a payment bounces. Ena takes that work off them in one loop: it receives supplier invoices through Jinius, pays them from the BoC account, keeps the books from bocAPI and JCC card data, forecasts cash for the coming weeks and, when a lean month is ahead, sets money aside for loan installments or offers a BoC Digital Business Loan for working capital. The owner sets a limit: Ena pays on its own below it, asks for approval above it and leaves a receipt for every action.

## The loop

| Step | What Ena does | API | Code | Runs on today |
|---|---|---|---|---|
| Supplier invoice | Reads the invoices sent to the business and marks one paid | Jinius (`GET /v3/invoices/payable`) | [ena/connectors/jinius.py](ena/connectors/jinius.py) | The Jinius integration environment with credentials; without them, or when Jinius can't be reached, the saved responses in [fixtures/jinius/](fixtures/jinius/) |
| Payment | Pays at or below the owner's limit, asks for approval above it, and writes a receipt for every payment | bocAPI payments (sign, initiate, consent, execute to `CPLT`) | The gate [ena/gate.py](ena/gate.py) with [ena/store.py](ena/store.py); the bank calls in [ena/connectors/boc.py](ena/connectors/boc.py) | The gate runs in the tests against a stand-in bank; the bocAPI payment runs on the sandbox by hand with `ena-boc` (below). The two are not wired together yet |
| Books | Matches statement lines to paid invoices and to card sales | bocAPI statement; JCC card payments (callback, `getOrderStatusExtended.do`) | [ena/rules/books.py](ena/rules/books.py); the statement call in [ena/connectors/boc.py](ena/connectors/boc.py); the JCC callback `POST /jcc/callback` in [ena/api.py](ena/api.py) with [ena/connectors/jcc.py](ena/connectors/jcc.py), which books a confirmed card payment once | The synthetic month. The JCC callback runs in the tests on the saved sandbox responses in [fixtures/jcc/](fixtures/jcc/); see [docs/jcc-notes.md](docs/jcc-notes.md) for the live sandbox |
| Cash forecast | Projects 8 weeks of cash and flags the first lean week | bocAPI balance and statement | [ena/rules/forecast.py](ena/rules/forecast.py) | The synthetic month: week 3 (16 November) is lean |
| Credit offer | Sets money aside for the loan instalment, or pre-fills a Digital Business Loan | None: the loan offer is simulated, since the sandbox has no lending API | [ena/rules/reserve.py](ena/rules/reserve.py), [ena/rules/loan.py](ena/rules/loan.py) | The forecast's numbers |

The synthetic month ([ena/seed.py](ena/seed.py)) is a bakery's November 2026 built from a fixed seed: a €180 dairy invoice under the €500 limit, a €1,240 flour invoice above it, card sales six days a week, the rent on 20 November and the loan instalment on 25 November. [ena/replay.py](ena/replay.py) plays it as a time-ordered stream of events.

Coming next: the chat agent that takes the replayed events through the rules and the gate and explains each step to the owner, and the chat demo on top of it. The run steps for that demo land with it.

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

Then start the API on http://127.0.0.1:8000 (the chat, the owner's approve and decline, the JCC callback), with the synthetic month as of 16 November and the store in `tmp/ena.db`; `uv run ena-serve --help` lists the flags. Its payments are simulated: a stand-in bank completes each one, so a receipt says paid though no money moved. Without `ANTHROPIC_API_KEY` (or `CLAUDE_CODE_OAUTH_TOKEN`) in the shell `/chat` answers with an error event, and without the JCC settings in `.env` the callback refuses every call:

```bash
uv run ena-serve
```

### On the sandboxes, with credentials

Not needed for the demo. To call the bocAPI sandbox, copy `.env.example` to `.env` and fill in the bocAPI app's client id and secret, then:

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
| Demo, submission and organisers | Open |

Members: Roman, Youssef and Keith. Keith's area is still to agree.

## Repository

- [docs/product.md](docs/product.md): the mission, goals, non-goals and quality bar
- [docs/plan.md](docs/plan.md): the team plan: decisions, roles, weekend, pitch hook, the one number, open tasks
- [docs/build-plan.md](docs/build-plan.md): proposed architecture, data model, tools, rules, tasks and timeline
- [docs/business-model-canvas.md](docs/business-model-canvas.md): the business model canvas: who pays, the channels, what Bank of Cyprus gains
- [docs/mockups/](docs/mockups/README.md): mockups of the owner's screens: the approval above the limit, receipts, the cash forecast, the credit offer and the month end
- [docs/hackathon.md](docs/hackathon.md): the hackathon reference: APIs, links, rules, how the jury scores
- [docs/interviews/](docs/interviews/guide.md): the customer interview guide (English, Russian, Greek)
- [ena/models.py](ena/models.py): the data model of the build plan's §4: amounts in integer euro cents, dates as ISO `YYYY-MM-DD`, frozen models that refuse unknown fields
- [ena/](ena/): the connectors, the rules, the gate, the synthetic month and its replay, as the loop table above lists
- [fixtures/](fixtures/): saved sandbox responses from bocAPI, Jinius and JCC

Every team member can edit these documents: change them in a pull request or push to `main`, and date each decision.

## License

Everything in this repository is published under [CC BY-SA 4.0](LICENSE), as the hackathon rules require.
