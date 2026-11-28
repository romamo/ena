import { useContext, useEffect, useState, type ReactNode } from "react";
import {
  ApiError,
  getDashboard,
  type Credit,
  type DashboardRoutes,
  type Overview,
  type Receipts,
} from "./api";
import { Card, FetchContext } from "./cards";
import { day, euro } from "./format";

// The owner's dashboard: what the chat's cards said, kept on one screen. It only reads
// (ena/dashboard.py); a pending payment is decided on its approval card, as in the chat.

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "receipts", label: "Receipts" },
  { id: "forecast", label: "Forecast" },
  { id: "credit", label: "Loans" },
] as const;

type Tab = (typeof TABS)[number]["id"];

type Load<T> = { state: "loading" } | { state: "ready"; data: T } | { state: "failed"; message: string };

/** Reads `route` each time the dashboard opens on it, so it shows what the chat just did. */
function useRoute<R extends keyof DashboardRoutes>(route: R, open: boolean): Load<DashboardRoutes[R]> {
  const fetcher = useContext(FetchContext);
  const [load, setLoad] = useState<Load<DashboardRoutes[R]>>({ state: "loading" });
  useEffect(() => {
    if (!open) return;
    let current = true;
    setLoad({ state: "loading" });
    getDashboard(route, fetcher).then(
      (data) => current && setLoad({ state: "ready", data }),
      (error: unknown) =>
        current &&
        setLoad({
          state: "failed",
          message: error instanceof ApiError ? error.message : "The dashboard can't be reached, try again",
        }),
    );
    return () => {
      current = false;
    };
  }, [route, open, fetcher]);
  return load;
}

export function Dashboard({ open }: { open: boolean }) {
  const [tab, setTab] = useState<Tab>("overview");
  return (
    <section className="dashboard" aria-label="Dashboard" hidden={!open}>
      <nav className="tabs" role="tablist" aria-label="Dashboard sections">
        {TABS.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            className={tab === id ? "tab on" : "tab"}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </nav>
      <div className="board" role="tabpanel" aria-label={TABS.find((t) => t.id === tab)?.label}>
        {tab === "overview" ? <OverviewTab open={open} /> : null}
        {tab === "receipts" ? <ReceiptsTab open={open} /> : null}
        {tab === "forecast" ? <ForecastTab open={open} /> : null}
        {tab === "credit" ? <CreditTab open={open} /> : null}
      </div>
    </section>
  );
}

function Loaded<T>({ load, children }: { load: Load<T>; children: (data: T) => ReactNode }) {
  if (load.state === "loading") return <p className="muted">Loading…</p>;
  if (load.state === "failed")
    return (
      <p className="error" role="alert">
        {load.message}
      </p>
    );
  return <>{children(load.data)}</>;
}

function Tile({ value, label, warn = false }: { value: string; label: string; warn?: boolean }) {
  return (
    <div className={warn ? "tile warn" : "tile"}>
      <span className="value">{value}</span>
      <span className="small">{label}</span>
    </div>
  );
}

function OverviewTab({ open }: { open: boolean }) {
  return (
    <Loaded load={useRoute("overview", open)}>
      {(o: Overview) => (
        <>
          <p className="muted small">
            {o.business} · as of {day(o.as_of)}
          </p>
          {o.pending.length > 0 ? (
            <section className="pending" aria-label="Needs your OK">
              <h2>Needs your OK · {o.pending.length}</h2>
              {o.pending.map((card) => (
                <Card key={card.id} event={{ kind: "card", card_kind: "approval", card }} />
              ))}
            </section>
          ) : null}
          <div className="tiles">
            <Tile value={euro(o.balance_cents)} label="balance today" />
            {o.lean_week !== null && o.lean_week_closing_cents !== null ? (
              <Tile value={euro(o.lean_week_closing_cents)} label={`lean week of ${day(o.lean_week)}`} warn />
            ) : (
              <Tile value="None" label="lean week in the next 8" />
            )}
            <Tile
              value={String(o.paid_on_its_own)}
              label={`paid on its own · ${euro(o.paid_on_its_own_cents)}`}
            />
            <Tile value={String(o.stepped_in)} label={o.stepped_in === 1 ? "time you stepped in" : "times you stepped in"} />
          </div>
          <dl className="facts">
            <div>
              <dt>Paid out by Ena</dt>
              <dd>{euro(o.paid_out_cents)}</dd>
            </div>
            <div>
              <dt>Unpaid invoices</dt>
              <dd>
                {o.unpaid_invoices} · {euro(o.unpaid_cents)}
              </dd>
            </div>
            <div>
              <dt>Your limit</dt>
              <dd>{euro(o.limit_cents)}</dd>
            </div>
          </dl>
        </>
      )}
    </Loaded>
  );
}

function ReceiptsTab({ open }: { open: boolean }) {
  return (
    <Loaded load={useRoute("receipts", open)}>
      {(r: Receipts) =>
        r.receipts.length === 0 ? (
          <p className="muted">No receipts yet. Every payment Ena makes or you decide shows here.</p>
        ) : (
          r.receipts.map((card) => (
            <Card key={card.id} event={{ kind: "card", card_kind: "receipt", card }} />
          ))
        )
      }
    </Loaded>
  );
}

function ForecastTab({ open }: { open: boolean }) {
  return (
    <Loaded load={useRoute("forecast", open)}>
      {(card) => <Card event={{ kind: "card", card_kind: "forecast", card }} />}
    </Loaded>
  );
}

const VERDICT_TITLE: Record<Credit["verdict"], string> = {
  no_lean_week: "No loan needed",
  reserve_covers: "Your own reserve covers it",
  loan_needed: "A loan may be needed",
};

function verdictText(c: Credit): string {
  const safe = euro(c.threshold_cents);
  if (c.lean_week === null || c.lean_week_closing_cents === null || c.gap_cents === null) {
    return `No week in the forecast closes below your ${safe} safe line.`;
  }
  const lean = `The week of ${day(c.lean_week)} closes at ${euro(c.lean_week_closing_cents)}, ${euro(c.gap_cents)} below your ${safe} safe line.`;
  return c.verdict === "loan_needed"
    ? `${lean} Setting money aside isn't enough to cover it.`
    : `${lean} Setting the instalment aside covers it.`;
}

function CreditTab({ open }: { open: boolean }) {
  return (
    <Loaded load={useRoute("credit", open)}>
      {(c: Credit) => (
        <>
          <article className={`card verdict verdict-${c.verdict}`} aria-label="Do I need a loan?">
            <header>
              <h3>{VERDICT_TITLE[c.verdict]}</h3>
              <span className="label">synthetic</span>
            </header>
            <p>{verdictText(c)}</p>
          </article>
          <dl className="facts">
            <div>
              <dt>Next loan instalment</dt>
              <dd>
                {euro(c.instalment_cents)} · {day(c.instalment_due)}
              </dd>
            </div>
          </dl>
          {c.reserve !== null ? <Card event={{ kind: "card", card_kind: "offer", card: c.reserve }} /> : null}
          {c.loan !== null ? <Card event={{ kind: "card", card_kind: "offer", card: c.loan }} /> : null}
          <p className="muted small">Ena never changes your existing loan.</p>
        </>
      )}
    </Loaded>
  );
}
