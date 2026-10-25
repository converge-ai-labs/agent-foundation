// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  CopilotAllowance,
  GrokAllowance,
  ChatGPTAllowance,
} from "./provider-usage";
import { AccountUsage } from "./subscriptions";
import { createTransport, type Schema } from "../transport/client";
import { TransportContext } from "../transport/context";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows Codex windows, weekly reset, and expiring vouchers outside management", () => {
  const view = {
    account: { provider: "codex" },
    usage: {
      observed_at: 1900000000,
      usage: {
        account_id: "one",
        plan_type: "plus",
        rate_limit: {
          allowed: true,
          limit_reached: false,
          primary_window: {
            used_percent: 25,
            limit_window_seconds: 18000,
            reset_at: 1900000000,
          },
          secondary_window: {
            used_percent: 60,
            limit_window_seconds: 604800,
            reset_at: 1900600000,
          },
        },
        reset_credits: {
          available_count: 1,
          credits: [
            {
              id: "one",
              status: "available",
              reset_type: "codex_rate_limits",
              expires_at: "2099-01-01T00:00:00Z",
            },
          ],
        },
      },
    },
  } as Schema<"SubscriptionAccountView">;
  render(<AccountUsage view={view} />);
  expect(
    screen.getByRole("meter", { name: "5h remaining" }).getAttribute("value"),
  ).toBe("75");
  expect(
    screen
      .getByRole("meter", { name: "Weekly remaining" })
      .getAttribute("value"),
  ).toBe("40");
  expect(screen.getAllByText(/^Resets /)).toHaveLength(2);
  expect(screen.getByText("Reset vouchers · 1 available")).toBeTruthy();
  expect(screen.getByText(/^Expires /)).toBeTruthy();
});

it("renders Grok allowance and purchased dollars without Codex windows or vouchers", () => {
  render(
    <GrokAllowance
      usage={{
        provider: "grok",
        config: {
          credit_usage_percent: 99.994,
          current_period: {
            type: "USAGE_PERIOD_TYPE_WEEKLY",
            end: "2030-01-01T00:00:00Z",
          },
          prepaid_balance: { val: -1500 },
        },
      }}
    />,
  );
  expect(screen.getByText("99%")).toBeTruthy();
  expect(
    screen.getByRole("meter", { name: "Weekly used" }).getAttribute("value"),
  ).toBe("99.994");
  expect(
    screen.getByText("Purchased credits").parentElement?.textContent,
  ).toMatch(/15.00/);
  expect(screen.queryByText(/voucher/i)).toBeNull();
});

it("refreshes Copilot independently and retains its last quota after a failed refresh", async () => {
  let calls = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      expect(new URL(request.url).pathname).toBe(
        "/api/subscriptions/copilot/usage",
      );
      calls++;
      return new Response(
        JSON.stringify(
          calls === 1
            ? {
                quota_snapshots: {
                  chat: { unlimited: true },
                  premium_models: {
                    percent_remaining: 35,
                    overage_permitted: false,
                  },
                },
                quota_reset_date: "2030-02-01T00:00:00Z",
              }
            : { detail: "Provider unavailable" },
        ),
        {
          status: calls === 1 ? 200 : 503,
          headers: { "Content-Type": "application/json" },
        },
      );
    }),
  );
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <TransportContext value={createTransport("", () => {})}>
        <CopilotAllowance identity="native:user" ready />
      </TransportContext>
    </QueryClientProvider>,
  );
  await screen.findByText("Premium models · 35% left");
  expect(screen.getByText("Chat · Unlimited")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Refresh allowance" }));
  await screen.findByRole("alert");
  expect(calls).toBe(2);
  expect(screen.getByText("Premium models · 35% left")).toBeTruthy();
});

it("shows ChatGPT's own settings without fetching Codex or fabricating quota", () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  render(<ChatGPTAllowance />);
  expect(screen.getByRole("link").getAttribute("href")).toBe(
    "https://chatgpt.com/settings/usage",
  );
  expect(screen.getByText(/Numeric allowance is unavailable/)).toBeTruthy();
  expect(fetch).not.toHaveBeenCalled();
});
