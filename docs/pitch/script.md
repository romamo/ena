# Ena: the full pitch script

The organisers' rule: **4 minutes in total, questions included**, "not PowerPoint-centric": idea, business case, then a **live demo**. This script runs **about 3:00**, leaving a minute for questions. Slides 9 to 11 are backup slides, shown only if a question calls for them.

- **Speaker:** Youssef. **Demo driver:** whoever sits at the agent laptop (setup in `refs/ena/docs/demo.md`).
- Words in quotes are said out loud. Lines in *italics* are actions. "Scores" says which judging criterion the slide earns.
- Each `## N ·` section is slide N's speaker notes in `Ena-pitch.pptx`. Edit here, then rebuild: `python pptx/build_deck.py`.

## How the deck answers the judging criteria

| Criterion | Weight | Where we earn it |
|---|---|---|
| Technology / Integration | 40% | Slide 4 (three real APIs: Jinius, bocAPI, JCC), slide 5 (the live app), slide 6 (what we built, in numbers), backup slides 9 (Jinius) and 10 (architecture) |
| Impact / Business model | 40% | Slide 1 (real owners), slide 3 (the four jobs, in their words), slide 7 (who it's for, who pays, what BoC gains, how BoC ships it) |
| Innovation | 20% | Slide 4 (Ena acts inside a limit the owner sets, not just advice; code decides every amount, the AI explains), the combination of e-invoicing, bank and card data in one loop |

Jinius has its own prize (€2,000, best use of the Jinius APIs), and its team lead is on the jury: say "Jinius" clearly on slides 4 and 6.

## 1 · Interviews video · 0:00 to 0:27

Scores: Impact (real owners, real pain).

*The video starts on its own. Say nothing: let the owners talk.*

## 2 · Cover · 0:27 to 0:35

Scores: Impact.

"Two hours every day. Three people on the bookkeeping. Three or four hours. That's time those owners spend on paperwork, not on their business."

"So we built Ena. Ena is Greek for 'one': one AI agent that is the back office."

## 3 · Ena is the back office · 0:35 to 0:47

Scores: Impact (the problem and who has it).

"A small business has four back-office jobs: the invoices, the books, the cash forecast, and the loan. Today the owner does all four, from a phone, at night. Ena does all four, on the owner's own Bank of Cyprus account."

## 4 · How it works · 0:47 to 1:17

Scores: Technology / Integration (three real APIs), Innovation (the gate).

"Here's how. A supplier sends an invoice on Jinius. Ena reads it from the Jinius API, pays it from the owner's account through bocAPI, and reports it back to Jinius as paid."

"JCC card sales keep the books matched, and Ena forecasts eight weeks of cash. Before a lean month it sets money aside for the loan instalment, or prepares a loan offer. That part is simulated: the sandbox has no lending API."

"The owner's limit is the rule. Below it, Ena acts on its own. Above it, Ena asks. Every action leaves a receipt."

## 5 · Live demo · 1:17 to 2:20

Scores: Technology (it works, end to end), Innovation (the gate on screen).

*Switch to the projector laptop's browser, on the chat page.*

*Tap "Pay the flour invoice".*
"A 1,240 euro flour invoice. That's above her 500 euro limit, so Ena doesn't pay it. It asks."

*Tap Approve.*
"One tap, and here's the receipt. In this demo the payment is simulated: no money moves."

*Tap "Will I be short of cash this month?"*
"Ena looks eight weeks ahead and finds the lean week: the week of 16 November drops below her safe line."

*Tap "Can I get an advance for the Georgiou wedding?"*
"The bakery has a big catering job coming. Ena offers a short advance for its costs, repaid from the event's final payment. A simulated offer, and labelled so."

"That was her whole month of back office. Ena needed her once."

*If anything fails: switch to the backup demo video and keep talking. Don't debug on stage.*

## 6 · Built during the hackathon · 2:20 to 2:37

Scores: Technology (completeness, integrations).

"All of this was built here, since Friday evening: 76 commits, 47 pull requests merged, 433 tests passing. And three sandboxes connected: bocAPI, JCC and Jinius, where we registered our own supplier company to send real invoices through the API."

*Say the numbers the slide shows: the build reads them from GitHub, so rebuild on Sunday morning.*

## 7 · How Bank of Cyprus ships it · 2:37 to 2:56

Scores: Impact / Business model ("can BoC apply it now", who it's for, who pays).

"Bank of Cyprus can ship it in four steps: a three-month pilot in one vertical; a payment mandate in production bocAPI, capped at the owner's limit; a lending API, so the loan offer becomes a real application; then Ena inside 1bank."

"The bank gets instalments paid on time, loans offered the week they're needed, and payments that stay at BoC."

## 8 · Thank you · 2:56 to 3:00

*The closer video plays on its own (4 seconds).*

"The owner was the back office. Now Ena is. Thank you."

*Leave this slide up for the questions.*

## 9 · Backup: how Ena uses Jinius

Show it if asked about Jinius, invoices or integrations.

"Jinius is where the invoice starts. We run two companies on the Jinius integration environment: the bakery as buyer, and a supplier we registered ourselves, Ena Flour Mill, which Jinius approved. That supplier sent the 180 and 1,240 euro invoices you saw, and a credit note."

"Ena reads them live from the Jinius API. Approving the invoice and marking it paid, with the bank's payment reference, are built and tested against the Jinius specification. Next: Jinius callbacks instead of polling, and paying through Jinius's own Pay Others with 1bank."

*Be exact: reading invoices is live; approve and markAsPaid are built and tested, not yet called live.*

## 10 · Backup: how it's built

Show it for technical questions.

"Three inputs, one server, one door for money. Jinius and JCC come in, the owner talks to Ena in the chat and the dashboard. Every payment goes through the gate, which checks the limit and writes the receipt. The rules compute the books, the forecast and the offers. The AI agent only calls tools: it never sets an amount, and its process never holds a bank or Jinius secret."

## 11 · Backup: the brand

Show it if asked about the name.

"Ena is Greek for one. The logo is one stroke that closes into a loop: invoices, books, cash forecast, loans. The amber dot is the owner: the only moment Ena stops and asks."

# Questions we expect (about 1 minute)

Keep each answer to two sentences.

**"How do you use Jinius?"** *(show slide 9)*
"Invoices start on Jinius: a supplier company we registered sends them, and Ena reads them live from the Jinius API. Paying is through bocAPI, and Ena reports the payment back to Jinius; that part is built and tested, the next step is calling it live."

**"Does it really move money?"**
"The bocAPI payment runs end to end on the sandbox, from consent to completed. In the demo we use a stand-in bank so it runs the same every time; joining the two is our next step."

**"What stops the AI paying the wrong amount?"** *(show slide 10)*
"The AI never sets an amount: code does, through one gate with the owner's limit, and every action writes a receipt. Above the limit, nothing moves until the owner approves."

**"Who pays for it?"**
"Either a monthly subscription below the cost of the bookkeeping it replaces, or Bank of Cyprus bundles it into a premium business account. The pilot tells us which."

**"Why not existing accounting software?"**
"Accounting software records what the owner did. Ena does it: it pays, books and plans on the BoC account, inside a limit the owner sets."

**"Is the data real?"**
"The bakery is synthetic, and the app says so on screen. The interviews are real owners, and the Jinius invoices are real API calls on the integration environment."

# Before Sunday

- Rebuild the deck on Sunday morning, so slide 6's commit and PR numbers are current; rerun the tests and update the 433 in `pptx/build_deck.py` if it changed.
- Record a backup video of the live demo (the interviews video is not a demo backup).
- Rehearse twice with a timer. Slides 4 and 7 are where people run over.
- Check that the people in the interview videos agreed to be shown on stage and in a public repo.
