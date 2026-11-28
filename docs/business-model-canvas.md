# Business model canvas

Ena's business model on one page, in the nine blocks of the Business Model Canvas. It is a draft for the team to correct: the figures marked *hypothesis* come from the interview guide's assumptions and change once the interviews give real numbers. The product itself is in [product.md](product.md) and the team plan in [plan.md](plan.md).

| Block | Ena |
|---|---|
| **Customer segments** | Micro-businesses and the self-employed in Cyprus who bank with Bank of Cyprus and have no back-office staff: a bakery, a barber, a photo studio. First vertical: picked from the interviews. Second customer: Bank of Cyprus itself, which offers Ena as a feature of its business account |
| **Value propositions** | For the owner: one agent for the whole back office. It pays supplier invoices within the owner's limit, asks above it, keeps the books, forecasts 8 weeks of cash and sets money aside or offers working capital before cash runs short. About 6 minutes a month of the owner's time instead of hours. Every action leaves a receipt, so the owner stays in control. For the bank: more loans paid on time, Digital Business Loan sales at the moment of need, and deposits that stay at BoC |
| **Channels** | Inside BoC: the business account and 1Bank, the bank's SME relationship managers, the Digital Business Loan page. Through partners: Jinius, which already reaches the businesses that send and receive e-invoices, and JCC's merchant base. Directly: word of mouth among owners in one vertical, then the next |
| **Customer relationships** | Self-service and automated: the owner connects the account and sets a limit in about 5 minutes. The agent talks to the owner in chat, asks only above the limit and explains each step. A monthly one-screen summary ("you stepped in 3 times") keeps the owner seeing the value |
| **Revenue streams** | *Hypothesis*: a monthly subscription paid by the owner, priced below the €150 to 300 a month of back-office spend Ena replaces (bookkeeping, payment admin). Or a per-account fee paid by the bank, which bundles Ena into a premium business account. Plus a referral share on Digital Business Loans that start in Ena, if BoC agrees one. The interviews (question 8) decide the price and who pays |
| **Key resources** | The gate: the code that holds the owner's limit and writes the receipts. The rules for books, the cash forecast, the reserve and the loan offer. The integrations with bocAPI, Jinius and JCC. The language model that explains and asks; code, not the model, decides every amount |
| **Key activities** | Running the loop for each business every day: read invoices, pay within the limit, book statement lines and card sales, forecast cash, offer the reserve or the loan. Keeping the integrations and the security of the bank connection. Onboarding owners in one vertical at a time |
| **Key partners** | Bank of Cyprus: bocAPI (accounts, payments, statement), the Digital Business Loan, distribution. Jinius: supplier invoices and remittance. JCC: card and link payments, the income side. The language model provider. Accountants who sign the yearly accounts, which Ena does not do |
| **Cost structure** | Hosting and the language model's per-call cost, which grows with the number of businesses. Engineering for the gate, the rules and the integrations. Security, compliance and the bank's vendor review. Onboarding and support. Little marketing while the bank and Jinius are the channels |

## What Bank of Cyprus gains

The question the jury asks first, answered in one place:

- **Fewer missed loan payments.** Ena sets money aside for the instalment when it sees a lean month, so the loan stays current. Small and medium businesses in Cyprus have a non-performing loan ratio of 3.6%, against the sector's 1.6% (Central Bank of Cyprus, 2026)
- **Loans sold when they are needed.** The Digital Business Loan (up to €20,000 over up to 2 years) is offered, pre-filled, in the week the forecast shows the gap
- **Deposits and payments stay at BoC.** Supplier payments, card income and the reserve all run through the BoC account

## Open questions

- Who pays: the owner, the bank or both. The interviews decide
- The price an owner accepts, from question 8 of the [interview guide](interviews/guide.md)
- Whether BoC pays a referral share on loans that start in Ena
- The first vertical
