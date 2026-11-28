import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { CREDIT, FORECAST, OVERVIEW, RECEIPT, RECEIPTS } from "./fixtures";

afterEach(cleanup);

function answering(routes: Record<string, () => Response>) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const route = routes[String(input)];
    if (route === undefined) throw new Error(`unexpected request ${String(input)}`);
    return route();
  });
}

const ROUTES = {
  "/dashboard/overview": () => Response.json(OVERVIEW),
  "/dashboard/receipts": () => Response.json(RECEIPTS),
  "/dashboard/forecast": () => Response.json(FORECAST.card),
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
    expect(await screen.findByText("€2,669.19")).toBeTruthy();
    expect(screen.getByText("€349.69")).toBeTruthy();
    expect(screen.getByText("lean week of 16 Nov")).toBeTruthy();
    expect(screen.getByText("paid on its own · €180.00")).toBeTruthy();
    expect(screen.getByText("times you stepped in")).toBeTruthy();
    const pending = screen.getByRole("region", { name: "Needs your OK" });
    const approval = within(pending).getByRole("article", { name: "Approval" });
    expect(within(approval).getByRole("button", { name: "Approve" })).toBeTruthy();
  });

  it("approves from the dashboard through the same route as the chat", async () => {
    const fetcher = answering({
      ...ROUTES,
      "/approvals/approval-syn-inv-0002/approve": () => Response.json(RECEIPT),
    });
    openDashboard(fetcher);
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("article", { name: "Receipt" })).toBeTruthy();
    expect(fetcher).toHaveBeenCalledWith("/approvals/approval-syn-inv-0002/approve", { method: "POST" });
  });

  it("lists the receipts and shows the forecast", async () => {
    openDashboard(answering(ROUTES));
    fireEvent.click(screen.getByRole("tab", { name: "Receipts" }));
    const receipt = await screen.findByRole("article", { name: "Receipt" });
    expect(within(receipt).getByText("simulated payment")).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: "Forecast" }));
    const forecast = await screen.findByRole("article", { name: "Forecast" });
    expect(within(forecast).getByText(/16 Nov · lean/)).toBeTruthy();
  });

  it("says whether the lean week needs a loan, with the reason in the API's numbers", async () => {
    openDashboard(answering(ROUTES));
    fireEvent.click(screen.getByRole("tab", { name: "Loans" }));
    const verdict = await screen.findByRole("article", { name: "Do I need a loan?" });
    expect(within(verdict).getByText("A loan may be needed")).toBeTruthy();
    expect(verdict.textContent).toMatch(
      /week of 16 Nov closes at €349\.69, €600\.31 below your €950\.00 safe line/,
    );
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
    expect(screen.queryByRole("article", { name: "Offer" })).toBeNull();
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
});
