import QRCode from "qrcode";
import { createContext, useContext, useEffect, useState } from "react";
import {
  ApiError,
  awardQuote,
  cancelConsent,
  consentState,
  decide,
  takeOffer,
  type AccountCard,
  type ApprovalCard,
  type CardEvent,
  type ConsentCard,
  type ForecastCard,
  type OfferCard,
  type OrderCard,
  type PayLinkCard,
  type QuotesCard,
  type ReceiptCard,
} from "./api";
import { day, dayTime, euro } from "./format";

/** The fetch the owner's taps go through; the tests give their own. */
export const FetchContext = createContext<typeof fetch>((input, init) => fetch(input, init));

/** The dashboard's tab for each kind of card. */
export type BoardTab = "overview" | "receipts" | "forecast" | "credit" | "jinius";

const CARD_TAB: Record<CardEvent["card_kind"], BoardTab> = {
  approval: "overview",
  consent: "overview",
  receipt: "receipts",
  forecast: "forecast",
  offer: "credit",
  account: "overview",
  pay_link: "receipts",
  quotes: "overview",
  order: "receipts",
};

/** Opens the dashboard on a tab; given only in the chat, so a card there links to it. */
export const OpenBoard = createContext<((tab: BoardTab) => void) | null>(null);

/** Called once the owner has approved or declined, so a view can read again. */
export const OnDecided = createContext<() => void>(() => {});

function BoardLink({ kind }: { kind: CardEvent["card_kind"] }) {
  const open = useContext(OpenBoard);
  if (open === null) return null;
  return (
    <button type="button" className="link" onClick={() => open(CARD_TAB[kind])}>
      See in dashboard →
    </button>
  );
}

/** How often a consent card asks whether the bank's confirmation came back. */
export const POLL_MS = 1500;

export function Card({ event }: { event: CardEvent }) {
  switch (event.card_kind) {
    case "approval":
      return <Approval card={event.card} />;
    case "consent":
      return <Consent card={event.card} />;
    case "account":
      return <Account card={event.card} />;
    case "receipt":
      return <Receipt card={event.card} />;
    case "forecast":
      return <Forecast card={event.card} />;
    case "offer":
      return <Offer card={event.card} />;
    case "pay_link":
      return <PayLink card={event.card} />;
    case "quotes":
      return <Quotes card={event.card} />;
    case "order":
      return <Order card={event.card} />;
  }
}

function Label({ children }: { children: string }) {
  return <span className="label">{children}</span>;
}

type Tap =
  | { state: "open" }
  | { state: "sending"; choice: "approve" | "decline" }
  | { state: "decided"; receipt: CardEvent }
  | { state: "failed"; message: string };

function Approval({ card }: { card: ApprovalCard }) {
  const fetcher = useContext(FetchContext);
  const decided = useContext(OnDecided);
  const [tap, setTap] = useState<Tap>({ state: "open" });
  const pending = card.status === "pending";

  async function choose(choice: "approve" | "decline") {
    setTap({ state: "sending", choice });
    try {
      setTap({ state: "decided", receipt: await decide(card.id, choice, fetcher) });
      decided();
    } catch (error) {
      const message = error instanceof ApiError ? error.message : String(error);
      setTap({ state: "failed", message });
    }
  }

  return (
    <article className="card approval" aria-label="Approval">
      <header>
        <h3>{approvalTitle(card, tap)}</h3>
        <Label>synthetic</Label>
      </header>
      <p className="amount">{euro(card.amount_cents)}</p>
      <p>{card.action}</p>
      <p className="muted">{card.reason}</p>
      {pending && tap.state !== "decided" ? (
        <div className="actions">
          <button
            type="button"
            className="primary"
            disabled={tap.state === "sending"}
            onClick={() => void choose("approve")}
          >
            {tap.state === "sending" && tap.choice === "approve" ? "Paying…" : "Approve"}
          </button>
          <button
            type="button"
            disabled={tap.state === "sending"}
            onClick={() => void choose("decline")}
          >
            Decline
          </button>
        </div>
      ) : null}
      {tap.state === "failed" ? (
        <p className="error" role="alert">
          {tap.message}
        </p>
      ) : null}
      {tap.state === "decided" ? <Card event={tap.receipt} /> : null}
      <BoardLink kind="approval" />
    </article>
  );
}

function approvalTitle(card: ApprovalCard, tap: Tap): string {
  if (tap.state === "decided" && tap.receipt.card_kind === "receipt") {
    return tap.receipt.card.result === "declined" ? "You declined" : "You approved";
  }
  if (tap.state === "decided" && tap.receipt.card_kind === "consent") return "You approved";
  return card.status === "pending" ? "Needs your approval" : `Already ${card.status}`;
}

