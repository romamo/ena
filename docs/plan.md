# Team plan

What we build, what is decided, who does what and what is left. Everyone on the team edits this file; record a decision with its date.

## The idea

A barber, a bakery or a photo studio has no back office. The owner pays supplier invoices from a phone, does the books at night or pays an accountant, and finds out money is short when a payment bounces. Ena is an AI agent on top of the owner's Bank of Cyprus account that takes this work off them.

The demo is one loop that works end to end:

| Step | What happens | API |
|---|---|---|
| 1. Supplier invoice | An invoice arrives; the agent reads it and schedules payment | Jinius |
| 2. Books | Payments out and card or link payments in are booked automatically | bocAPI, JCC |
| 3. Cash forecast | The month ahead: what comes in, what goes out, where it gets tight | bocAPI |
| 4. Credit offer | When cash runs short, a real BoC business loan is offered, shown as simulated | bocAPI Products API |

**The gate** sets it apart: the agent pays on its own up to a limit the owner sets, asks the owner above it, and leaves a receipt for every action. The organisers' Open Day slides list exactly these guardrails (spending limits, human review, log everything), so the pitch uses their words.

## Owner journey (demo script)

One month of a bakery owner with Ena, replayed from the synthetic month. The amounts are placeholders until the interviews.

| Stage | Owner does | Ena does | Owner's time |
|---|---|---|---|
| Set up (day 1) | Connects the BoC account, sets a €500 limit | Reads 3 months of history, finds the suppliers and their rhythm | 5 min |
| Small invoice (Monday) | Nothing; sees "Paid dairy €180" | Reads the invoice in Jinius, pays within the limit, logs a receipt | 0 |
| Large invoice (Wednesday) | Taps Approve on "Pay the flour supplier €1,240?" | Asks above the limit, pays after approval, logs a receipt | 5 sec |
| Lean week ahead (week 3) | Approves a €400 reserve, or the loan application | Sees the gap in the forecast, sets money aside for the installment or pre-fills a Digital Business Loan | 10 sec |
| Month end (day 30) | Reads one screen | Books matched, cash for next month, "you stepped in 3 times" | 1 min |

- The large invoice is the pitch hook: the pitch opens on that approval card and closes on its receipt
- The last column is the one number: about 6 minutes a month with Ena after setup, against the hours the owners report in the interviews
- The rows show the split we pitch: the owner decides, Ena does the work

## Decided

- **Name: Ena** for the team and the product (2026-11-27). Greek ένα means "one": one agent for the whole back office. On stage, say it once: "Ena, one agent for your whole back office"
- **One scenario, end to end.** The organisers say one working loop beats ten half-finished features
- **Jinius is the backbone** of the loop (supplier invoices and remittance). It scores on integration and puts us in the running for the €2,000 Jinius prize
- **Credit offer: BoC Digital Business Loan (Working Capital)**: up to €20,000 over up to 2 years, online application, reply within 24 hours, no guarantees. The agent pre-fills the application from the account data. The sandbox has no lending API, so we say plainly that this step is simulated. (Fleksy was dropped on 2026-11-26: it is a consumer buy-now-pay-later product, not a business one)
- **Audience: micro-businesses and the self-employed.** The first vertical comes from the interviews (a photo studio and a bakery so far, a barber next)
- **Loan reserve.** When the forecast sees a lean month and the business has a loan, the agent sets money aside for the installment early, so the loan stays current. Banks give relief after disasters only on loans paid on time, as Bank of Cyprus did after the 2025 wildfires (6-month moratorium) and the 2026 foot-and-mouth outbreak (12 months)
- **Built from scratch at the event** and published under CC BY-SA 4.0, as the rules require. No code from earlier projects
- **Stack:** the bank tools and the gate live in our own MCP server; the agent on top is swappable. IBM Bob and watsonx Orchestrate are not used; if a mentor or the jury clearly values IBM, either can be plugged in as one more MCP client (about an hour), and the pitch says "the gate works with any agent"
- **Stack** (2026-11-27): as proposed in [build-plan.md](build-plan.md) §12: Python, FastAPI, the MCP Python SDK, SQLite, pytest, a Vite + React chat page, Claude through the Agent SDK
- **This repo is the working copy.** The final version is submitted to the organisers separately

## Who does what

| Area | What it covers | Owner |
|---|---|---|
| Software | The app, the gate, the API integrations, the repo | Roman |
| Pitch on stage | The 4 minute talk, live demo included, the jury's Q&A after it (2026-11-28): the problem, a live demo, the next step; answers to the jury | Youssef |
| Presentation | Deck of 4 to 5 slides; mockups of the owner's screens (approval above the limit, receipts, forecast, credit offer) | Together |
| Idea work | Positioning, business model canvas (who pays, how much, what BoC gains), the one number | Together |
| Customer interviews | Finding micro-businesses, the interviews, numbers and quotes | Together |
| Demo | The demo script, the synthetic month, a backup video, who clicks on stage | Keith (2026-11-28) |
| Submission and organisers | GitHub usernames, F1 form, sandbox sign-ups, mentors, the final repo (README, sample data, mockups, canvas, deck) | Youssef (2026-11-28) |

