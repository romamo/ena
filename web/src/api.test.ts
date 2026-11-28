import { describe, expect, it, vi } from "vitest";
import { ApiError, SseParser, chatEvent, decide, sendChat, type ChatEvent } from "./api";
import { APPROVAL, RECEIPT, sse, streaming } from "./fixtures";

const TURN = [
  APPROVAL,
  { kind: "text", text: "The flour invoice waits for your approval." },
  { kind: "done", text: "The flour invoice waits for your approval.", unconfirmed: false, cached: false },
];

describe("SseParser", () => {
  it.each([
    ["LF", "\n"],
    ["CRLF", "\r\n"],
    ["CR", "\r"],
  ])("reads events with %s line ends, in chunks of any size", (_name, end) => {
    const body = `event: text${end}data: {"kind":"text","text":"a"}${end}${end}: a comment${end}event: done${end}data: {"kind":"done"}${end}${end}`;
    for (const size of [1, 2, 5, body.length]) {
      const parser = new SseParser();
      const events = [];
      for (let i = 0; i < body.length; i += size) events.push(...parser.push(body.slice(i, i + size)));
      events.push(...parser.finish());
      expect(events).toEqual([
        { name: "text", data: '{"kind":"text","text":"a"}' },
        { name: "done", data: '{"kind":"done"}' },
      ]);
    }
  });

  it("joins a multi-line data field", () => {
    expect(new SseParser().push("data: one\ndata: two\n\n")).toEqual([
      { name: "message", data: "one\ntwo" },
    ]);
  });
});

describe("chatEvent", () => {
  it("refuses an event whose name and kind disagree", () => {
    expect(() => chatEvent("done", '{"kind":"text","text":"a"}')).toThrow(ApiError);
  });

  it("refuses an unknown card kind", () => {
    expect(() => chatEvent("card", '{"kind":"card","card_kind":"loan","card":{}}')).toThrow(ApiError);
  });
});

describe("sendChat", () => {
  it("posts the message and yields the turn's events in order", async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      streaming(sse(...TURN), 3),
    );
    const events: ChatEvent[] = [];
    await sendChat("Pay the flour invoice", (event) => events.push(event), fetcher);
    expect(events).toEqual(TURN);
    expect(fetcher).toHaveBeenCalledWith("/chat", expect.objectContaining({ method: "POST" }));
    const init = fetcher.mock.calls[0]?.[1];
    expect(JSON.parse(String(init?.body))).toEqual({ text: "Pay the flour invoice" });
  });

  it("fails on an answer that is not 200", async () => {
    const fetcher = vi.fn(async () => new Response("{}", { status: 422 }));
    await expect(sendChat("hi", () => undefined, fetcher)).rejects.toThrow("the chat answered 422");
  });
});

describe("decide", () => {
  it("answers with the receipt card", async () => {
    const fetcher = vi.fn(async () => Response.json(RECEIPT));
    expect(await decide("approval-syn-inv-0002", "approve", fetcher)).toEqual(RECEIPT);
    expect(fetcher).toHaveBeenCalledWith("/approvals/approval-syn-inv-0002/approve", { method: "POST" });
  });

  it("names the API's reason for a refused tap", async () => {
    const fetcher = vi.fn(async () =>
      Response.json({ detail: "approval-syn-inv-0002 is already approved" }, { status: 409 }),
    );
    await expect(decide("approval-syn-inv-0002", "decline", fetcher)).rejects.toThrow(
      "approval-syn-inv-0002 is already approved",
    );
  });
});
