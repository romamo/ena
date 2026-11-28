# Jinius integration notes

Read from public documentation on 2026-11-27, before any live call. **UNKNOWN** marks what the public docs do not say; **ASSUMED** marks a guess we act on until a mentor or a real response confirms it. Section 0 is what we saw in the integration portal itself; where it disagrees with later sections, section 0 wins.

## 0. Integration environment as seen (2026-11-27, evening)

Looked around the portal while logged in; no API call made yet.

- **Portal:** `https://b2b.integration.platform.cy/` (not `b2b.jinius.com.cy`). Our team's user is admin of one legal entity, **DUMMY**, registered in October 2024 (an earlier account, reused). Profile fields are mostly empty; industry Professional Services / IT
- **Auth:** Keycloak, realm `platform-integration`. Token endpoint is the standard Keycloak one: `https://b2b.integration.platform.cy/auth/realms/platform-integration/protocol/openid-connect/token`; the realm allows `client_credentials` (VERIFIED from its OpenID configuration). The portal itself uses the public client `b2b-portal-client`
- **API client:** Settings → Developer already holds a Client ID and Client Secret for DUMMY (with Refresh and Delete buttons). Copy them into `.env` from that page; never into the repo or chat
- **Webhooks exist:** the same page takes up to 3 **Callback URLs**, each with a secret key. This answers the "webhooks: UNKNOWN" row in section 5; payloads and events are still UNKNOWN (guide 6). Polling stays the plan until a callback is seen
- **Portal API shape:** the portal calls gRPC-style paths `POST /api/cy.jinius.b2b_orchestration.v1.<Service>/<Method>` (for example `IntegrationServiceOrchestration/GetClient`, `OutboundServiceOrchestration/GetCallBackUrlsForLegalEntity`). It is **not** the integration API: see the token test below
- **Payments (Settings → Payments):** two Get Paid IBANs, a BoC one (Unverified, default) and an AstroBank one (Disabled). **Pay Others with 1bank: "Connection expired"**, asking for a B2B Company UserID to re-authorise. No "mock payment settings" control is visible; that is guide 4, which we do not have yet
- **Activity:** invoices and remittances are all zero. Subscription shows no plan and no billing
- **Ecosystem:** 281 businesses in the test directory (for example "AB Supplies Ltd", "Alpha Supermarkets", "Bank of Cyprus - Test"); invoices can only go to a business on Jinius, so the supplier for our demo is either a second entity of ours or one of these
- **Still missing:** the six guides (no link in the portal; the "How can I…" help links are empty)

### Token test (2026-11-28)

- `client_credentials` with DUMMY's Client ID and Secret: **200**. `expires_in` 300 (5 minutes), `refresh_expires_in` 0 (no refresh token), `scope` "profile email"
- Claims: `iss` is the realm above, `azp` and `client_id` are the Client ID, and a custom `legalEntity` claim holds the legal entity's id (here equal to the Client ID). So a token acts for exactly one legal entity: the supplier needs its own client
- The same token on two read calls of the portal API (`LegalEntityServiceOrchestration/GetLegalEntityOfUser`, `IntegrationServiceOrchestration/GetClient`): **403** with an empty body. The integration API is a different surface, so its host and paths stay UNKNOWN until guide 6
- For the connector: fetch a fresh token when the old one has under a minute left; 5 minutes is shorter than one demo run

### Supplier entity (2026-11-28)