Team: Roman, Youssef and Keith.

## Weekend

| When | What |
|---|---|
| Fri 17:00 to 18:00 | Check-in at IDEA |
| Fri 18:00 to 19:00 | Kick off |
| Fri 19:00 | F1 Form of Needs due (matches us with mentors) |
| Fri 19:30 to 20:30 | Mandatory mentoring session |
| Sat morning | Customer interviews: the problem, the auto-pay limit, the price ([guide](interviews/guide.md)) |
| Sat 13:00 to 14:00 | "Let's Pitch" workshop |
| Sat afternoon | Pitch draft together, with the interview numbers and quotes |
| Sun morning | Debug the app and polish the pitch |
| Sun 14:00 to 15:00 | Final submission |
| Sun 15:30 | Stop coding |
| Sun 16:30 to 19:30 | Pitches, 4 minutes per team with the live demo (2026-11-28); winners 19:30 to 20:30 |

## Pitch hook

The first 15 seconds decide whether the jury listens. We pick one on Saturday afternoon, once the interviews give us real numbers and quotes.

- **Live notification (recommended).** Open with the phone on screen: "Pay the flour supplier €1,240? It's above your €500 limit. Approve?" Then: "This is the only thing a bakery owner did today. The agent did the rest." Close with the same scene: the owner taps Approve and the receipt appears. The amounts are placeholders until the interviews
- **An owner's own words.** One quote from the interviews about doing the books at night or a payment that bounced, with the owner's business named
- **The bank's number.** Small and medium businesses in Cyprus have a non-performing loan ratio of 3.6%, more than twice the banking sector's 1.6% (Central Bank of Cyprus, 2026; check the month before the pitch). Then: "Most of them didn't fail. They ran out of cash for a month"

## The one number

The organisers ask every team to measure one success metric. Ours: the owner's hours on paperwork a month, or the share of supplier invoices paid without the owner.

- **Before:** self-reported by about 5 owners in the interviews (question 2), with invoice counts from question 1
- **After:** a synthetic month for one business, built from the interview numbers (supplier invoices, card sales, rent, one tight week), replayed through the loop in about a minute. The gate's log counts every time the owner was asked; touches times seconds per approval gives the hours
- The replay is a seed script plus an accelerated clock, not a full simulator. Actions go to the real sandboxes (bocAPI payments between sample accounts, Jinius mock payments, JCC test cards)
- The pitch says plainly that "before" is self-reported and "after" is modelled

## How we stand apart

- Thematic areas: D (Fintech for SMEs) first, C (Open Banking), H1 (credit on alternative data)
- The organisers list an AI cash-flow coach (D1) and invoice-to-payment matching (D2) as sample ideas, so expect rival teams there. The agent that pays within a limit, with a receipt per action, is what sets us apart
- Innovation weighs only 20%, so the pitch does not rest on the gate alone
- Market proof: on 2026-11-24 Flexprice launched Ferry, an agent for B2B finance teams that does the work and asks for approval in a Slack thread. It shows "agent does, human approves" is where the market is heading. The contrast: Ferry serves companies with CRM and ERP, never pays anything out and has no bank data or credit; Ena serves micro-businesses with no back-office team, pays suppliers from the BoC account within a limit and draws the line by amount

## Questions to answer

- **Who pays?** The owner by subscription, the bank as a feature of its business account, or both? What price would an owner accept?
- **What does Bank of Cyprus gain?** Deposits, business loan sales, fewer missed payments. Which one leads the pitch?
- **Which vertical opens the story?** We pick it from the interviews
- **How do we stand apart** from the cash-flow-coach teams in the first 30 seconds?
- **What next step** do we promise at the end: a pilot with BoC business customers, a partner, a vertical?

## Open

- [x] Send every member's GitHub username to the organisers (Roman `romamo`, Youssef `YoussefBouz`, Keith `keithmaina123`): sent by Roman
- [ ] Register in the bocAPI sandbox, the Jinius integration environment and the JCC sandbox; run the Postman collections
- [x] F1 Form of Needs, due Friday 19:00: a mentor from BoC SME lending (does the Digital Business Loan fit micro-businesses; can it be applied for through an API in the sandbox or should we emulate it), plus a Jinius mentor: sent by Youssef
- [x] Ask the organisers: the jury's Q&A is separate from the 4 minutes, which include the live demo (2026-11-28)
- [ ] About 5 customer interviews on Saturday morning: a photo studio and a bakery are lined up; find a barber and one or two more
- [x] Agree Keith's area: Demo (2026-11-28)