function Receipt({ card }: { card: ReceiptCard }) {
  return (
    <article className={`card receipt result-${card.result}`} aria-label="Receipt">
      <header>
        <h3>Receipt · {card.result}</h3>
        <Label>{receiptLabel(card)}</Label>
      </header>
      <p className="amount">{euro(card.amount_cents)}</p>
      <p>{card.action}</p>
      <p className="muted">{card.explanation}</p>
      <p className="muted small">
        By {card.actor === "ena" ? "Ena" : "you"}, {dayTime(card.at)} · {card.id}
      </p>
      <BoardLink kind="receipt" />
    </article>
  );
}

function receiptLabel(card: ReceiptCard): string {
  if (card.sandbox) return "BoC sandbox payment";
  return card.simulated ? "simulated payment" : "synthetic";
}

type Confirm =
  | { state: "open" }
  | { state: "waiting"; problem?: string }
  | { state: "settled"; receipt: CardEvent }
  | { state: "failed"; message: string };

function Consent({ card }: { card: ConsentCard }) {
  const fetcher = useContext(FetchContext);
  const [confirm, setConfirm] = useState<Confirm>({ state: "open" });

  useEffect(() => {
    if (confirm.state !== "waiting") return;
    const timer = setInterval(() => {
      consentState(card.id, fetcher).then(
        (answer) => {
          if (answer.card_kind === "receipt") setConfirm({ state: "settled", receipt: answer });
        },
        (error: unknown) => {
          // One failed check doesn't settle the payment: keep waiting, and keep Cancel
          const problem = error instanceof ApiError ? error.message : String(error);
          setConfirm({ state: "waiting", problem });
        },
      );
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [confirm.state, card.id, fetcher]);

  function open() {
    window.open(card.consent_url, "_blank");
    setConfirm({ state: "waiting" });
  }

  async function cancel() {
    try {
      setConfirm({ state: "settled", receipt: await cancelConsent(card.id, fetcher) });
    } catch (error) {
      const message = error instanceof ApiError ? error.message : String(error);
      setConfirm({ state: "failed", message });
    }
  }

  return (
    <article className="card consent" aria-label="Bank confirmation">
      <header>
        <h3>{confirm.state === "settled" ? "Bank answered" : "Confirm in Bank of Cyprus"}</h3>
        <Label>BoC sandbox</Label>
      </header>
      <p className="amount">{euro(card.amount_cents)}</p>
      <p>{card.action}</p>
      <p className="muted">
        {card.actor === "ena" ? "Within your limit" : "You approved it"}; the bank asks you to
        confirm it in its own screen. Nothing is paid until you do.
      </p>
      {confirm.state === "open" || confirm.state === "waiting" ? (
        <div className="actions">
          <button type="button" className="primary" onClick={open}>
            {confirm.state === "waiting" ? "Open Bank of Cyprus again" : "Confirm in Bank of Cyprus"}
          </button>
          <button type="button" onClick={() => void cancel()}>
            Cancel
          </button>
        </div>
      ) : null}
      {confirm.state === "waiting" ? (
        <p className="muted small">
          Waiting for the bank's confirmation…
          {confirm.problem === undefined ? "" : ` (last check failed: ${confirm.problem})`}
        </p>
      ) : null}
      {confirm.state === "failed" ? (
        <p className="error" role="alert">
          {confirm.message}
        </p>
      ) : null}
      {confirm.state === "settled" ? <Card event={confirm.receipt} /> : null}
    </article>
  );
}

function Account({ card }: { card: AccountCard }) {
  return (
    <article className="card account" aria-label="Bank account">
      <header>
        <h3>Bank of Cyprus sandbox account</h3>
        <Label>{card.stale ? "last copy, BoC sandbox" : "live, BoC sandbox"}</Label>
      </header>
      <p className="amount">
        {card.available_cents === null ? "—" : euro(card.available_cents)}
        <span className="muted small"> available</span>
      </p>
      <p className="muted small">
        {card.iban ?? card.account_id} · a sample customer's account, not the bakery's · read{" "}
        {dayTime(card.read_at)}
      </p>
      {card.stale ? (
        <p className="muted small">
          The sandbox allows only a few account reads a day, so this is the last copy it gave.
        </p>
      ) : null}
      {card.lines_stale ? (
        <p className="muted small">The sandbox refused the statement today: these lines are older.</p>
      ) : null}
      {card.lines.length > 0 ? (
        <ol className="lines">
          {card.lines.map((line) => (
            <li key={line.id}>
              <span>{day(line.date)}</span>
              <span>{line.description || "—"}</span>
              <span>
                {line.direction === "out" ? "−" : line.direction === "in" ? "+" : ""}
                {euro(line.amount_cents)}
              </span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted small">No statement lines.</p>
      )}
    </article>
  );
}

function Forecast({ card }: { card: ForecastCard }) {
  const highest = Math.max(1, ...card.weeks.map((week) => Math.abs(week.closing_cents)));
  return (
    <article className="card forecast" aria-label="Forecast">
      <header>
        <h3>Cash forecast, {card.weeks.length} weeks</h3>
        <Label>synthetic</Label>
      </header>
      <ol className="weeks">
        {card.weeks.map((week) => {
          const lean = week.week_start === card.lean_week;
          return (
            <li key={week.week_start} className={lean ? "lean" : undefined}>
              <span className="week">
                {day(week.week_start)}
                {lean ? " · lean" : ""}
              </span>
              <span className="bar">
                <span style={{ width: `${(Math.abs(week.closing_cents) / highest) * 100}%` }} />
              </span>
              <span className="closing">{euro(week.closing_cents)}</span>
            </li>
          );
        })}
      </ol>
      <p className="muted">{card.reason}</p>
      <BoardLink kind="forecast" />
    </article>
  );
}

/** The pay link as an SVG QR code, drawn in the page: the link never leaves it. */
function useQr(url: string): string | null {
  const [svg, setSvg] = useState<string | null>(null);
  useEffect(() => {
    let current = true;
    setSvg(null);
    void QRCode.toString(url, { type: "svg", margin: 1, errorCorrectionLevel: "M" }).then(
      (drawn) => {
        if (current) setSvg(drawn);
      },
    );
    return () => {
      current = false;
    };
  }, [url]);
  return svg;
}

function PayLink({ card }: { card: PayLinkCard }) {
  const svg = useQr(card.url);
  return (
    <article className="card pay-link" aria-label="Pay link">
      <header>
        <h3>Card payment link</h3>
        <Label>{card.sandbox ? "JCC sandbox" : "JCC"}</Label>
      </header>
      <p className="amount">{euro(card.amount_cents)}</p>
      <p>{card.description}</p>
      {svg === null ? (
        <p className="muted small">Drawing the QR code…</p>
      ) : (
        <img
          className="qr"
          alt={`QR code to pay ${euro(card.amount_cents)} by card`}
          src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`}
        />
      )}
      <a href={card.url} target="_blank" rel="noreferrer noopener">
        Open the payment page
      </a>
      <p className="muted">
        Nothing has moved yet: the sale is booked when JCC confirms the card payment.
        {card.sandbox ? " Only a test card pays it." : ""}
      </p>
      <p className="muted small">
        Open until {dayTime(card.expires_at)} · {card.id}
      </p>
      <BoardLink kind="pay_link" />
    </article>
  );
}

type Award =
  | { state: "open" }
  | { state: "sending"; quotationId: string }
  | { state: "ordered"; order: CardEvent }
  | { state: "failed"; message: string };

function Quotes({ card }: { card: QuotesCard }) {
  const fetcher = useContext(FetchContext);
  const decided = useContext(OnDecided);
  const [award, setAward] = useState<Award>({ state: "open" });
  const open = card.order_id === null && award.state !== "ordered";

  async function choose(quotationId: string) {
    setAward({ state: "sending", quotationId });
    try {
      setAward({ state: "ordered", order: await awardQuote(quotationId, fetcher) });
      decided();
    } catch (error) {
      const message = error instanceof ApiError ? error.message : String(error);
      setAward({ state: "failed", message });
    }
  }

  return (
    <article className="card quotes" aria-label="Quotations">
      <header>
        <h3>{open ? "Quotations to compare" : "Ordered"}</h3>
        <Label>synthetic</Label>
      </header>
      <p>{card.request.title}</p>
      <p className="muted small">
        Needed by {day(card.request.needed_by)} · asked {card.request.suppliers.length} suppliers
        outside Jinius (simulated)
      </p>
      {card.quotes.length === 0 ? (
        <p className="muted">No quotations yet.</p>
      ) : (
        <ol className="quotes-list">
          {card.quotes.map(({ quotation, valid, issues }) => {
            const recommended = quotation.id === card.recommended;
            const awarded = quotation.id === card.awarded;
            return (
              <li key={quotation.id} className={recommended ? "recommended" : undefined}>
                <div className="quote-head">
                  <span>{quotation.supplier_name}</span>
                  <span className="amount-inline">{euro(quotation.total_cents)}</span>
                </div>
                <p className="muted small">
                  Delivers {day(quotation.delivery_date)} · holds until {day(quotation.valid_until)}
                  {recommended ? " · Ena recommends" : ""}
                  {awarded ? " · ordered" : ""}
                </p>
                {issues.length > 0 ? <p className="muted small">{issues.join("; ")}</p> : null}
                {open && valid ? (
                  <button
                    type="button"
                    className={recommended ? "primary" : undefined}
                    disabled={award.state === "sending"}
                    onClick={() => void choose(quotation.id)}
                  >
                    {award.state === "sending" && award.quotationId === quotation.id
                      ? "Ordering…"
                      : `Order from ${quotation.supplier_name}`}
                  </button>
                ) : null}
              </li>
            );
          })}
        </ol>
      )}
      <p className="muted">{card.reason}</p>
      {award.state === "failed" ? (
        <p className="error" role="alert">
          {award.message}
        </p>
      ) : null}
      {award.state === "ordered" ? <Card event={award.order} /> : null}
    </article>
  );
}

function Order({ card }: { card: OrderCard }) {
  return (
    <article className="card order" aria-label="Purchase order">
      <header>
        <h3>Purchase order {card.id}</h3>
        <Label>{card.simulated ? "sent outside Jinius, simulated" : "synthetic"}</Label>
      </header>
      <p className="amount">{euro(card.total_cents)}</p>
      <p>
        To {card.supplier_name}, delivered by {day(card.deliver_by)}
      </p>
      <ol className="lines">
        {card.lines.map((line) => (
          <li key={line.description}>
            <span>
              {line.quantity} {line.unit}
            </span>
            <span>{line.description}</span>
            <span>{euro(line.total_cents)}</span>
          </li>
        ))}
      </ol>
      <p className="muted small">
        By {card.actor === "ena" ? "Ena" : "you"}, {dayTime(card.issued_at)}. Ena pays the
        supplier's Jinius invoice only if it names this order and matches it.
      </p>
      <BoardLink kind="order" />
    </article>
  );
}

const OFFER_TITLE: Record<OfferCard["kind"], string> = {
  reserve: "Set money aside",
  loan: "Digital Business Loan",
  event_advance: "Event advance",
};

type OfferTap =
  | { state: "open" }
  | { state: "sending"; choice: "accept" | "decline" }
  | { state: "decided"; receipt: CardEvent }
  | { state: "failed"; message: string };

function Offer({ card }: { card: OfferCard }) {
  const fetcher = useContext(FetchContext);
  const [tap, setTap] = useState<OfferTap>({ state: "open" });
  // Only an event advance is taken on its card; the reserve and the loan are offers to read
  const takeable = card.kind === "event_advance";

  async function choose(choice: "accept" | "decline") {
    setTap({ state: "sending", choice });
    try {
      setTap({ state: "decided", receipt: await takeOffer(card.id, choice, fetcher) });
    } catch (error) {
      const message = error instanceof ApiError ? error.message : String(error);
      setTap({ state: "failed", message });
    }
  }

  return (
    <article className="card offer" aria-label="Offer">
      <header>
        <h3>{OFFER_TITLE[card.kind]}</h3>
        <Label>{card.simulated ? "offer, simulated" : "synthetic"}</Label>
      </header>
      <p className="amount">{euro(card.amount_cents)}</p>
      <p className="muted">
        {card.kind === "reserve"
          ? `From your own balance, for ${card.term_days} days until the instalment`
          : `Over ${card.term_days} days`}
      </p>
      {card.simulated ? (
        <p className="muted small">
          Pre-filled, not an application and not a promise: the bank decides.
        </p>
      ) : null}
      {takeable && tap.state !== "decided" ? (
        <div className="actions">
          <button
            type="button"
            className="primary"
            disabled={tap.state === "sending"}
            onClick={() => void choose("accept")}
          >
            {tap.state === "sending" && tap.choice === "accept" ? "Accepting…" : "Accept"}
          </button>
          <button
            type="button"
            disabled={tap.state === "sending"}
            onClick={() => void choose("decline")}
          >
            Decline
          </button>
        </div>
      ) : null}
      {tap.state === "failed" ? (
        <p className="error" role="alert">
          {tap.message}
        </p>
      ) : null}
      {tap.state === "decided" ? <Card event={tap.receipt} /> : null}
      <BoardLink kind="offer" />
    </article>
  );
}
