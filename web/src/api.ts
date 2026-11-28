// The API's chat events and cards (ena/agent.py, ena/models.py, #52), as `POST /chat`
// streams them over SSE and the owner's taps answer them. The shapes are the API's: a
// change to them is a change there first.

export type IsoDate = string;
export type IsoDateTime = string;

export type ApprovalCard = {
  id: string;
  action: string;
  amount_cents: number;
  reason: string;
  status: "pending" | "approved" | "declined";
  created_at: IsoDateTime;
  decided_at: IsoDateTime | null;
};

export type ReceiptCard = {
  id: string;
  at: IsoDateTime;
  actor: "ena" | "owner";
  action: string;
  amount_cents: number;
  ref: string;
  result: string;
  explanation: string;
};

export type ForecastWeek = {
  week_start: IsoDate;
  opening_cents: number;
  in_cents: number;
  out_cents: number;
  closing_cents: number;
};

export type ForecastCard = {
  weeks: ForecastWeek[];
  lean_week: IsoDate | null;
  reason: string;
};

export type OfferCard = {
  id: string;
  kind: "reserve" | "loan" | "event_advance";
  amount_cents: number;
  term_days: number;
  basis: Record<string, number>;
  simulated: boolean;
};

export type CardEvent =
  | { kind: "card"; card_kind: "approval"; card: ApprovalCard }
  | { kind: "card"; card_kind: "receipt"; card: ReceiptCard }
  | { kind: "card"; card_kind: "forecast"; card: ForecastCard }
  | { kind: "card"; card_kind: "offer"; card: OfferCard };

export type ChatEvent =
  | { kind: "text"; text: string }
  | CardEvent
  | { kind: "done"; text: string; unconfirmed: boolean; cached: boolean }
  | { kind: "error"; message: string };

const EVENT_KINDS = new Set(["text", "card", "done", "error"]);
const CARD_KINDS = new Set(["approval", "receipt", "forecast", "offer"]);

export class ApiError extends Error {}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** One SSE event's data as a chat event; refuses one whose name and kind disagree. */
export function chatEvent(name: string, data: string): ChatEvent {
  const parsed: unknown = JSON.parse(data);
  if (!isRecord(parsed) || typeof parsed.kind !== "string" || !EVENT_KINDS.has(parsed.kind)) {
    throw new ApiError(`not a chat event: ${data}`);
  }
  if (parsed.kind !== name) {
    throw new ApiError(`event ${name} carries kind ${parsed.kind}`);
  }
  if (parsed.kind === "card" && !cardEvent(parsed)) {
    throw new ApiError(`not a card event: ${data}`);
  }
  return parsed as ChatEvent;
}

function cardEvent(value: Record<string, unknown>): boolean {
  return (
    typeof value.card_kind === "string" && CARD_KINDS.has(value.card_kind) && isRecord(value.card)
  );
}

/**
 * Splits an SSE stream into events as its chunks arrive. A chunk may end mid-line or
 * mid-event; `push` returns only the events it completed.
 */
export class SseParser {
  private buffer = "";
  private name = "message";
  private data: string[] = [];

  push(chunk: string): { name: string; data: string }[] {
    this.buffer += chunk;
    const events: { name: string; data: string }[] = [];
    let newline: number;
    while ((newline = this.buffer.search(/\r\n|\r|\n/)) !== -1) {
      const line = this.buffer.slice(0, newline);
      const width = this.buffer.startsWith("\r\n", newline) ? 2 : 1;
      // A lone "\r" at the chunk's end may be the first half of "\r\n"
      if (width === 1 && this.buffer[newline] === "\r" && newline === this.buffer.length - 1) {
        break;
      }
      this.buffer = this.buffer.slice(newline + width);
      const event = this.line(line);
      if (event !== null) events.push(event);
    }
    return events;
  }

  /** The events the stream's end completes: a last event with no blank line after it. */
  finish(): { name: string; data: string }[] {
    return this.push("\n\n");
  }

  private line(line: string): { name: string; data: string } | null {
    if (line === "") {
      if (this.data.length === 0) {
        this.name = "message";
        return null;
      }
      const event = { name: this.name, data: this.data.join("\n") };
      this.name = "message";
      this.data = [];
      return event;
    }
    if (line.startsWith(":")) return null;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") this.name = value;
    else if (field === "data") this.data.push(value);
    return null;
  }
}

/** Sends the owner's message and calls `onEvent` for each event of the turn, in order. */
export async function sendChat(
  text: string,
  onEvent: (event: ChatEvent) => void,
  fetcher: typeof fetch = fetch,
): Promise<void> {
  const response = await fetcher("/chat", {
    method: "POST",
    headers: { "content-type": "application/json", accept: "text/event-stream" },
    body: JSON.stringify({ text }),
  });
  if (!response.ok || response.body === null) {
    throw new ApiError(`the chat answered ${response.status}`);
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  const parser = new SseParser();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    for (const { name, data } of parser.push(value)) onEvent(chatEvent(name, data));
  }
  for (const { name, data } of parser.finish()) onEvent(chatEvent(name, data));
}

/** The owner's tap on an approval card; answers with the receipt card. */
export async function decide(
  approvalId: string,
  choice: "approve" | "decline",
  fetcher: typeof fetch = fetch,
): Promise<CardEvent> {
  const response = await fetcher(`/approvals/${encodeURIComponent(approvalId)}/${choice}`, {
    method: "POST",
  });
  const body: unknown = await response.json();
  if (!response.ok) {
    const detail = isRecord(body) && typeof body.detail === "string" ? body.detail : "";
    throw new ApiError(detail || `the ${choice} answered ${response.status}`);
  }
  if (!isRecord(body) || body.kind !== "card" || !cardEvent(body)) {
    throw new ApiError(`not a card event: ${JSON.stringify(body)}`);
  }
  return body as CardEvent;
}
