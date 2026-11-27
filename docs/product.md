# Ena

## Mission

Ena is one AI agent for a micro-business's back office on top of its Bank of Cyprus account: it pays supplier invoices within the owner's limit, keeps the books, forecasts cash and offers working capital before money runs short, so the owner only decides. Built at BoC Fintech Hackathon 7.0, 2026-11-27 to 29.

## Users

- U-1: Owners of micro-businesses and the self-employed in Cyprus who bank with Bank of Cyprus (a bakery, a photo studio, a barber)

## Goals

- G-1: One loop works end to end: a supplier invoice from Jinius is paid within the limit or after approval, booked, the cash forecast flags a lean week, and a Digital Business Loan or a reserve is offered. Check: the demo replays the synthetic month live on the BoC sandbox with no hand fix-up. By: 2026-11-29
- G-2: The entry is submitted on time with code, sample data, README and deck. Check: the F2 final submission is sent before 15:00. By: 2026-11-29

## Non-goals

- N-1: Lending itself: the loan offer is simulated, since the sandbox has no lending API
- N-2: Tax filings and accounts signed for the authorities
- N-3: Code from earlier projects: everything is built at the event and published under CC BY-SA 4.0

## Quality bar

- Q-1: The gate holds: no payment above the owner's limit without the owner's approval, and every action leaves a receipt
- Q-2: Code decides amounts and rules; the agent explains and asks, never computes money itself
- Q-3: No sandbox keys or other secrets in the repo
- Q-4: Every change keeps the demo loop working
