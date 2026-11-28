# Ena

One agent for a micro-business's back office. Built at [BoC Fintech Hackathon 7.0](https://bochackathon.net), Nicosia, 27 to 29 November 2026.

> This is the team's working copy. The final version is submitted to the organisers separately.

Ena is an AI back-office agent for micro-businesses and the self-employed who bank with Bank of Cyprus. A barber or a bakery owner pays supplier invoices from a phone, does the books at night and finds out cash is short when a payment bounces. Ena takes that work off them in one loop: it receives supplier invoices through Jinius, pays them from the BoC account, keeps the books from bocAPI and JCC card data, forecasts cash for the coming weeks and, when a lean month is ahead, sets money aside for loan installments or offers a BoC Digital Business Loan for working capital. The owner sets a limit: Ena pays on its own below it, asks for approval above it and leaves a receipt for every action.

## The loop

| Step | What Ena does | Source |
|---|---|---|
| Supplier invoice | Receives the invoice and pays it | Jinius, bocAPI |
| Books | Matches payments and card sales | bocAPI, JCC |
| Cash forecast | Projects the coming weeks, flags a lean month | bocAPI |
| Credit offer | Sets money aside for installments, or offers a Digital Business Loan (simulated: the sandbox has no lending API) | bocAPI Products API |

The gate: Ena pays within the owner's limit, asks above it and leaves a receipt for every action.

## Team

| Area | Owner |
|---|---|
| Software | Roman |
| Pitch on stage | Youssef |
| Presentation, idea work, customer interviews | Together |
| Demo, submission and organisers | Open |

Members: Roman, Youssef and Keith. Keith's area is still to agree.

## Repository

- [docs/plan.md](docs/plan.md): the team plan: decisions, roles, weekend, pitch hook, the one number, open tasks
- [docs/build-plan.md](docs/build-plan.md): proposed architecture, data model, tools, rules, tasks and timeline
- [docs/hackathon.md](docs/hackathon.md): the hackathon reference: APIs, links, rules, how the jury scores
- [docs/interviews/](docs/interviews/guide.md): the customer interview guide (English, Russian, Greek)
- [ena/models.py](ena/models.py): the data model of the build plan's §4: amounts in integer euro cents, dates as ISO `YYYY-MM-DD`, frozen models that refuse unknown fields

Every team member can edit these documents: change them in a pull request or push to `main`, and date each decision.

## Run

Needs [uv](https://docs.astral.sh/uv/). Copy `.env.example` to `.env` and fill in the bocAPI app's client id and secret, then:

```bash
uv run ena-boc token
```

`uv run ena-boc --help` lists the steps from account consent to a payment that reaches `CPLT`. Tests: `uv run pytest`.

## License

Everything in this repository is published under [CC BY-SA 4.0](LICENSE), as the hackathon rules require.
