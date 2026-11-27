# Ena

One agent for a micro-business's back office. Built at [BoC Fintech Hackathon 7.0](https://bochackathon.net), Nicosia, 27 to 29 November 2026.

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

## Repository

- `docs/interviews/`: the customer interview guide (English, Russian, Greek)

## License

Everything in this repository is published under [CC BY-SA 4.0](LICENSE), as the hackathon rules require.
