# bocAPI sandbox: integration notes

Read 2026-11-27 from public documentation only, before any live call. No sign-up, login or terms were accepted. Sources:

- How to test our APIs: https://developer.bankofcyprus.com/docs/how-to-test-our-apis
- How to access and call our APIs: https://developer.bankofcyprus.com/docs/how-to-access-and-call-our-apis
- PSD2 APIs and plans: https://developer.bankofcyprus.com/psd2-apis, https://developer.bankofcyprus.com/psd2-apis/account-information-and-payment-initiation-subscription-plan
- B2B APIs: https://developer.bankofcyprus.com/b2b-apis
- API list: https://developer.bankofcyprus.com/APIs
- FAQs: https://developer.bankofcyprus.com/faqs
- Portal guide PDF (same content as the test page, plus diagrams): https://developer.bankofcyprus.com/system/files/2024-09/BoC%20APIC%20Portals%20Documentation.pdf
- Sandbox catalogue: https://sandbox.bankofcyprus.com/product and its API pages (`/product/psd2-sb-aisp-pisp-alias/api/1860`, `.../1866`, `.../1869`, `/product/b2b-sb-apis-alias/api/1863`, `.../1872`)
- Hackathon Technical Specifications (Google Doc linked from docs/hackathon.md)

Read later the same day: the Postman suite `Sandbox samples.zip` (https://developer.bankofcyprus.com/system/files/2024-09/Sandbox%20samples.zip, a collection and an environment dated 2024-09), summarised in section 8. Not read: the Google Drive Postman files from the Tech Spec (no link found). No public bocSDK repository was found on GitHub or the portal.

## 1. Access

1. Create a free developer account on https://sandbox.bankofcyprus.com (Sign-On). Credentials arrive by email
2. In "My Apps", register an application. The OAuth redirect URI is mandatory. The docs' samples redirect to `https://localhost/?code=...`; our app "Ena" is registered with `https://localhost/`, which works (VERIFIED 2026-11-28, section 4)
3. The client secret is shown once only; it can be reset from the app page, not recovered
4. In the app's subscription area, subscribe it to a plan. For our loop: **PSD2-AISP-PISP-Product** (accounts + payments + subscriptions) and also **B2B-APIs-Product** (BOC B2B Plan), which BoC's sandbox guide requires; without it the consent never returns a `code` (section 4). Plans carry rate limits; the app page shows usage. Actual numbers are not published (UNKNOWN)

`.env.example` names (values never in the repo):

```text
BOC_CLIENT_ID=
BOC_CLIENT_SECRET=
BOC_REDIRECT_URI=https://localhost/
BOC_API_BASE=https://sandbox-apis.bankofcyprus.com/df-boc-org-sb/sb/psd2
BOC_SIGN_URL=https://sandbox-apis.bankofcyprus.com/df-boc-org-sb/sb/jwssignverifyapi/sign
BOC_SUBSCRIPTION_ID=          # filled after consent, valid 180 days
```

Base URLs:

| Use | URL |
|---|---|
| Sandbox API (all calls below are relative to it) | `https://sandbox-apis.bankofcyprus.com/df-boc-org-sb/sb/psd2` |
| Sandbox signing helper | `https://sandbox-apis.bankofcyprus.com/df-boc-org-sb/sb/jwssignverifyapi/sign` |
| Sandbox portal (catalogue, apps, "Try" buttons) | `https://sandbox.bankofcyprus.com` |
| Production PSD2 (not for us: needs eIDAS QWAC) | `https://apis-secure.bankofcyprus.com/df-boc-org-prd/prod/psd2/v2` |
| Production B2B (via a banker's application form) | `https://apis.bankofcyprus.com/df-boc-org-prd/prod/psd2` |

## 2. Auth and consent, step by step

There are two tokens. The **client token** (`scope=TPPOAuth2Security`) identifies the app. The **user token** (`scope=UserOAuth2Security`) comes from a user's consent and is what account reads and payment execute use.

1. **Client token.** `POST /oauth2/token`, form-encoded: `grant_type=client_credentials`, `client_id`, `client_secret`, `scope=TPPOAuth2Security`. Response: `token_type`, `access_token`, `expires_in` (3600), `consented_on`, `scope`
2. **Create subscription (consent request).** `POST /v1/subscriptions` with the client token. Headers: `Authorization: Bearer`, `Content-Type: application/json`, `timeStamp`, `journeyId` (a fresh GUID). Body: `accounts` {`transactionHistory`, `balance`, `details`, `checkFundsAvailability`: booleans} and `payments` {`limit`, `currency`, `amount`}. Response has `subscriptionId`, `status: PENDING`, `selectedAccounts: []`, `duration` {`startDate`, `endDate`}
3. **User authorisation redirect.** Send the browser to `GET /oauth2/authorize?response_type=code&redirect_uri=...&scope=UserOAuth2Security&client_id=...&subscriptionid=<id>`. BoC hosts a simulated 1Bank login; the test user is `999999` / passcode `112233`. The user picks accounts and consents; the browser returns to `redirect_uri?code=...`. This screen is a real redirect to a BoC page with mock credentials, not something we can skip with an API call (UNKNOWN whether it can be automated headless)
4. **User token.** `POST /oauth2/token`, `grant_type=authorization_code`, `client_id`, `client_secret`, `code`, `scope=UserOAuth2Security`. Same response shape
5. **Activate.** `GET /v1/subscriptions/{subscriptionId}` (now carries the accounts the user picked), then `PATCH /v1/subscriptions/{subscriptionId}` with that body plus `selectedAccounts` [{`accountId`}] and `status`. Response `status: ACTV`. The web page says to send `INPROGRESS` in the PATCH; the PDF's PATCH sample sends neither status nor `selectedAccounts`. Trust the real response

Lifetimes:

| Item | Lifetime | Confidence |
|---|---|---|
| Access token (both kinds) | `expires_in: 3600` in every sample; one passage says "a few minutes" | 60 min likely, check `expires_in` |
| Refresh token | none in any sample | UNKNOWN; plan to redo the flow |
| Authorization code | not stated | UNKNOWN; exchange at once |
| Subscription | 180 days per the text; samples show 1 year and 3 months | 180 days, read `duration.endDate` |

Which token the PATCH and account calls take: the web page says the user token. Samples only use a placeholder (`{{oauth_code}}`), so if one is rejected try the other.

## 3. Endpoints the loop needs

Common headers on resource calls: `Authorization: Bearer <token>`, `Content-Type: application/json`, `journeyId` (GUID per call), `timestamp` (Postman's `{{$timestamp}}`, i.e. Unix seconds; format otherwise UNKNOWN). Account calls add `subscriptionId`.

### Accounts (PSD2_Accounts_API 1.1)

| Call | Path | Notes |
|---|---|---|
| List accounts | `GET /v1/accounts` | Array of {`bankId`, `accountId`, `accountAlias`, `accountType`, `accountName`, `IBAN`, `currency`, `infoTimeStamp`, `interestRate`, `maturityDate`, `lastPaymentDate`, `nextPaymentDate`, `remainingInstallments`, `balances`}; `balances` empty here |
| Account details | `GET /v1/accounts/{accountId}` | Same shape, returned as an array of one, with `balances` filled |
| Balance | `GET /v1/accounts/{accountId}/balance` | Same array shape; `balances` [{`amount`, `balanceType`: `AVAILABLE` or `CURRENT`}] |
| Statement | `GET /v1/accounts/{accountId}/statement?startDate=dd/mm/yyyy&endDate=dd/mm/yyyy&maxCount=N` | Returns `account` and `transaction` (singular key, an array) |
| Health | `GET /v1/accnts/healthCheck` | Spelling as listed |

Transaction fields: `id`, `dcInd` (`DEBIT`; credit value UNKNOWN, likely `CREDIT`), `transactionAmount` {`amount`, `currency`}, `description`, `postingDate`, `valueDate` (both `dd/mm/yyyy`), `transactionType` (`PAYMENT` seen). Samples omit `dcInd`, `description` and `transactionType` on some rows, so treat them as optional. `amount` is positive; direction comes only from `dcInd`.

Paging: only `maxCount` is documented; no page token, offset or default. To read a long period, split the date range and merge by `id` (UNKNOWN whether `maxCount` has a ceiling).

### Payments (PSD2-Payments-API V1.1)

1. **Sign** (sandbox helper). `POST <BOC_SIGN_URL>` with header `tppId: singpaymentdata` (spelled so in the docs). Body: `debtor` {`bankId`, `accountId`}, `creditor` {`bankId`, `accountId`}, `transactionAmount` {`amount`, `currency`}, `paymentDetails`. Returns `payload` and `signatures` [{`protected`, `signature`}], RS256. The test key pair (`sandbox-key.pem`, `sandbox-cert.pem`) is published, so we could sign locally the same way. In the sample, debtor `bankId` is empty and creditor `bankId` is a BIC
2. **Initiate.** `POST /v1/payments/initiate`, body {`payload`, `signatures`}. Extra headers: `customerDevice`, `customerIP`, `customerSessionId`, `loginTimeStamp`. Response: `authCodeNeeded: true`, `payment` {`paymentId`, `charges` [{`name`, `value`}]}. The sample charge is `Transfer_charges` "10" for a SWIFT transfer. Which token the docs mean here is UNKNOWN (placeholder only); the client token is the logical one, since the user has not consented yet
3. **User authorisation.** Same `/oauth2/authorize` redirect with `paymentid=<paymentId>` in place of `subscriptionid`; same test login; user reviews and may pick the debit account
4. **User token.** Exchange the new `code` as in section 2 step 4
5. **Execute.** `POST /v1/payments/{paymentId}/execute` with the user token, headers `journeyId`, `timestamp`. Success: `code: CPLT`, `description` (array), `refNumber`

Other operations listed (fields not public without opening each operation): `GET /v1/payments/{paymentId}`, `GET /v1/payments/{paymentId}/status`, `DELETE /v1/payments/{paymentId}`, `GET /v1/payments/accounts/{accountId}`, `GET /v1/payments/{paymentId}/funds-confirmation`, `POST /v1/payments/fundAvailability`, standing orders, mass and group payments. Status values other than `CPLT` are UNKNOWN: treat anything else as not paid.

Per-payment consent: every payment goes through the redirect and login. A subscription is not reused to skip it (VERIFIED by the documented flow).

### B2B payments (B2B-Payments-API V1.1)

`POST/GET /v1/payments/groupPayments`, `POST/GET /v1/massPayments`, `DELETE /v1/b2bPayments/{paymentId}`. These are batches that a company signatory approves later in 1Bank's "pending for authorization" page, so they do not finish with `CPLT` in our flow. Production B2B is paid and goes through a banker. **Not needed for the demo**; one line in the pitch ("in production, bulk supplier runs go through B2B mass payments") is enough. The B2B product also has a Customer API (`GET /v1/customers`).

### Products API (business loans)

Not found. The sandbox catalogue has five products (B2B-APIs, PSD2-AISP-PISP, PSD2-AISP, PSD2-PISP, psd2-customer-information) and none of them is Products, Branches, ATMs or loans. The Tech Spec lists Branches, ATMs and Products as bocAPI resources with real data, but gives no path. The developer portal's API list shows 10 of 41 items, so it may exist on a page we did not see. UNKNOWN; ask mentors first thing.

## 4. Sandbox specifics

**Resolved, 2026-11-28** (#24): following BoC's step-by-step sandbox guide (from the mentors), two changes made the consent return a `code`:

- The app also needs the **B2B-APIs-Product** plan (BOC B2B Plan) in the sandbox portal, next to PSD2-AISP-PISP-Product. Its redirect URI is `https://localhost/`
- The authorize URL must **not** carry `state`. With the B2B plan in place, a payment consent opened with `state=...` still came back `error=badstate`; the same consent without `state` came back with `code`. `ena-boc` sends no `state` since #35
- The authorize URL must carry `redirect_uri` **unencoded** (`redirect_uri=https://localhost/`). With no `state`, a subscription consent opened with `redirect_uri=https%3A%2F%2Flocalhost%2F` came back `error=badstate` (no `state` in the return either), three tries on 2026-11-28; the same consent with the URI unencoded came back with `code` and `ena-boc activate` reached `ACTV` (#24)

With both, a subscription reached `ACTV` (consent, `code`, user token, `PATCH`) and a payment reached `CPLT` (consent, `code`, user token, `execute`).

**Verified 2026-11-28, `ena-serve --bank boc`:** the portal accepts an `http` redirect URI with a private IP and no port (`http://192.168.50.69/boc/consent`), which answers §6 question 9. With that URI on the app, the code exchange must carry `redirect_uri` too: without it `POST /oauth2/token` answered 400 `apicoa400`; with it, the user token came back and `execute` reached `CPLT`. A payment whose code was spent stays `AUTHORIZED` in BoC's screen and offers no new code, so it needs a new payment.

**Superseded: live run, 2026-11-27 evening** (kept as a record; the resolution above replaces its open question) (app "Ena", redirect URI `https://localhost/callback`, `uv run ena-boc`):

| Step | Result |
|---|---|
| Client token | Works: `TPPOAuth2Security`, `expires_in` 3600 |
| Create subscription | Works: `PENDING`, `duration` 27/11/2026 to 25/02/2027 (90 days, not 180) |
| GET subscription | Client token works; answers an array of one |
| Consent screen | Now a newer login than the docs show: `sandbox.bankofcyprus.com/sandbox/login`, then `999999` / `112233`, account choice and an **OTP** field (`123456` accepted) |
| After consent | The choice is saved (subscription turns `INPROGRESS` with `selectedAccounts`), but the browser is redirected to `https://localhost/callback?state=...&error=badstate` with no `code`. Four tries, with and without our `state`, in Chrome and in a fresh browser |
| Read accounts on `INPROGRESS` | 400 "Status Not Active" |
| Activate (PATCH) with the client token | 403 Forbidden; needs the user token, which needs the `code` |
| Sign + initiate payment | Works: `authCodeNeeded: true`, a `paymentId` |
| Payment consent | Same `error=badstate` |

`badstate` comes from IBM API Connect's OAuth provider: the authorize URL gains an `rstate` when it sends the browser to the login page, and the redirect back fails that check. The open question then (a sandbox fault, or an app setting?) is answered by the resolution above: the missing B2B plan and the `state` parameter.

- Test login for both consent screens: `999999` / `112233`
- Sample accounts: `351012345671` (ANDREAS MICHAEL, EUR, IBAN `CY11002003510000000012345671`); also `351092345672`, `351012345673`, `351012345674`, `351012345675`, `351092345676` in the activate sample
- Sample payment: debtor `351012345671` → creditor BIC `CITIUS33`, account `48193222324233`, EUR 30, "SWIFT Transfer". Whether a BoC-to-BoC transfer between sample accounts works (and what `bankId` it needs) is UNKNOWN
- Mocked: the 1Bank login and consent pages, the signing step, account data (preloaded sample customers). Real-time balance effects of a payment: UNKNOWN
- Subscription `payments` {`limit`, `amount`, `currency`}: meaning not explained (per-payment cap vs total?). If it is a per-payment cap enforced by BoC, it could back up Ena's owner limit; UNKNOWN
- Inconsistencies seen in the docs:
  - `SubscriptionId` (GET) vs `subscriptionId` elsewhere
  - Header casing: `timeStamp` vs `timestamp`, `journeyId` vs `journeyid` vs `journeyIid`; send the documented camelCase and expect the gateway to be case-insensitive (UNKNOWN)
  - Status sent `INPROGRESS`, returned `ACTV`, prose says "ACTIVE"
  - `expirationDate` in GET vs `duration` in POST/PATCH responses; durations disagree with the 180-day rule
  - Path says `{accountNumber}` on the web page, `{accountId}` in the catalogue
  - Single-account endpoints return arrays
  - Sandbox uses `/v1/`, production PSD2 `/v2`
  - Client and user token samples show identical values; trailing commas and a broken placeholder (`{(client_secret}}`) in samples
  - The payment step heading says the second token is "for the PATCH subscription"; it is for execute

## 5. Assumptions in build-plan §10

| Assumption | Verdict | Source |
|---|---|---|
| Client token and user token differ; execute needs the user token | VERIFIED: scopes `TPPOAuth2Security` vs `UserOAuth2Security`; execute uses the token from the payment's auth code | how-to-test page, portal PDF |
| Consent screens are simulated with test credentials | VERIFIED: BoC-hosted mock 1Bank login, `999999` / `112233`, once for the subscription and once per payment | how-to-test page, portal PDF |
| Doc samples are inconsistent | VERIFIED: see section 4 list | how-to-test page, portal PDF |
| Dates are `dd/mm/yyyy` | VERIFIED for statement query and response dates and subscription dates; `timestamp` header format UNKNOWN (sample uses Unix seconds) | how-to-test page |
| Endpoint groups `/oauth2`, `/psd2subscription`, `/psd2account`, `/psd2payments`, `/b2bpayments` | REFUTED as URL paths: these appear only in the Tech Spec under `sandbox.bankofcyprus.com` (the portal). Real calls go to `sandbox-apis.bankofcyprus.com/df-boc-org-sb/sb/psd2` + `/oauth2/...`, `/v1/subscriptions`, `/v1/accounts`, `/v1/payments`; B2B under `/v1/massPayments` etc. The names still map to API products | Tech Spec vs how-to-test page and sandbox catalogue |
| Products API available in the same app | UNKNOWN, leaning REFUTED: not in the sandbox catalogue; Tech Spec names it without a path | sandbox `/product`, Tech Spec |

## 6. Open questions for BoC mentors

1. Where is the Products API (and Branches/ATMs) the Tech Spec mentions: host, path, plan to subscribe? Does it list the Digital Business Loan with its limits?
2. Can the payment consent redirect be done once for several payments, or completed without a browser in the sandbox?
3. Payment status values besides `CPLT`, and is `GET /v1/payments/{paymentId}/status` worth polling?
4. Does a transfer between two sandbox accounts (BoC to BoC) work, and what goes in `creditor.bankId`? Does a payment change the sample balance and statement?
5. What do `payments.limit` and `payments.amount` in the subscription mean, and are they enforced?
6. Is there a refresh token, and what are the code and token lifetimes really?
7. Statement paging: is there a cap on `maxCount` or a way to page?
8. Plan rate limits for the sandbox app
9. Does the redirect URI accept `https://localhost`, or `http://localhost:<port>`?

## 7. Risks for the demo and fallbacks

| Risk | Fallback |
|---|---|
| Consent redirect per payment breaks the chat flow | Accept it: Approve tap opens the BoC sandbox screen in a new tab; say on stage it is the bank's own consent. Record a backup video of a `CPLT` run |
| Sandbox down or slow on stage | Connector serves saved real responses (fixtures captured Friday night) with "sandbox offline, showing saved data" |
| Token expires mid-demo (60 min, no refresh) | Fetch fresh tokens right before going on stage; connector redoes the client token once, then fails loudly |
| Subscription consent lost or expired | Do the subscription consent Friday night; keep `BOC_SUBSCRIPTION_ID`; it lasts far longer than the event |
| Products API does not exist in the sandbox | Show the Digital Business Loan as a simulated offer with terms read from BoC's public product page, cited on the card; say "no lending API in the sandbox" |
| Only the SWIFT sample payee works | Use the documented creditor (`CITIUS33` / `48193222324233`) as the demo supplier's account; map invoice payee to it in seed data |
| Statement data is thin (a few sample rows) | Books and forecast run on seeded history; live statement rows are merged and shown as "from BoC" |
| Field names or casing differ from the docs | Parse real responses with tolerant keys at the connector boundary only, strict models inside; capture a real response before writing models |

## 8. What the Postman samples add

Read 2026-11-27 from `Sandbox samples.zip`: 24 requests and one environment. They confirm the paths of section 3 and add:

- **Environment.** Base URL `https://sandbox-apis.bankofcyprus.com/df-boc-org-sb/sb/psd2`; sample debtor account `351012345671`; `originUserId` `999999`; `tppId` `singpaymentdata`; `client_id` and `client_secret` empty, to fill from the registered app
- **Tokens.** `POST /oauth2/token` as a form, `grant_type=client_credentials`, `scope=TPPOAuth2Security` for the client token; `grant_type=authorization_code`, `scope=UserOAuth2Security`, `code=<from the redirect>` for the user token. `POST /oauth2/introspect` (`token`, `token_type_hint=access_token`) checks a token, which section 2 did not list
- **Subscription body.** `accounts` {`transactionHistory`, `balance`, `details`, `checkFundsAvailability`: true} and `payments` {`limit`, `currency`, `amount`}. The sample sets `limit` 99999999 and `amount` 999999999 with no explanation, so their meaning stays UNKNOWN; treat them as caps above any demo amount. `originUserId` goes in a header on create only
- **Subscription after consent.** The sample `PATCH /v1/subscriptions/{id}` body has `status` `INPROGRESS` and `selectedAccounts` listing six sample accounts (`351012345671`, `351012345673` to `351012345675`, `351092345672`, `351092345676`), which are the sample customer's accounts to pick from
- **Statement.** `GET /v1/accounts/{account}/statement?startDate=dd/mm/yyyy&endDate=dd/mm/yyyy&maxCount=0`; `maxCount=0` appears to mean no cap (UNKNOWN until tried)
- **Payment.** Sign with `POST .../jwssignverifyapi/sign` (header `tppId`), then `POST /v1/payments/initiate` with {`payload`, `signatures`} and the headers `loginTimeStamp`, `customerDevice`, `customerIp`, `customerSessionId`, `lang`; `POST /v1/payments/{id}/execute` takes an empty JSON body; `GET /v1/payments/{id}/status`, `GET /v1/payments/{id}` and `DELETE /v1/payments/{id}` follow. The sample's creditor is a SWIFT transfer (`CITIUS33`); a BoC to BoC payment between two sample accounts is the one to try first
- **Casing varies within the collection** (`timeStamp` and `timestamp`; `customerIp` here, `customerIP` in the docs). HTTP headers are case-insensitive, so this matters only if the gateway is not; send the docs' spelling and keep the collection's as the fallback

