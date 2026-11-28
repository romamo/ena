import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { APPROVAL, DECLINED, FORECAST, LOAN, RECEIPT, sse, streaming } from "./fixtures";

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

  it("says so when the API can't be reached", async () => {
    const fetcher = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    render(<App fetcher={fetcher} />);
    fireEvent.click(screen.getByRole("button", { name: "What's my balance?" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Ena can't be reached, try again");
  });
});
