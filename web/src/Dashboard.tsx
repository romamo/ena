import { useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import {
  ApiError,
  getDashboard,
  readJinius,
  type JiniusFeed,
  type Credit,
  type DashboardRoutes,
  type ForecastView,
  type Overview,
  type Receipts,
} from "./api";
import { Card, FetchContext, OnDecided, type BoardTab } from "./cards";
import { day, euro } from "./format";

// The owner's dashboard: what the chat's cards said, kept on one screen. It only reads
// (ena/dashboard.py) and only formats: every amount, count and verdict is the API's. A
// pending payment is decided on its approval card, as in the chat; a question goes back
// to the chat.

const TABS: { id: BoardTab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "receipts", label: "Receipts" },
  { id: "forecast", label: "Forecast" },
  { id: "credit", label: "Loans" },
  { id: "jinius", label: "Jinius" },
];

type Load<T> = { state: "loading" } | { state: "ready"; data: T } | { state: "failed"; message: string };

type Ask = { onAsk: (question: string) => void; busy: boolean };

/** Reads `route` each time the dashboard opens on it or `version` moves. */
function useRoute<R extends keyof DashboardRoutes>(
  route: R,
  open: boolean,
  version: number,
): Load<DashboardRoutes[R]> {
  const fetcher = useContext(FetchContext);
  const [load, setLoad] = useState<Load<DashboardRoutes[R]>>({ state: "loading" });
  useEffect(() => {
    if (!open) return;
    let current = true;
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
  }, [route, open, version, fetcher]);
  return load;
}

export function Dashboard({
  open,
  tab,
  onTab,
  onAsk,
  busy,
}: { open: boolean; tab: BoardTab; onTab: (tab: BoardTab) => void } & Ask) {
  const [version, setVersion] = useState(0);
  const jinius = useJiniusRead();
  const reread = useCallback(() => setVersion((v) => v + 1), []);
  const props = { open, version, onAsk, busy };
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
            onClick={() => onTab(id)}
          >
            {label}
          </button>
        ))}
      </nav>
      <OnDecided value={reread}>
        <div className="board" role="tabpanel" aria-label={TABS.find((t) => t.id === tab)?.label}>
          {tab === "overview" ? <OverviewTab {...props} onTab={onTab} /> : null}
          {tab === "receipts" ? <ReceiptsTab {...props} /> : null}
          {tab === "forecast" ? <ForecastTab {...props} /> : null}
          {tab === "credit" ? <CreditTab {...props} /> : null}
          {tab === "jinius" ? <JiniusTab {...jinius} /> : null}
        </div>
      </OnDecided>
    </section>
  );
}

type TabProps = { open: boolean; version: number } & Ask;

function Loaded<T>({ load, children }: { load: Load<T>; children: (data: T) => ReactNode }) {
  if (load.state === "loading") return <p className="muted board-note">Loading…</p>;
  if (load.state === "failed")
    return (
      <p className="error board-note" role="alert">
        {load.message}
      </p>
    );
  return <div className="board-body">{children(load.data)}</div>;
}

/** Questions that go back to the chat, where Ena answers them with its tools. */
function AskEna({ questions, onAsk, busy }: { questions: string[] } & Ask) {
  return (
    <section className="ask" aria-label="Ask Ena">
      <h2 className="eyebrow">Ask Ena</h2>
      <div className="ask-list">
        {questions.map((question) => (
          <button key={question} type="button" className="ask-q" disabled={busy} onClick={() => onAsk(question)}>
            <span>{question}</span>
            <span aria-hidden="true">→</span>
          </button>
        ))}
      </div>
    </section>
  );
}

