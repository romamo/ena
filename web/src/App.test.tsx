import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App, LIVE_SUGGESTION } from "./App";
import {
  ACCEPTED,
  ACCOUNT,
  ADVANCE,
  APPROVAL,
  CONSENT,
  DECLINED,
  FORECAST,
  LOAN,
  ORDER,
  PAY_LINK,
  QUOTES,
  RECEIPT,
  SANDBOX_RECEIPT,
  sse,
  streaming,
} from "./fixtures";

afterEach(cleanup);

function answering(routes: Record<string, () => Response>) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const route = routes[String(input)];
    if (route === undefined) throw new Error(`unexpected request ${String(input)}`);
    return route();
  });
}

describe("App", () => {
  it("labels the data as synthetic and the payments as simulated", () => {
    render(<App fetcher={answering({})} />);
    expect(screen.getByRole("note").textContent).toMatch(/Synthetic demo.*payments are\s+simulated/s);
  });

  it("sends a chip's question and streams the reply with its cards, in order", async () => {
    const fetcher = answering({
      "/chat": () =>
        streaming(
          sse(
            FORECAST,
            { kind: "text", text: "Week 3 is lean." },
            { kind: "text", text: "A loan offer is below." },
            LOAN,
            { kind: "done", text: "Week 3 is lean.\n\nA loan offer is below.", unconfirmed: false, cached: true },
          ),
        ),
    });
    render(<App fetcher={fetcher} />);
    fireEvent.click(screen.getByRole("button", { name: "Will I be short of cash this month?" }));

    const forecast = await screen.findByRole("article", { name: "Forecast" });
    expect(within(forecast).getByText(/16 Nov · lean/)).toBeTruthy();
    expect(within(forecast).getByText("€349.69")).toBeTruthy();
    const offer = screen.getByRole("article", { name: "Offer" });
    expect(within(offer).getByText("offer, simulated")).toBeTruthy();
    expect(within(offer).getByText("€1,000.00")).toBeTruthy();
    expect(screen.getByText("Will I be short of cash this month?", { selector: ".owner" })).toBeTruthy();

    const reply = forecast.closest(".ena") as HTMLElement;
    const order = [...reply.children].map((child) =>
      child.getAttribute("aria-label") ?? child.textContent,
    );
    expect(order).toEqual(["Forecast", "Week 3 is lean.\n\nA loan offer is below.", "Offer"]);
  });

  it("approves from the card and shows the receipt the API answers", async () => {
    const fetcher = answering({
      "/chat": () =>
        streaming(sse(APPROVAL, { kind: "text", text: "It waits for you." }, { kind: "done", text: "It waits for you.", unconfirmed: false, cached: false })),
      "/approvals/approval-syn-inv-0002/approve": () => Response.json(RECEIPT),
    });
    render(<App fetcher={fetcher} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), {
      target: { value: "Pay the flour invoice" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    const approval = await screen.findByRole("article", { name: "Approval" });
    expect(within(approval).getByText("€1,240.00")).toBeTruthy();
    fireEvent.click(within(approval).getByRole("button", { name: "Approve" }));

    const receipt = await within(approval).findByRole("article", { name: "Receipt" });
    expect(within(receipt).getByText("Receipt · paid")).toBeTruthy();
    expect(within(receipt).getByText("simulated payment")).toBeTruthy();
    expect(within(approval).queryByRole("button", { name: "Approve" })).toBeNull();
    expect(within(approval).getByText("You approved")).toBeTruthy();
    expect(fetcher).toHaveBeenCalledWith("/approvals/approval-syn-inv-0002/approve", { method: "POST" });
  });

  it("takes an event advance with the owner's Accept, and never a loan", async () => {
    const fetcher = answering({
      "/chat": () => streaming(sse(ADVANCE, LOAN, { kind: "done", text: "x", unconfirmed: false, cached: false })),
      "/offers/advance-syn-event-0014-280000/accept": () => Response.json(ACCEPTED),
    });
    render(<App fetcher={fetcher} />);
    fireEvent.click(screen.getByRole("button", { name: "Can I get an advance for the Georgiou wedding's costs?" }));
    const [advance, loan] = await screen.findAllByRole("article", { name: "Offer" });
    if (advance === undefined || loan === undefined) throw new Error("two offer cards expected");
    expect(within(loan).queryByRole("button", { name: "Accept" })).toBeNull();
    fireEvent.click(within(advance).getByRole("button", { name: "Accept" }));
    const receipt = await within(advance).findByRole("article", { name: "Receipt" });
    expect(within(receipt).getByText("Receipt · accepted")).toBeTruthy();
    expect(within(advance).queryByRole("button", { name: "Accept" })).toBeNull();
    expect(fetcher).toHaveBeenCalledWith("/offers/advance-syn-event-0014-280000/accept", {
      method: "POST",
    });
  });

  it("shows a pay link with its QR code and the payment page", async () => {
    const fetcher = answering({
      "/chat": () => streaming(sse(PAY_LINK, { kind: "done", text: "Here it is.", unconfirmed: false, cached: false })),
    });
    render(<App fetcher={fetcher} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), {
      target: { value: "Make a €15 pay link for two loaves and a coffee" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    const card = await screen.findByRole("article", { name: "Pay link" });
    expect(within(card).getByText("€15.00")).toBeTruthy();
    expect(within(card).getByText("JCC sandbox")).toBeTruthy();
    const qr = await within(card).findByRole("img", { name: "QR code to pay €15.00 by card" });
    expect(qr.getAttribute("src")).toMatch(/^data:image\/svg\+xml;charset=utf-8,%3Csvg/);
    const page = within(card).getByRole("link", { name: "Open the payment page" });
    expect(page.getAttribute("href")).toBe(PAY_LINK.card_kind === "pay_link" ? PAY_LINK.card.url : "");
    expect(within(card).getByText(/Nothing has moved yet/).textContent).toContain("Only a test card pays it.");
  });

  it("compares quotations and orders from the one the owner taps", async () => {
    const fetcher = answering({
      "/chat": () => streaming(sse(QUOTES, { kind: "done", text: "x", unconfirmed: false, cached: false })),
      "/quotes/syn-quote-0006/award": () => Response.json(ORDER),
    });
    render(<App fetcher={fetcher} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Which supplier should I order the wedding supplies from?" }),
    );

    const quotes = await screen.findByRole("article", { name: "Quotations" });
    const suppliers = within(quotes)
      .getAllByRole("listitem")
      .map((item) => item.querySelector(".quote-head span")?.textContent);
    expect(suppliers).toEqual([
      "Limassol Cash & Carry (synthetic)",
      "Kyrenia Dairy (synthetic)",
      "Paphos Wholesale (synthetic)",
    ]);
    expect(within(quotes).getByText(/Ena recommends/)).toBeTruthy();
    expect(within(quotes).getByText(/delivers 2026-12-16, needed by 2026-12-14/)).toBeTruthy();
    // Every quotation that still holds can be ordered, the late one too: the owner decides
    expect(within(quotes).getAllByRole("button", { name: /^Order from/ })).toHaveLength(3);

    fireEvent.click(within(quotes).getByRole("button", { name: "Order from Limassol Cash & Carry (synthetic)" }));

    const order = await within(quotes).findByRole("article", { name: "Purchase order" });
    expect(within(order).getByText("€605.00")).toBeTruthy();
    expect(within(order).getByText("sent outside Jinius, simulated")).toBeTruthy();
    expect(within(quotes).queryByRole("button", { name: /^Order from/ })).toBeNull();
    expect(fetcher).toHaveBeenCalledWith("/quotes/syn-quote-0006/award", { method: "POST" });
  });

  it("shows the refusal when a quotation can't be ordered", async () => {
    const fetcher = answering({
      "/chat": () => streaming(sse(QUOTES, { kind: "done", text: "x", unconfirmed: false, cached: false })),
      "/quotes/syn-quote-0004/award": () =>
        Response.json({ detail: "already ordered from Limassol" }, { status: 409 }),
    });
    render(<App fetcher={fetcher} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Which supplier should I order the wedding supplies from?" }),
    );
    const quotes = await screen.findByRole("article", { name: "Quotations" });
    fireEvent.click(within(quotes).getByRole("button", { name: "Order from Kyrenia Dairy (synthetic)" }));

    expect((await within(quotes).findByRole("alert")).textContent).toBe("already ordered from Limassol");
  });

  it("labels a receipt simulated only when the API says so", async () => {
    const fetcher = answering({
      "/chat": () => streaming(sse(APPROVAL, { kind: "done", text: "x", unconfirmed: false, cached: false })),
      "/approvals/approval-syn-inv-0002/decline": () => Response.json(DECLINED),
    });
    render(<App fetcher={fetcher} />);
    fireEvent.click(screen.getByRole("button", { name: "Pay the flour invoice" }));
    const approval = await screen.findByRole("article", { name: "Approval" });
    fireEvent.click(within(approval).getByRole("button", { name: "Decline" }));
    const receipt = await within(approval).findByRole("article", { name: "Receipt" });
    expect(within(receipt).getByText("Receipt · declined")).toBeTruthy();
    expect(within(receipt).queryByText("simulated payment")).toBeNull();
    expect(within(approval).getByText("You declined")).toBeTruthy();
  });

  it("shows the API's reason when a tap is refused and keeps the buttons", async () => {
    const fetcher = answering({
      "/chat": () => streaming(sse(APPROVAL, { kind: "done", text: "x", unconfirmed: false, cached: false })),
      "/approvals/approval-syn-inv-0002/decline": () =>
        Response.json({ detail: "approval-syn-inv-0002 is already approved" }, { status: 409 }),
    });
    render(<App fetcher={fetcher} />);
    fireEvent.click(screen.getByRole("button", { name: "Pay the flour invoice" }));
    const approval = await screen.findByRole("article", { name: "Approval" });
    fireEvent.click(within(approval).getByRole("button", { name: "Decline" }));
    expect((await within(approval).findByRole("alert")).textContent).toBe(
      "approval-syn-inv-0002 is already approved",
    );
    expect(within(approval).getByRole("button", { name: "Approve" })).toBeTruthy();
  });

  it("shows the error event and lets the owner ask again", async () => {
    const fetcher = answering({
      "/chat": () =>
        streaming(sse({ kind: "error", message: "Ena could not answer, try again: no Anthropic credential" })),
    });
    render(<App fetcher={fetcher} />);
    fireEvent.click(screen.getByRole("button", { name: "What's my balance?" }));
    expect((await screen.findByRole("alert")).textContent).toContain("no Anthropic credential");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "What's my balance?" }).hasAttribute("disabled")).toBe(false),
    );
  });

  it("labels payments as the Bank of Cyprus sandbox's when the API says the bank is live", async () => {
    render(<App fetcher={answering({ "/bank": () => Response.json({ live: true }) })} />);
    await waitFor(() => expect(screen.getByRole("note").textContent).toMatch(/Bank of Cyprus sandbox/));
    expect(screen.getByRole("button", { name: LIVE_SUGGESTION })).toBeTruthy();
  });

  it("after Approve, opens the bank's screen and shows the receipt once the bank answers", async () => {
    const opened = vi.spyOn(window, "open").mockReturnValue(null);
    let settled = false;
    const fetcher = answering({
      "/bank": () => Response.json({ live: true }),
      "/chat": () => streaming(sse(APPROVAL, { kind: "done", text: "x", unconfirmed: false, cached: false })),
      "/approvals/approval-syn-inv-0002/approve": () => Response.json(CONSENT),
      "/consents/consent-syn-inv-0002": () => Response.json(settled ? SANDBOX_RECEIPT : CONSENT),
    });
    render(<App fetcher={fetcher} />);
    fireEvent.click(screen.getByRole("button", { name: "Pay the flour invoice" }));
    const approval = await screen.findByRole("article", { name: "Approval" });
    fireEvent.click(within(approval).getByRole("button", { name: "Approve" }));

    const consent = await within(approval).findByRole("article", { name: "Bank confirmation" });
    expect(within(approval).getByText("You approved")).toBeTruthy();
    expect(within(consent).queryByRole("article", { name: "Receipt" })).toBeNull();
    fireEvent.click(within(consent).getByRole("button", { name: "Confirm in Bank of Cyprus" }));
    expect(opened).toHaveBeenCalledWith(expect.stringContaining("paymentid=pay-1"), "_blank");
    expect(within(consent).getByText("Waiting for the bank's confirmation…")).toBeTruthy();

    settled = true;
    const receipt = await within(consent).findByRole("article", { name: "Receipt" }, { timeout: 4000 });
    expect(within(receipt).getByText("BoC sandbox payment")).toBeTruthy();
    expect(within(consent).queryByRole("button", { name: "Cancel" })).toBeNull();
    opened.mockRestore();
  });

  it("keeps waiting, with Cancel, when one check of the bank confirmation fails", async () => {
    const opened = vi.spyOn(window, "open").mockReturnValue(null);
    const fetcher = answering({
      "/chat": () => streaming(sse(CONSENT, { kind: "done", text: "x", unconfirmed: false, cached: false })),
      "/consents/consent-syn-inv-0002": () => Response.json({ detail: "the API is restarting" }, { status: 503 }),
    });
    render(<App fetcher={fetcher} />);
    fireEvent.click(screen.getByRole("button", { name: "Pay the flour invoice" }));
    const consent = await screen.findByRole("article", { name: "Bank confirmation" });
    fireEvent.click(within(consent).getByRole("button", { name: "Confirm in Bank of Cyprus" }));

    expect(await within(consent).findByText(/last check failed: the API is restarting/, {}, { timeout: 4000 })).toBeTruthy();
    expect(within(consent).getByRole("button", { name: "Cancel" })).toBeTruthy();
    opened.mockRestore();
  });

  it("cancels a waiting bank confirmation", async () => {
    const fetcher = answering({
      "/chat": () => streaming(sse(CONSENT, { kind: "done", text: "x", unconfirmed: false, cached: false })),
      "/consents/consent-syn-inv-0002/cancel": () => Response.json(DECLINED),
    });
    render(<App fetcher={fetcher} />);
    fireEvent.click(screen.getByRole("button", { name: "Pay the flour invoice" }));
    const consent = await screen.findByRole("article", { name: "Bank confirmation" });
    fireEvent.click(within(consent).getByRole("button", { name: "Cancel" }));
    expect(await within(consent).findByRole("article", { name: "Receipt" })).toBeTruthy();
    expect(fetcher).toHaveBeenCalledWith("/consents/consent-syn-inv-0002/cancel", { method: "POST" });
  });

  it("shows the live sandbox account apart from the bakery's numbers", async () => {
    const fetcher = answering({
      "/chat": () => streaming(sse(ACCOUNT, { kind: "done", text: "x", unconfirmed: false, cached: false })),
    });
    render(<App fetcher={fetcher} />);
    fireEvent.click(screen.getByRole("button", { name: "What's my balance?" }));
    const account = await screen.findByRole("article", { name: "Bank account" });
    expect(within(account).getByText("live, BoC sandbox")).toBeTruthy();
    expect(within(account).getByText("€8,250.50")).toBeTruthy();
    expect(within(account).getByText(/not the bakery's/)).toBeTruthy();
    expect(within(account).getByText("+€120.25")).toBeTruthy();
  });

  it("marks the account card when it shows the last copy", async () => {
    if (ACCOUNT.card_kind !== "account") throw new Error("an account card expected");
    const stale = { ...ACCOUNT, card: { ...ACCOUNT.card, stale: true } };
    const fetcher = answering({
      "/chat": () => streaming(sse(stale, { kind: "done", text: "x", unconfirmed: false, cached: false })),
    });
    render(<App fetcher={fetcher} />);
    fireEvent.click(screen.getByRole("button", { name: "What's my balance?" }));
    const account = await screen.findByRole("article", { name: "Bank account" });
    expect(within(account).getByText("last copy, BoC sandbox")).toBeTruthy();
    expect(within(account).getByText(/only a few account reads a day/)).toBeTruthy();
  });

  it("says so when the API can't be reached", async () => {
    const fetcher = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    render(<App fetcher={fetcher} />);
    fireEvent.click(screen.getByRole("button", { name: "What's my balance?" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Ena can't be reached, try again");
  });
});
