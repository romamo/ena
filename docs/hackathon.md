# BoC Fintech Hackathon 7.0: reference

In person at IDEA Innovation Centre, Nicosia, 27 to 29 November 2026. Facts from bochackathon.net, the organisers' Medium post, the Technical Specifications and the Evaluation & Mentoring Guide v1.0, read 24 to 26 November 2026; recheck before relying on them. Our own plan is in [plan.md](plan.md).

## Prizes

- 1st €7,000, 2nd €4,000, 3rd €2,000
- Best use of the Jinius APIs €2,000
- Best Smart City idea €1,000 (Municipality of Nicosia)
- IBM gives participants access to IBM watsonx and IBM Bob (500 Bobcoins)

## APIs and sandboxes

- **bocAPI and bocSDK** (open banking sandbox): Branches, ATMs and Products return real BoC data; sample customers; Accounts, Payments and B2B payments over PSD2 (Subscriptions for consent, OAuth2); Postman collections; bocSDK for JS, Swift, Android, Java, C#, PHP. No lending or credit API
- **Jinius**: integration environment with a user and legal entity, client id and secret, Postman, mock payment settings, Remittance Management. Six guides at business.jinius.com.cy/support, read in order
- **JCC payment gateway** (self-service sandbox, test cards): Pay by Link, permanent links and Universal QR, payment widget, direct API, order status, callbacks, refunds, installments. It covers the income side of our loop: a customer pays the business by link or QR, the callback books it, and the forecast gets the inflow; keep it to one payment in the demo
- **Nicosia smart city** (Nokia IOC)
- Slack arrives after registration; the GitHub organisation opens on day one

## Links

- BoC APIs, how to test: https://developer.bankofcyprus.com/docs/how-to-test-our-apis
- BoC sandbox: https://sandbox.bankofcyprus.com/
- BoC developer portal and bocSDK: https://developer.bankofcyprus.com/
- Jinius guides: https://business.jinius.com.cy/support/
- JCC gateway docs and sandbox: https://gateway.jcc.com.cy/developer
- Nicosia smart city API: https://nokia.smartnicosia.eu/api-docs/
- Technical Specifications: https://docs.google.com/document/d/e/2PACX-1vQhHQmn0tyWX4TlBGT3Y6Ub9LcvnTEh4j8zpZJC5r0RCOhCMgNaw_1HEZ7p6u7JP53n-g3iES1__VOZ/pub
- Evaluation & Mentoring Guide: https://docs.google.com/document/d/e/2PACX-1vT06rUjVfdZh7pfpb-97c62X6uE4ien9IVg4_RNGlXgq1yy8pUdEDn84aJx3yqu7rGDX1MR0drnT8Gf/pub
- Organisers' ideas post: https://medium.com/the-crowdpolicy-collection-en/boc-fintech-hackathon-7-0-ideas-for-apps-that-change-how-we-bank-pay-and-live-abfa4267527b
- Agenda: https://bochackathon.net/en/agenda/

## What to submit

- Everything goes into a repo in the hackathon's GitHub organisation, where the code is also judged: code, sample data and README, mockups, business model canvas, pitch deck of 4 to 5 slides
- Every team member's GitHub username goes to the organisers to get the invite
- Final submission: Sunday 14:00 to 15:00; stop coding 15:30

## Rules

- Teams of 1 to 6 natural persons; legal entities are excluded
- Everything produced during the event is published under CC BY-SA 4.0
- Rights to the work stay with the team

## How the jury scores

Each criterion is scored 1 to 5, then weighted:

| Criterion | What they look at | Weight | Our estimate |
|---|---|---|---|
| Technology / Integration | How complete the app is, integration with third-party systems through APIs, features with business value | 40% | 4 |
| Impact / Business model | Whether BoC, its partners or the public sector can apply it now, how many people it reaches, fit with the thematic areas; scores best when it matches the depth of the build | 40% | 4 |
| Innovation | Combination of technologies, use of open data and APIs, incentive to use it | 20% | 3 |

The organisers' advice: one scenario that works end to end beats ten half-finished ones; the AI explains its results, protects data and leaves the final decision to a person; the pitch is the problem, a live demo and the next step.

**Evaluation committee:** Bank of Cyprus (Executive Director Technology & Operations; Manager IT Services), Jinius (team lead), JCC, IBM (two), Kyndryl, Microsoft, Cytabusiness, University of Cyprus, Crowdpolicy.

**Mentors** include Jinius and JCC people, BoC developers and architects, the BoC Head of Internet Banking & Operations, Nicosia Municipality IT, IBM consultants and Kyndryl. No one from BoC lending is listed, so we ask for one in F1. Mentors are matched through F1 and some also sit on the jury.

## Open Day slides (24 November)

- Building blocks of an AI fintech app: language models, your data via RAG, tools and function calling, agents (plan, call tools, check, retry), guardrails (validation, spending limits, human review)
- From a traditional app to an LLM app: keep the app, add an LLM layer, ground it in your data (sandbox APIs, no training), give it safe tools (balance, transfer, quote), guard and measure (confirm actions with the user, log everything, track one success metric)
- Sample MVPs: "AI money coach for small businesses" (Fintech for SMEs, bocAPI + Jinius) is first on the list, so expect several teams on it; also explainable credit for first-time borrowers and a friendly payment guardian
- What the best examples share: a real everyday pain (waiting, paperwork, access to credit), the data you have, people in the loop for sensitive cases, every result explained, one number measured