function Stat({ value, label, tone }: { value: string; label: string; tone?: "warn" | "good" | undefined }) {
  return (
    <div className={tone === undefined ? "stat" : `stat ${tone}`}>
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}

// Overview

function OverviewTab({ open, version, onAsk, busy, onTab }: TabProps & { onTab: (tab: BoardTab) => void }) {
  return (
    <Loaded load={useRoute("overview", open, version)}>
      {(o: Overview) => (
        <>
          <section className="hero" aria-label="Balance">
            <p className="hero-meta">
              {o.business} · {day(o.as_of)}
            </p>
            <p className="hero-amount">{euro(o.balance_cents)}</p>
            <p className="hero-meta">in your account today</p>
          </section>

          {o.pending.length > 0 ? (
            <section className="pending" aria-label="Needs your OK">
              <h2 className="eyebrow warn">Needs your OK · {o.pending.length}</h2>
              {o.pending.map((card) => (
                <Card key={card.id} event={{ kind: "card", card_kind: "approval", card }} />
              ))}
            </section>
          ) : null}

          {o.lean_week !== null && o.lean_week_closing_cents !== null ? (
            <button type="button" className="alert" onClick={() => onTab("forecast")}>
              <span className="alert-title">Lean week of {day(o.lean_week)}</span>
              <span>Cash falls to {euro(o.lean_week_closing_cents)}</span>
              <span className="alert-go">See the forecast →</span>
            </button>
          ) : (
            <p className="calm">No lean week in the next 8 weeks.</p>
          )}

          <section aria-label="This month">
            <h2 className="eyebrow">This month</h2>
            <div className="stats">
              <Stat
                value={String(o.paid_on_its_own)}
                label={`paid by Ena on its own · ${euro(o.paid_on_its_own_cents)}`}
                tone="good"
              />
              <Stat
                value={String(o.stepped_in)}
                label={o.stepped_in === 1 ? "time you stepped in" : "times you stepped in"}
              />
              <Stat value={euro(o.paid_out_cents)} label="paid out by Ena" />
              <Stat
                value={String(o.unpaid_invoices)}
                label={`unpaid invoices · ${euro(o.unpaid_cents)}`}
                tone={o.unpaid_invoices > 0 ? "warn" : undefined}
              />
            </div>
          </section>

          <p className="rule">
            Ena pays on its own up to <b>{euro(o.limit_cents)}</b> and asks you above it.
          </p>

          <AskEna questions={["What did you pay this month?", "Pay the flour invoice"]} onAsk={onAsk} busy={busy} />
        </>
      )}
    </Loaded>
  );
}

// Receipts

function ReceiptsTab({ open, version, onAsk, busy }: TabProps) {
  return (
    <Loaded load={useRoute("receipts", open, version)}>
      {(r: Receipts) => (
        <>
          <h2 className="eyebrow">
            {r.receipts.length} {r.receipts.length === 1 ? "receipt" : "receipts"}, newest first
          </h2>
          {r.receipts.length === 0 ? (
            <p className="calm">No receipts yet. Every payment Ena makes or you decide shows here.</p>
          ) : (
            <div className="stack">
              {r.receipts.map((card) => (
                <Card key={card.id} event={{ kind: "card", card_kind: "receipt", card }} />
              ))}
            </div>
          )}
          <AskEna questions={["What did you pay this month?"]} onAsk={onAsk} busy={busy} />
        </>
      )}
    </Loaded>
  );
}

// Forecast

function ForecastTab({ open, version, onAsk, busy }: TabProps) {
  return (
    <Loaded load={useRoute("forecast", open, version)}>
      {(f: ForecastView) => (
        <>
          <section className="panel" aria-label="Cash at the end of each week">
            <h2 className="eyebrow">Cash at the end of each week</h2>
            <Chart view={f} />
            <p className="muted small">{f.forecast.reason}</p>
          </section>
          <Card event={{ kind: "card", card_kind: "forecast", card: f.forecast }} />
          <AskEna
            questions={["Will I be short of cash this month?", "Should I set money aside for the loan instalment?"]}
            onAsk={onAsk}
            busy={busy}
          />
        </>
      )}
    </Loaded>
  );
}

/** Eight bars and the safe line, drawn to one scale; the numbers are the API's. */
function Chart({ view }: { view: ForecastView }) {
  const { weeks, lean_week } = view.forecast;
  const top = Math.max(1, view.threshold_cents, ...weeks.map((w) => w.closing_cents));
  const height = (cents: number) => `${(Math.max(0, cents) / top) * 100}%`;
  return (
    <div className="chart">
      <div className="plot" role="img" aria-label={`Weekly closing balances, safe line at ${euro(view.threshold_cents)}`}>
        <div className="safe" style={{ bottom: height(view.threshold_cents) }}>
          <span>safe line {euro(view.threshold_cents)}</span>
        </div>
        {weeks.map((week) => (
          <div key={week.week_start} className="col">
            <span
              className={week.closing_cents < view.threshold_cents ? "bar below" : "bar"}
              style={{ height: height(week.closing_cents) }}
            />
          </div>
        ))}
      </div>
      <div className="axis" aria-hidden="true">
        {weeks.map((week) => (
          <span key={week.week_start} className={week.week_start === lean_week ? "lean" : undefined}>
            {day(week.week_start)}
          </span>
        ))}
      </div>
    </div>
  );
}

// Loans

const VERDICT: Record<Credit["verdict"], { title: string; tone: string }> = {
  no_lean_week: { title: "No loan needed", tone: "good" },
  reserve_covers: { title: "Your own reserve covers it", tone: "good" },
  loan_needed: { title: "A loan may be needed", tone: "warn" },
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

/** How the lean week's closing and its gap make up the safe line. */
function GapBar({ c }: { c: Credit }) {
  if (c.lean_week_closing_cents === null || c.gap_cents === null) return null;
  const share = (cents: number) => `${Math.min(100, (Math.max(0, cents) / Math.max(1, c.threshold_cents)) * 100)}%`;
  return (
    <div className="gap">
      <div
        className="gap-bar"
        role="img"
        aria-label={`${euro(c.lean_week_closing_cents)} of the ${euro(c.threshold_cents)} safe line, ${euro(c.gap_cents)} short`}
      >
        <span className="have" style={{ width: share(c.lean_week_closing_cents) }} />
        <span className="short" style={{ width: share(c.gap_cents) }} />
      </div>
      <div className="gap-legend">
        <span>
          <i className="dot have" /> Lean week {euro(c.lean_week_closing_cents)}
        </span>
        <span>
          <i className="dot short" /> Short {euro(c.gap_cents)}
        </span>
      </div>
    </div>
  );
}

function CreditTab({ open, version, onAsk, busy }: TabProps) {
  return (
    <Loaded load={useRoute("credit", open, version)}>
      {(c: Credit) => (
        <>
          <article className={`verdict ${VERDICT[c.verdict].tone}`} aria-label="Do I need a loan?">
            <p className="eyebrow">Do I need a loan?</p>
            <h3>{VERDICT[c.verdict].title}</h3>
            <p>{verdictText(c)}</p>
            <GapBar c={c} />
          </article>

          <p className="rule">
            Next loan instalment <b>{euro(c.instalment_cents)}</b> on <b>{day(c.instalment_due)}</b>
          </p>

          {c.reserve !== null || c.loan !== null ? (
            <section className="stack" aria-label="What Ena suggests">
              <h2 className="eyebrow">What Ena suggests</h2>
              {c.reserve !== null ? <Card event={{ kind: "card", card_kind: "offer", card: c.reserve }} /> : null}
              {c.loan !== null ? <Card event={{ kind: "card", card_kind: "offer", card: c.loan }} /> : null}
            </section>
          ) : null}

          <p className="muted small">Ena never changes your existing loan.</p>
          <AskEna
            questions={["Should I set money aside for the loan instalment?", "Can I get a loan to cover the gap?"]}
            onAsk={onAsk}
            busy={busy}
          />
        </>
      )}
    </Loaded>
  );
}

// Jinius

/** Jinius's status names as the owner reads them; an unknown one is shown as Jinius sent it. */
const JINIUS_STATUS: Record<string, string> = {
  WaitingApproval: "Waiting approval",
  Approved: "Approved",
  Paid: "Paid",
  Rejected: "Rejected",
  Voided: "Voided",
};

type JiniusRead = { load: Load<JiniusFeed> | null; read: () => void };

/** The Jinius read, held by the dashboard so a tab switch doesn't lose it or its answer. */
function useJiniusRead(): JiniusRead {
  const fetcher = useContext(FetchContext);
  const [load, setLoad] = useState<Load<JiniusFeed> | null>(null);
  const read = useCallback(() => {
    setLoad({ state: "loading" });
    readJinius(fetcher).then(
      (data) => setLoad({ state: "ready", data }),
      (error: unknown) =>
        setLoad({
          state: "failed",
          message: error instanceof ApiError ? error.message : "Jinius can't be reached, try again",
        }),
    );
  }, [fetcher]);
  return { load, read };
}

/** Reads the invoices sent to the business from Jinius, only when the owner taps. */
function JiniusTab({ load, read }: JiniusRead) {
  return (
    <div className="board-body">
      <section className="panel" aria-label="Invoices on Jinius">
        <h2 className="eyebrow">Invoices on Jinius</h2>
        <p className="muted small">The invoices your suppliers sent you through Jinius, read when you tap.</p>
        <button type="button" className="primary" disabled={load?.state === "loading"} onClick={read}>
          {load?.state === "loading" ? "Reading…" : "Read invoices from Jinius"}
        </button>
      </section>
      {load?.state === "failed" ? (
        <p className="error board-note" role="alert">
          {load.message}
        </p>
      ) : null}
      {load?.state === "ready" ? <JiniusInvoices feed={load.data} /> : null}
    </div>
  );
}

function JiniusInvoices({ feed }: { feed: JiniusFeed }) {
  return (
    <section className="stack" aria-label="Invoices read from Jinius">
      <p className={feed.source === "live" ? "source live" : "source saved"}>
        {feed.source === "live" ? "Live" : "Saved data"} · {feed.note}
      </p>
      <h2 className="eyebrow">
        {feed.invoices.length} {feed.invoices.length === 1 ? "invoice" : "invoices"}
      </h2>
      {feed.invoices.length === 0 ? (
        <p className="calm">No invoices on Jinius yet.</p>
      ) : (
        feed.invoices.map((invoice) => (
          <article key={invoice.number} className="panel jinius-invoice" aria-label={`Invoice ${invoice.number}`}>
            <div className="jinius-row">
              <b>{invoice.supplier}</b>
              <b className="amount">{euro(invoice.amount_cents)}</b>
            </div>
            <div className="jinius-row muted small">
              <span>
                {invoice.number} · issued {day(invoice.issue_date)}
                {invoice.due_date === null ? "" : ` · due ${day(invoice.due_date)}`}
              </span>
              <span>{JINIUS_STATUS[invoice.status] ?? invoice.status}</span>
            </div>
          </article>
        ))
      )}
    </section>
  );
}