- Registered **Ena Flour Mill Ltd** ("Register new business" on the dashboard) under the same login as DUMMY; the business switcher in the header lists both, so one user can act as buyer and supplier
- Wizard: Business Details (made-up registration number, TIN and VAT; Food, Beverages or Tobacco → Grain mill products; "Registration number already exists" if another team took the number), Subscription, Documents, Validate Account
- **Plans:** invoice API ("Automate invoice management with API") only on **Enterprise** (€75/month) and Enterprise Max (€150/month); remittance API from Premium (€50) up. A 1-month free trial; picked Enterprise. DUMMY shows no plan at all yet has an API client, so the integration environment may not enforce plans (UNKNOWN)
- **Documents:** only the IBAN certificate is required; a labelled placeholder PDF was accepted. Board Resolution and SEPA mandate are optional ("or send a hard copy by post")
- **Validate Account:** the supplier's IBAN is checked for a valid mod-97 checksum (the BoC docs' sample `CY11002003510000000012345671` fails it; a made-up `CY80002001950000357072811260` passes), plus a checkbox stating a €1 verification transfer to JINIUS LTD was made
- **Result:** "under review … within the next two business days". Until approved the entity has only Dashboard, Contact Jinius and Report Content: no invoices, no Developer page, no API client. Approval asked of the Jinius mentors (#26)

## 1. Sources

| Source | URL | What it gave |
|---|---|---|
| Jinius support page | https://business.jinius.com.cy/support/ | Onboarding, payment settings, the two integrable services (Invoicing, Remittance Management). No API guides on it |
| Jinius Help Center (Zendesk, 152 articles) | https://help.jinius.com.cy/hc/en-us | Invoice and remittance statuses, mark-as-paid flows, API credentials, the remittance file formats |
| Invoicing API credentials | https://help.jinius.com.cy/hc/en-us/articles/53299661177617 | Client ID and Client Secret, made in the portal; the API docs come from the Jinius team on request |
| Remittance JSON fields | https://help.jinius.com.cy/hc/en-us/articles/35189384938257 | Full field list and a sample file |
| Remittance Excel and XML fields | https://help.jinius.com.cy/hc/en-us/articles/35184131882257, https://help.jinius.com.cy/hc/en-us/articles/35189624048145 | Flat field names; XML is ISO 20022 pain.001.001.03 |
| Invoice statuses | https://help.jinius.com.cy/hc/en-us/articles/53300036937233 | Invoice lifecycle |
| Remittance statuses | https://help.jinius.com.cy/hc/en-us/articles/53310735702033 | Payment lifecycle |
| Mark paid (buyer), set paid (supplier), accept pending payment | https://help.jinius.com.cy/hc/en-us/articles/53296977420305, https://help.jinius.com.cy/hc/en-us/articles/35191485264913, https://help.jinius.com.cy/hc/en-us/articles/35183411482641 | How an invoice becomes Paid |
| Pay others, send a remittance | https://help.jinius.com.cy/hc/en-us/articles/4410632548241, https://help.jinius.com.cy/hc/en-us/articles/5280665137681 | Payments go through 1bank B2B APIs |
| Hackathon Technical Specifications | https://docs.google.com/document/d/e/2PACX-1vQhHQmn0tyWX4TlBGT3Y6Ub9LcvnTEh4j8zpZJC5r0RCOhCMgNaw_1HEZ7p6u7JP53n-g3iES1__VOZ/pub | The titles and order of the six guides, with no links |

Searched and not found: a public API reference, OpenAPI or Swagger file, Postman collection or developer portal (Jinius site, its WordPress media, the Help Center, the BoC developer portal, bochackathon.net, web search). `api.jinius.com.cy` resolves and answers 403/404 without credentials; whether it is the API host is UNKNOWN.

## 2. What Jinius is

Jinius is a Bank of Cyprus B2B platform: e-invoicing between registered companies, Remittance Management (batch supplier payments with remittance advice), tenders and a marketplace. Roles:
- **Supplier (seller)**: issues invoices and credit notes to a buyer registered on Jinius (Receivables tab); gets paid to a verified IBAN; sees remittance advice under Remittance Management → Receive
- **Buyer**: receives invoices (Payables tab), approves or rejects them, pays them through Jinius or marks them paid; sends remittances
- **Bank (BoC, 1bank)**: Jinius starts payments in 1bank through the BoC **B2B APIs**; an authorised user approves them in 1bank, and Jinius updates its records from the bank's status. Only BoC accounts can pay or be paid through Jinius today

## 3. Access

- **Integration environment**: we get a user and a legal entity on it through guide 1. Its URL is UNKNOWN (in guide 1); production portal is `https://b2b.jinius.com.cy/`
- **Credentials**: in the portal, Settings → Developer → "Create Client" makes a **Client ID** and **Client Secret**. Credentials belong to the legal entity that made them (ASSUMED), so a supplier entity and a buyer entity need a pair each
- **Auth flow**: UNKNOWN; OAuth2 client credentials is likely (ASSUMED, guide 2 or 3)
- **Plan gate**: production API integration is on selected subscription plans only; whether the integration environment has the same gate is UNKNOWN
- **Docs**: in production the API documentation is sent by the Jinius team on request; for the hackathon it is guide 6

`.env.example` names (values never committed):

```
JINIUS_TOKEN_URL=https://b2b.integration.platform.cy/auth/realms/platform-integration/protocol/openid-connect/token
JINIUS_CLIENT_ID=               # buyer: DUMMY (in .env since 2026-11-28)
JINIUS_CLIENT_SECRET=
JINIUS_SUPPLIER_CLIENT_ID=      # Ena Flour Mill Ltd, once Jinius approves it (#26)
JINIUS_SUPPLIER_CLIENT_SECRET=
JINIUS_BASE_URL=                # integration API base, UNKNOWN until guide 6
JINIUS_DEBTOR_ACCOUNT=          # buyer's BoC account number for remittance files
```

## 4. The six guides, in order

None of the six is public; the Technical Specifications list only their titles. They reach us through the hackathon (Slack, the GitHub organisation or the mentors): UNKNOWN which.

1. **Register a User and a Legal Entity on Integration Environment**: signing up on the test portal and creating the company. In production this needs documents, a board resolution and a €1 verification transfer; the test flow is probably lighter (UNKNOWN)
2. **Generate Client Id and Secret for Jinius APIs**: the Settings → Developer → Create Client step above
3. **Using Jinius APIs with Postman**: a collection or environment and how to get a token; our first live call happens here
4. **Setting up your Mock Payment Settings**: a test stand-in for the 1bank B2B API connection (the 8-digit UserID and yearly authorisation in production), so payments can complete without a real bank. The exact behaviour is UNKNOWN
5. **Jinius - Remittance Management Feature Info**: the batch payment flow and its file formats (matches the public Help Center articles in section 5)
6. **Jinius API - Technical Guide**: the endpoint reference. Everything marked UNKNOWN in section 5 should be answered here

## 5. Operations the loop needs

| Need | Method, path | Key fields | Status |
|---|---|---|---|
| Get a token | `POST https://b2b.integration.platform.cy/auth/realms/platform-integration/protocol/openid-connect/token`, form `grant_type=client_credentials` | Client ID, Client Secret | VERIFIED 2026-11-28 (section 0): 5-minute token, no refresh, one legal entity per client |
| Supplier submits an invoice or credit note (to seed the demo) | UNKNOWN | invoice number, issue date, due date, buyer, business unit, currency, line items (description, quantity, price, tax, discount), file, supporting documents (up to 15) | Operation exists (Help Center); path and field names UNKNOWN |
| Buyer lists incoming invoices | UNKNOWN | portal filters suggest: invoice number, currency, issue date range, due date range, company, recipient business unit, status | UNKNOWN whether the API exposes it; the public docs describe the integration as "submit invoices and credit notes", and say both sides can review and settle from their ERP |
| Read one invoice | UNKNOWN | amounts, VAT per line, due date, outstanding amount, history | UNKNOWN |
| Supplier IBAN on the invoice | UNKNOWN | the supplier's default verified IBAN is set in Settings → Payments → Get Paid | UNKNOWN whether the invoice payload carries it; if not, it comes from the payment Jinius makes, or we keep it in our seed data |
| Buyer approves an invoice | UNKNOWN | | Needed: mark-as-paid requires status Approved |
| Pay through Jinius (Remittance Management) | UNKNOWN (file upload through API or portal) | JSON file, see below | Format VERIFIED from public docs; API endpoint UNKNOWN. With mock payment settings the 1bank approval step is presumably simulated (UNKNOWN) |
| Mark paid after paying outside Jinius (our bocAPI payment) | UNKNOWN | amount (outstanding or partial), payment method (Cash, Cheque, Other), payment date, comment, receipt file | Exists in the portal; becomes **Paid only after the supplier accepts** the payment |
| Supplier accepts a pending payment | UNKNOWN | approve or reject | Exists in the portal; with two test entities we can accept it ourselves |
| Payment status | UNKNOWN | remittance status (below) | Statuses VERIFIED; read path UNKNOWN |
| Remittance advice for the supplier | UNKNOWN | credit IBAN, payment reference, amount, invoices paid with amounts | Portal Receive tab, emailed link, Excel/CSV export; API UNKNOWN |
| Webhooks | Settings → Developer → Callback URLs (up to 3, each with a secret key) | | Exist (section 0); events and payload UNKNOWN until guide 6. Plan on **polling** until a callback is seen |

**Invoice statuses**: Draft, Submitted, Pending Approval (buyer confirmed receipt), Approved, Rejected (with reason), Paid, Voided. An invoice sent through an integration that gets rejected is voided and resent under a new number.

**Remittance statuses**: Pending Approval (sent to the bank, awaiting approval), Paid, Failed (bank rules, reason in the portal), Rejected (declined in 1bank), Document validation failed (a referenced invoice exists but amounts disagree; the rest of the file still goes), Expired (90 days pending), Submission declined (zero amount).

**Remittance JSON file** (field names from the public sample):

```
groupHeader: remittanceAdviceId (unique per file), creationDateTime, executionDate (today or later),
             numberOfPayments, totalAmount
payments[]:
  paymentDetails:
    debtorDetails: accountNumber        # BoC account, digits only
    creditorDetails: iban | accountId, creditorName, creditorAddress?, bankId (BIC)?, bankName?, bankCountryCode?
    amount, currency (default EUR), paymentReference (unique across all files, max 35),
    description? (bank statement text, max 80), chargesPaidBy? (creditor|debtor|shared),
    chargesAccount? { bankId, accountId }
  paymentOptions?: paymentType (general | invoices)
  supplier?: taxIdentificationNumber, vatNumber, companyRegistrationNumber, companyName, email
  invoices[]?: buyerReferenceNumber, supplierInvoiceNumber, amount (negative for a credit note),
               currency, discount, issueDate, documentType (Invoice | CreditNote | General),
               originalCurrencyAmount?, originalCurrency?
```

Jinius checks the counts and sums, and cross-checks `supplierInvoiceNumber` and amounts against invoices already on the platform; a match is what links our payment to the Jinius invoice. `supplier.email` gets the remittance notice. Note: the field table calls the payment amount "Remittance Amount" while the sample uses `amount`; trust the sample and the real response.

**A design point for us:** Jinius's own payment path is Jinius → 1bank B2B APIs, not the bocAPI PSD2 payments our plan uses. Two ways to close the loop:
- **A. Pay through Jinius**: the gate's execute step builds a one-payment remittance file and submits it; mock payment settings complete it; we poll its status. Deepest use of Jinius (the €2,000 prize), but the bocAPI CPLT moment moves off the main path
- **B. Pay through bocAPI, then mark paid in Jinius**: as planned; mark paid with method Other and the bocAPI reference, then accept it as the supplier entity so the invoice reaches Paid. Keeps bocAPI central, uses Jinius only for read and status

Recommendation: build B first (it matches the plan and needs fewer unknowns), and add A for invoices whose supplier the owner chose to "pay via Jinius" if guide 6 shows a remittance endpoint and mock payments work. Ask the mentors which they would judge as better use.

## 6. Test data

- Create **two legal entities** in the integration environment: our micro-business (buyer) and its flour supplier (supplier). If one user can admin two entities, one login is enough (UNKNOWN)
- The supplier entity sets a "Get Paid" IBAN (a BoC test IBAN; verification in production needs an IBAN certificate, test behaviour UNKNOWN)
- The supplier sends invoices to the buyer from the portal (manual entry, or AI Document Intelligence on a PDF) or through the API; three are enough: €180 (auto-pay), €1,240 (approval), one with a credit note
- The buyer entity sets up mock payment settings (guide 4)
- Save every real response to `fixtures/jinius/` on first success
- How mock payments behave (instant Paid, configurable failure, delay): UNKNOWN

## 7. Minimal integration for two days

Calls, in order of value:
1. **Token** (client credentials)
2. **List buyer's incoming invoices** filtered by status, polled every 30 s, mapped to `SupplierInvoice` (`jiniusId`, `supplierName`, `amountCents`, `issueDate`, `dueDate`, status)
3. **Approve** the invoice, if the API needs it before payment
4. **Mark paid** with the bocAPI reference (option B), then **accept** it from the supplier entity; or **submit a one-payment remittance** and poll its status (option A)

Seeding invoices can be done in the portal by hand if the submit endpoint costs time.

**Fallback** (if guide 6 shows no buyer-side read or no status update we can use): `connectors/jinius.py` serves the same shape from `fixtures/jinius/` (statuses and field names as in section 5, remittance file exactly as the JSON format above), and the UI says "Jinius sandbox data, saved". We still generate a valid remittance JSON for each payment and show it, since that format is public and checkable, and say plainly on stage which calls were live.

## 8. Open questions for Jinius mentors

1. Where are the six guides and the Postman collection for hackathon teams?
2. Integration environment URL, API base URL and token endpoint; is it OAuth2 client credentials, and how long does a token last?
3. Can a buyer's credentials list and read incoming invoices (Payables) through the API, or is the API supplier-side only?
4. Does the invoice payload carry VAT per line, the due date, the outstanding amount and the supplier's IBAN?
5. Are approve, mark-as-paid and accept-payment available through the API, and what fields do they take?
6. Can a remittance file be submitted through the API (JSON body or upload), and how do we read its status and the remittance advice?
7. What do mock payment settings do: instant Paid, a simulated 1bank approval, configurable failures?
8. Are there webhooks or event callbacks, or should we poll? Any rate limits?
9. Can one user hold two legal entities (buyer and supplier) on the integration environment?
10. For the "best use of Jinius APIs" prize, would paying through Remittance Management count for more than marking paid after a bocAPI payment?
