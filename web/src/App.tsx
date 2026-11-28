import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, sendChat, type CardEvent, type ChatEvent } from "./api";
import { Card, FetchContext, OpenBoard, type BoardTab } from "./cards";
import { Dashboard } from "./Dashboard";

// The demo questions of tests/chat_questions.json the chips offer
export const SUGGESTIONS = [
  "Pay the flour invoice",
  "Will I be short of cash this month?",
  "Should I set money aside for the loan instalment?",
  "Can I get a loan to cover the gap?",
  "Can I get an advance for the Georgiou wedding's costs?",
  "What did you pay this month?",
  "What's my balance?",
];

type Part = { kind: "text"; text: string } | { kind: "card"; event: CardEvent };

type Message =
  | { id: number; role: "owner"; text: string }
  | { id: number; role: "ena"; parts: Part[]; status: "streaming" | "done" | "error"; note?: string };

type Ena = Extract<Message, { role: "ena" }>;

/** The Ena message after one more event of its turn. */
export function apply(message: Ena, event: ChatEvent): Ena {
  switch (event.kind) {
    case "card":
      return { ...message, parts: [...message.parts, { kind: "card", event }] };
    case "text": {
      const last = message.parts.at(-1);
      if (last?.kind === "text") {
        const merged = { kind: "text" as const, text: `${last.text}\n\n${event.text}` };
        return { ...message, parts: [...message.parts.slice(0, -1), merged] };
      }
      return { ...message, parts: [...message.parts, { kind: "text", text: event.text }] };
    }
    case "done":
      return event.unconfirmed
        ? { ...message, status: "done", note: "A payment claim in this reply is unconfirmed" }
        : { ...message, status: "done" };
    case "error":
      return { ...message, status: "error", note: event.message };
  }
}

export function App({ fetcher }: { fetcher: typeof fetch }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<"chat" | "dashboard">("chat");
  const [tab, setTab] = useState<BoardTab>("overview");
  const nextId = useRef(0);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    end.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [messages]);

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    const ownerId = nextId.current++;
    const enaId = nextId.current++;
    setDraft("");
    setBusy(true);
    setMessages((all) => [
      ...all,
      { id: ownerId, role: "owner", text: trimmed },
      { id: enaId, role: "ena", parts: [], status: "streaming" },
    ]);
    const update = (change: (message: Ena) => Ena) =>
      setMessages((all) =>
        all.map((message) =>
          message.id === enaId && message.role === "ena" ? change(message) : message,
        ),
      );
    try {
      await sendChat(trimmed, (event) => update((message) => apply(message, event)), fetcher);
      update((message) =>
        message.status === "streaming"
          ? { ...message, status: "error", note: "The reply ended early, try again" }
          : message,
      );
    } catch (error) {
      const note = error instanceof ApiError ? error.message : "Ena can't be reached, try again";
      update((message) => ({ ...message, status: "error", note }));
    } finally {
      setBusy(false);
    }
  }

  function openBoard(on: BoardTab) {
    setTab(on);
    setView("dashboard");
  }

  /** A question asked from the dashboard: back to the chat, sent as the owner's. */
  function ask(question: string) {
    setView("chat");
    void send(question);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void send(draft);
  }

  return (
    <FetchContext value={fetcher}>
      <div className="phone">
        <header className="top">
          <div>
            <h1>Ena</h1>
            <p className="muted small">Your back-office assistant</p>
          </div>
          <div className="top-actions">
            <span className="label">demo</span>
            <button
              type="button"
              className="switch"
              onClick={() => setView(view === "chat" ? "dashboard" : "chat")}
            >
              {view === "chat" ? "Dashboard" : "Back to chat"}
            </button>
          </div>
        </header>
        <p className="banner" role="note">
          Synthetic demo: the bakery, its bank data and invoices are made up, and payments are
          simulated. No money moves.
        </p>
        <Dashboard open={view === "dashboard"} tab={tab} onTab={setTab} onAsk={ask} busy={busy} />
        <OpenBoard value={openBoard}>
        <main className="messages" aria-live="polite" hidden={view !== "chat"}>
          {messages.length === 0 ? (
            <p className="muted empty">Ask about your cash, invoices and payments.</p>
          ) : null}
          {messages.map((message) =>
            message.role === "owner" ? (
              <div key={message.id} className="bubble owner">
                {message.text}
              </div>
            ) : (
              <div key={message.id} className="bubble ena" aria-busy={message.status === "streaming"}>
                {message.parts.map((part, index) =>
                  part.kind === "text" ? (
                    <p key={index} className="text">
                      {part.text}
                    </p>
                  ) : (
                    <Card key={index} event={part.event} />
                  ),
                )}
                {message.status === "streaming" && message.parts.length === 0 ? (
                  <p className="muted typing">Ena is thinking…</p>
                ) : null}
                {message.note !== undefined ? (
                  <p className={message.status === "error" ? "error" : "muted small"} role="alert">
                    {message.note}
                  </p>
                ) : null}
              </div>
            ),
          )}
          <div ref={end} />
        </main>
        </OpenBoard>
        <footer className="composer" hidden={view !== "chat"}>
          <div className="chips" aria-label="Suggested questions">
            {SUGGESTIONS.map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                className="chip"
                disabled={busy}
                onClick={() => void send(suggestion)}
              >
                {suggestion}
              </button>
            ))}
          </div>
          <form onSubmit={submit}>
            <input
              aria-label="Message"
              placeholder="Message Ena"
              value={draft}
              maxLength={2000}
              onChange={(event) => setDraft(event.target.value)}
            />
            <button type="submit" className="primary" disabled={busy || !draft.trim()}>
              Send
            </button>
          </form>
        </footer>
      </div>
    </FetchContext>
  );
}
