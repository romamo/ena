import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { CREDIT, FORECAST, OVERVIEW, RECEIPT, RECEIPTS, sse, streaming } from "./fixtures";

afterEach(cleanup);

function answering(routes: Record<string, () => Response>) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const route = routes[String(input)];
    if (route === undefined) throw new Error(`unexpected request ${String(input)}`);
    return route();
  });
}

const FORECAST_VIEW = {
  forecast: FORECAST.card_kind === "forecast" ? FORECAST.card : null,
  threshold_cents: 95000,
};

const ROUTES = {
  "/dashboard/overview": () => Response.json(OVERVIEW),
  "/dashboard/receipts": () => Response.json(RECEIPTS),
  "/dashboard/forecast": () => Response.json(FORECAST_VIEW),
  "/dashboard/credit": () => Response.json(CREDIT),
};

function openDashboard(fetcher: typeof fetch) {
  render(<App fetcher={fetcher} />);
  fireEvent.click(screen.getByRole("button", { name: "Dashboard" }));
}

describe("Dashboard", () => {
  it("opens from the chat's header and goes back to the chat", async () => {
    openDashboard(answering(ROUTES));
    expect(screen.getByRole("region", { name: "Dashboard" })).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "Message" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Back to chat" }));
    expect(screen.getByRole("textbox", { name: "Message" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Dashboard" })).toBeNull();
  });

  it("shows the overview's figures as the API computes them, and what needs an OK", async () => {
    openDashboard(answering(ROUTES));
    const balance = await screen.findByRole("region", { name: "Balance" });
    expect(within(balance).getByText("€2,669.19")).toBeTruthy();
    expect(screen.getByText("Lean week of 16 Nov")).toBeTruthy();
    expect(screen.getByText("Cash falls to €349.69")).toBeTruthy();
    expect(screen.getByText("paid by Ena on its own · €180.00")).toBeTruthy();
    expect(screen.getByText("times you stepped in")).toBeTruthy();
    expect(screen.getByText("unpaid invoices · €1,240.00")).toBeTruthy();
    const pending = screen.getByRole("region", { name: "Needs your OK" });
    const approval = within(pending).getByRole("article", { name: "Approval" });
    expect(within(approval).getByRole("button", { name: "Approve" })).toBeTruthy();
  });

  it("opens the forecast from the lean-week alert", async () => {
    openDashboard(answering(ROUTES));
    fireEvent.click(await screen.findByRole("button", { name: /Lean week of 16 Nov/ }));
    expect(screen.getByRole("tab", { name: "Forecast" }).getAttribute("aria-selected")).toBe("true");
    expect(await screen.findByRole("img", { name: "Weekly closing balances, safe line at €950.00" })).toBeTruthy();
  });

  it("approves through the same route as the chat, then reads the overview again", async () => {
    let decided = false;
    const fetcher = answering({
      ...ROUTES,
      "/dashboard/overview": () =>
        Response.json(decided ? { ...OVERVIEW, pending: [], stepped_in: 1 } : OVERVIEW),
      "/approvals/approval-syn-inv-0002/approve": () => {
        decided = true;
        return Response.json(RECEIPT);
      },
    });
    openDashboard(fetcher);
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByText("time you stepped in")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Needs your OK" })).toBeNull();
    expect(fetcher).toHaveBeenCalledWith("/approvals/approval-syn-inv-0002/approve", { method: "POST" });
  });

  it("lists the receipts and draws the forecast against its safe line", async () => {
    openDashboard(answering(ROUTES));
    fireEvent.click(screen.getByRole("tab", { name: "Receipts" }));
    const receipt = await screen.findByRole("article", { name: "Receipt" });
    expect(within(receipt).getByText("simulated payment")).toBeTruthy();
    expect(within(receipt).queryByRole("button", { name: /See in dashboard/ })).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "Forecast" }));
    expect(await screen.findByText("safe line €950.00")).toBeTruthy();
    expect(screen.getByRole("article", { name: "Forecast" })).toBeTruthy();
  });

  it("says whether the lean week needs a loan, with the reason in the API's numbers", async () => {
    openDashboard(answering(ROUTES));
    fireEvent.click(screen.getByRole("tab", { name: "Loans" }));
    const verdict = await screen.findByRole("article", { name: "Do I need a loan?" });
    expect(within(verdict).getByText("A loan may be needed")).toBeTruthy();
    expect(verdict.textContent).toMatch(
      /week of 16 Nov closes at €349\.69, €600\.31 below your €950\.00 safe line/,
    );
    expect(within(verdict).getByRole("img", { name: "€349.69 of the €950.00 safe line, €600.31 short" })).toBeTruthy();
    const offers = screen.getAllByRole("article", { name: "Offer" });
    expect(offers.map((offer) => within(offer).getByRole("heading").textContent)).toEqual([
      "Set money aside",
      "Digital Business Loan",
    ]);
    expect(within(offers[1]!).getByText("offer, simulated")).toBeTruthy();
    expect(screen.getByText("Ena never changes your existing loan.")).toBeTruthy();
  });

  it("says plainly when no loan is needed", async () => {
    openDashboard(
      answering({
        ...ROUTES,
        "/dashboard/credit": () =>
          Response.json({
            ...CREDIT,
            verdict: "no_lean_week",
            lean_week: null,
            lean_week_closing_cents: null,
            gap_cents: null,
            reserve: null,
            loan: null,
          }),
      }),
    );
    fireEvent.click(screen.getByRole("tab", { name: "Loans" }));
    const verdict = await screen.findByRole("article", { name: "Do I need a loan?" });
    expect(within(verdict).getByText("No loan needed")).toBeTruthy();
    expect(within(verdict).queryByRole("img")).toBeNull();
    expect(screen.queryByRole("article", { name: "Offer" })).toBeNull();
  });

  it("sends a question back to the chat", async () => {
    const fetcher = answering({
      ...ROUTES,
      "/chat": () =>
        streaming(sse({ kind: "text", text: "A loan offer is below." }, { kind: "done", text: "A loan offer is below.", unconfirmed: false, cached: false })),
    });
    openDashboard(fetcher);
    fireEvent.click(screen.getByRole("tab", { name: "Loans" }));
    const ask = await screen.findByRole("region", { name: "Ask Ena" });
    fireEvent.click(within(ask).getByRole("button", { name: /Can I get a loan to cover the gap\?/ }));
    expect(screen.getByRole("textbox", { name: "Message" })).toBeTruthy();
    expect(screen.getByText("Can I get a loan to cover the gap?", { selector: ".owner" })).toBeTruthy();
    expect(await screen.findByText("A loan offer is below.", { selector: ".text" })).toBeTruthy();
  });

  it("opens the dashboard on the card's tab from a card in the chat", async () => {
    const fetcher = answering({
      ...ROUTES,
      "/chat": () =>
        streaming(sse(FORECAST, { kind: "done", text: "", unconfirmed: false, cached: false })),
    });
    render(<App fetcher={fetcher} />);
    fireEvent.click(screen.getByRole("button", { name: "Will I be short of cash this month?" }));
    const card = await screen.findByRole("article", { name: "Forecast" });
    fireEvent.click(within(card).getByRole("button", { name: "See in dashboard →" }));
    expect(screen.getByRole("tab", { name: "Forecast" }).getAttribute("aria-selected")).toBe("true");
    await waitFor(() => expect(fetcher).toHaveBeenCalledWith("/dashboard/forecast"));
  });

  it("names the API's reason when a read fails", async () => {
    openDashboard(
      answering({
        ...ROUTES,
        "/dashboard/overview": () => Response.json({ detail: "no forecast on 2026-11-30" }, { status: 409 }),
      }),
    );
    expect((await screen.findByRole("alert")).textContent).toBe("no forecast on 2026-11-30");
  });

  it("reads the invoices from Jinius only when the owner taps the button", async () => {
    const feed = {
      source: "live",
      note: "Jinius integration environment, live",
      invoices: [
        {
          id: "d7778e64-5206-45e5-9d04-b24174130ec3",
          number: "EFM-2026-002",
          supplier: "Ena Flour Mill Ltd",
          amount_cents: 124000,
          issue_date: "2026-11-28",
          due_date: "2026-12-11",
          status: "WaitingApproval",
        },
      ],
    };
    const fetcher = answering({ ...ROUTES, "/jinius/invoices": () => Response.json(feed) });
    openDashboard(fetcher);
    fireEvent.click(screen.getByRole("tab", { name: "Jinius" }));
    expect(fetcher).not.toHaveBeenCalledWith("/jinius/invoices");

    fireEvent.click(screen.getByRole("button", { name: "Read invoices from Jinius" }));
    const invoice = await screen.findByRole("article", { name: "Invoice EFM-2026-002" });
    expect(invoice.textContent).toContain("Ena Flour Mill Ltd");
    expect(invoice.textContent).toContain("€1,240.00");
    expect(invoice.textContent).toContain("due 11 Dec");
    expect(invoice.textContent).toContain("Waiting approval");
    expect(screen.getByText(/Live · Jinius integration environment, live/)).toBeTruthy();
  });

  it("names why Jinius could not be read", async () => {
    openDashboard(
      answering({
        ...ROUTES,
        "/jinius/invoices": () => Response.json({ detail: "Jinius is not configured" }, { status: 503 }),
      }),
    );
    fireEvent.click(screen.getByRole("tab", { name: "Jinius" }));
    fireEvent.click(screen.getByRole("button", { name: "Read invoices from Jinius" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Jinius is not configured");
  });

  it("keeps the Jinius answer that arrives after the owner switched tabs", async () => {
    let answer: (response: Response) => void = () => {};
    const pending = new Promise<Response>((resolve) => {
      answer = resolve;
    });
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/jinius/invoices") return pending;
      const route = (ROUTES as Record<string, () => Response>)[String(input)];
      if (route === undefined) throw new Error(`unexpected request ${String(input)}`);
      return route();
    });
    openDashboard(fetcher);
    fireEvent.click(screen.getByRole("tab", { name: "Jinius" }));
    fireEvent.click(screen.getByRole("button", { name: "Read invoices from Jinius" }));
    fireEvent.click(screen.getByRole("tab", { name: "Receipts" }));
    answer(Response.json({ source: "fixture", note: "Jinius sandbox offline, showing saved data", invoices: [] }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledWith("/dashboard/receipts"));

    fireEvent.click(screen.getByRole("tab", { name: "Jinius" }));
    expect((await screen.findByText(/Saved data · Jinius sandbox offline/)).textContent).toContain("Saved data");
    expect(screen.getByText("No invoices on Jinius yet.")).toBeTruthy();
  });

  it("refuses a Jinius answer whose invoice is malformed", async () => {
    const feed = { source: "live", note: "live", invoices: [{ number: "EFM-1", amount_cents: 12.5 }] };
    openDashboard(answering({ ...ROUTES, "/jinius/invoices": () => Response.json(feed) }));
    fireEvent.click(screen.getByRole("tab", { name: "Jinius" }));
    fireEvent.click(screen.getByRole("button", { name: "Read invoices from Jinius" }));
    expect((await screen.findByRole("alert")).textContent).toContain("not a Jinius answer");
  });

  it("approves a Jinius invoice, then marks it paid, on the owner's taps", async () => {
    const id = "d7778e64-5206-45e5-9d04-b24174130ec3";
    const invoice = {
      id,
      number: "EFM-2026-002",
      supplier: "Ena Flour Mill Ltd",
      amount_cents: 124000,
      issue_date: "2026-11-28",
      due_date: "2026-12-27",
      status: "WaitingApproval",
    };
    const fetcher = answering({
      ...ROUTES,
      "/jinius/invoices": () => Response.json({ source: "live", note: "live", invoices: [invoice] }),
      [`/jinius/invoices/${id}/approve`]: () => Response.json({ ...invoice, status: "Approved" }),
      [`/jinius/invoices/${id}/mark-paid`]: () => Response.json({ ...invoice, status: "PendingPaymentAcceptance" }),
    });
    openDashboard(fetcher);
    fireEvent.click(screen.getByRole("tab", { name: "Jinius" }));
    fireEvent.click(screen.getByRole("button", { name: "Read invoices from Jinius" }));

    fireEvent.click(await screen.findByRole("button", { name: "Approve on Jinius" }));
    fireEvent.click(await screen.findByRole("button", { name: "Mark paid on Jinius" }));
    const card = screen.getByRole("article", { name: "Invoice EFM-2026-002" });
    await waitFor(() => expect(card.textContent).toContain("PendingPaymentAcceptance"));
    expect(within(card).queryByRole("button")).toBeNull();
    expect(fetcher).toHaveBeenCalledWith(`/jinius/invoices/${id}/approve`, { method: "POST" });
    expect(fetcher).toHaveBeenCalledWith(`/jinius/invoices/${id}/mark-paid`, { method: "POST" });
  });

  it("names Jinius's refusal on the invoice and keeps its button", async () => {
    const id = "d7778e64-5206-45e5-9d04-b24174130ec3";
    const invoice = {
      id,
      number: "EFM-2026-002",
      supplier: "Ena Flour Mill Ltd",
      amount_cents: 124000,
      issue_date: "2026-11-28",
      due_date: null,
      status: "Approved",
    };
    openDashboard(
      answering({
        ...ROUTES,
        "/jinius/invoices": () => Response.json({ source: "live", note: "live", invoices: [invoice] }),
        [`/jinius/invoices/${id}/mark-paid`]: () =>
          Response.json({ detail: "EFM-2026-002 is WaitingApproval: approve it first" }, { status: 409 }),
      }),
    );
    fireEvent.click(screen.getByRole("tab", { name: "Jinius" }));
    fireEvent.click(screen.getByRole("button", { name: "Read invoices from Jinius" }));
    fireEvent.click(await screen.findByRole("button", { name: "Mark paid on Jinius" }));

    expect((await screen.findByRole("alert")).textContent).toBe("EFM-2026-002 is WaitingApproval: approve it first");
    expect(screen.getByRole("button", { name: "Mark paid on Jinius" })).toBeTruthy();
  });

  it("offers no tap on saved data", async () => {
    const invoice = {
      id: "d7778e64-5206-45e5-9d04-b24174130ec3",
      number: "EFM-2026-002",
      supplier: "Ena Flour Mill Ltd",
      amount_cents: 124000,
      issue_date: "2026-11-28",
      due_date: null,
      status: "WaitingApproval",
    };
    openDashboard(
      answering({
        ...ROUTES,
        "/jinius/invoices": () =>
          Response.json({ source: "fixture", note: "Jinius sandbox offline", invoices: [invoice] }),
      }),
    );
    fireEvent.click(screen.getByRole("tab", { name: "Jinius" }));
    fireEvent.click(screen.getByRole("button", { name: "Read invoices from Jinius" }));
    const card = await screen.findByRole("article", { name: "Invoice EFM-2026-002" });
    expect(within(card).queryByRole("button")).toBeNull();
  });
});
