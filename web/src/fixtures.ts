// Card events as the API sends them for the synthetic month on 16 November (ena/seed.py)
import type { CardEvent, Credit, Overview, Receipts } from "./api";

export const APPROVAL: CardEvent = {
  kind: "card",
  card_kind: "approval",
  card: {
    id: "approval-syn-inv-0002",
    action: "Pay Mesaoria Mills (synthetic) invoice syn-inv-0002",
    amount_cents: 124000,
    reason: "€1,240.00 is above the €500.00 auto-pay limit",
    status: "pending",
    created_at: "2026-11-16T09:00:00Z",
    decided_at: null,
  },
};

export const RECEIPT: CardEvent = {
  kind: "card",
  card_kind: "receipt",
  card: {
    id: "receipt-syn-inv-0002",
    at: "2026-11-16T09:05:00Z",
    actor: "owner",
    action: "Pay Mesaoria Mills (synthetic) invoice syn-inv-0002",
    amount_cents: 124000,
    ref: "syn-inv-0002",
    result: "paid",
    explanation: "Approved by the owner; paid €1,240.00, bank status CPLT (simulated: no money moved)",
    simulated: true,
    sandbox: false,
  },
};

export const FORECAST: CardEvent = {
  kind: "card",
  card_kind: "forecast",
  card: {
    weeks: [
      { week_start: "2026-11-16", opening_cents: 266919, in_cents: 72050, out_cents: 304000, closing_cents: 34969 },
      { week_start: "2026-11-23", opening_cents: 34969, in_cents: 72050, out_cents: 40000, closing_cents: 67019 },
    ],
    lean_week: "2026-11-16",
    reason: "The week of 16 Nov 2026 closes at €349.69, below €950.00",
  },
};

export const LOAN: CardEvent = {
  kind: "card",
  card_kind: "offer",
  card: {
    id: "loan-20261116-100000",
    kind: "loan",
    amount_cents: 100000,
    term_days: 730,
    basis: { gap_cents: 60031, rounded_gap_cents: 100000 },
    simulated: true,
  },
};

export const ADVANCE: CardEvent = {
  kind: "card",
  card_kind: "offer",
  card: {
    id: "advance-syn-event-0014-280000",
    kind: "event_advance",
    amount_cents: 280000,
    term_days: 16,
    basis: { gap_cents: 280000, spare_cents: 0 },
    simulated: true,
  },
};

export const ACCEPTED: CardEvent = {
  kind: "card",
  card_kind: "receipt",
  card: {
    id: "receipt-accept-advance-syn-event-0014-280000",
    at: "2026-11-16T09:05:00Z",
    actor: "owner",
    action: "Accept the event advance for Georgiou wedding (synthetic) (simulated)",
    amount_cents: 280000,
    ref: "accept-advance-syn-event-0014-280000",
    result: "accepted",
    explanation: "Accepted by the owner. Simulated: there is no lending API, no money moved",
    simulated: false,
    sandbox: false,
  },
};

export const DECLINED: CardEvent = {
  kind: "card",
  card_kind: "receipt",
  card: {
    id: "receipt-syn-inv-0002",
    at: "2026-11-16T09:05:00Z",
    actor: "owner",
    action: "Pay Mesaoria Mills (synthetic) invoice syn-inv-0002",
    amount_cents: 124000,
    ref: "syn-inv-0002",
    result: "declined",
    explanation: "Declined by the owner; nothing paid",
    simulated: false,
    sandbox: false,
  },
};

// The dashboard's answers for the same day (ena/dashboard.py), the flour invoice pending

export const OVERVIEW: Overview = {
  as_of: "2026-11-16",
  business: "Andreou Bakery (synthetic)",
  synthetic: true,
  limit_cents: 50000,
  balance_cents: 266919,
  pending: [APPROVAL.card_kind === "approval" ? APPROVAL.card : never()],
  paid_on_its_own: 1,
  paid_on_its_own_cents: 18000,
  stepped_in: 0,
  paid_out_cents: 18000,
  unpaid_invoices: 1,
  unpaid_cents: 124000,
  lean_week: "2026-11-16",
  lean_week_closing_cents: 34969,
};

export const RECEIPTS: Receipts = {
  receipts: [RECEIPT.card_kind === "receipt" ? RECEIPT.card : never()],
};

export const CREDIT: Credit = {
  verdict: "loan_needed",
  lean_week: "2026-11-16",
  lean_week_closing_cents: 34969,
  threshold_cents: 95000,
  gap_cents: 60031,
  instalment_cents: 40000,
  instalment_due: "2026-11-25",
  reserve: {
    id: "reserve-20261125",
    kind: "reserve",
    amount_cents: 40000,
    term_days: 10,
    basis: { instalment_cents: 40000 },
    simulated: false,
  },
  loan: LOAN.card_kind === "offer" ? LOAN.card : never(),
};

function never(): never {
  throw new Error("a fixture has the wrong card kind");
}

/** An SSE body as `POST /chat` streams it: one event per block, named by its kind. */
export function sse(...events: object[]): string {
  return events
    .map((event) => `event: ${(event as { kind: string }).kind}\r\ndata: ${JSON.stringify(event)}\r\n\r\n`)
    .join("");
}

/** A fetch that answers `/chat` with `body` in chunks of `size` characters. */
export function streaming(body: string, size = 7): Response {
  const encoder = new TextEncoder();
  let offset = 0;
  const stream = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (offset >= body.length) {
        controller.close();
        return;
      }
      controller.enqueue(encoder.encode(body.slice(offset, offset + size)));
      offset += size;
    },
  });
  return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
}

export const CONSENT: CardEvent = {
  kind: "card",
  card_kind: "consent",
  card: {
    id: "consent-syn-inv-0002",
    action: "Pay Mesaoria Mills (synthetic) invoice syn-inv-0002",
    amount_cents: 124000,
    ref: "syn-inv-0002",
    actor: "owner",
    consent_url: "https://sandbox-apis.bankofcyprus.com/df-boc-org-sb/sb/psd2/oauth2/authorize?paymentid=pay-1",
    created_at: "2026-11-16T09:05:00Z",
  },
};

export const SANDBOX_RECEIPT: CardEvent = {
  kind: "card",
  card_kind: "receipt",
  card: {
    id: "receipt-syn-inv-0002",
    at: "2026-11-16T09:06:00Z",
    actor: "owner",
    action: "Pay Mesaoria Mills (synthetic) invoice syn-inv-0002",
    amount_cents: 124000,
    ref: "syn-inv-0002",
    result: "paid",
    explanation: "Approved by the owner; confirmed in the bank, paid €1,240.00, bank status CPLT",
    simulated: false,
    sandbox: true,
  },
};

export const ACCOUNT: CardEvent = {
  kind: "card",
  card_kind: "account",
  card: {
    account_id: "351012345671",
    iban: "CY11002003510000000012345671",
    currency: "EUR",
    available_cents: 825050,
    current_cents: 830000,
    read_at: "2026-11-28T09:00:00Z",
    lines: [{ id: "t-1", date: "2026-11-25", amount_cents: 12025, direction: "in", description: "Deposit" }],
    stale: false,
  },
};
