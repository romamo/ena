import { createContext, useContext, useState } from "react";
import {
  ApiError,
  decide,
  type ApprovalCard,
  type CardEvent,
  type ForecastCard,
  type OfferCard,
  type ReceiptCard,
} from "./api";
import { day, dayTime, euro } from "./format";

/** The fetch the owner's taps go through; the tests give their own. */
export const FetchContext = createContext<typeof fetch>((input, init) => fetch(input, init));

/** The dashboard's tab for each kind of card. */
export type BoardTab = "overview" | "receipts" | "forecast" | "credit";

const CARD_TAB: Record<CardEvent["card_kind"], BoardTab> = {
  approval: "overview",
  receipt: "receipts",
  forecast: "forecast",
  offer: "credit",
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

export function Card({ event }: { event: CardEvent }) {
  switch (event.card_kind) {
    case "approval":
      return <Approval card={event.card} />;
    case "receipt":
      return <Receipt card={event.card} />;
    case "forecast":
      return <Forecast card={event.card} />;
    case "offer":
      return <Offer card={event.card} />;
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
  return card.status === "pending" ? "Needs your approval" : `Already ${card.status}`;
}

function Receipt({ card }: { card: ReceiptCard }) {
  return (
    <article className={`card receipt result-${card.result}`} aria-label="Receipt">
      <header>
        <h3>Receipt · {card.result}</h3>
        <Label>{card.simulated ? "simulated payment" : "synthetic"}</Label>
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

const OFFER_TITLE: Record<OfferCard["kind"], string> = {
  reserve: "Set money aside",
  loan: "Digital Business Loan",
  event_advance: "Event advance",
};

function Offer({ card }: { card: OfferCard }) {
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
      <BoardLink kind="offer" />
    </article>
  );
}
