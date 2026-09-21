// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TransportContext } from "../transport/context";
import type { Schema, Transport } from "../transport/client";
import { ComposerStatus } from "./composer-status";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
it("ticks the exact current receipt and paints live context without waiting for a saved transcript", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  const operation: Schema<"RootOperationView"> = {
    receipt: {
      receipt_id: "current",
      thread_id: "one",
      submitted_at: "2026-01-01T00:00:00Z",
    },
    status: "preparing",
  };
  const key = ["thread", "one", "operation", "current"];
  queries.setQueryData(key, operation);
  const configurationKey = ["thread", "one", "configuration"];
  queries.setQueryData(configurationKey, {
    capture_source: "selected_continuation",
    receipt_id: "old",
    captured: { agent: { fast: "on" } },
  });
  queries.setQueryData(["thread", "one", "context-usage"], {
    thread_id: "one",
    context_window: 1000,
    latest_request_tokens: 100,
  });
  const usageKey = ["thread", "one", "usage"];
  const usage = {
    first_observed_at: "2026-01-01T00:00:00Z",
    root: { model_requests: 0, tokens: [] },
    models: [],
    other_models: { model_requests: 0 },
    combined: {
      provider_receipts: 0,
      model_requests: 2,
      unknown_model_costs: 0,
      model_cost_usd: "0.025",
      tokens: [
        ["input_tokens", 10000],
        ["output_tokens", 2345],
      ],
    },
  };
  queries.setQueryData(usageKey, usage);
  const transport = {
    client: {
      GET: vi.fn(async (path: string) => ({
        data: path.includes("operations")
          ? operation
          : path.includes("activity")
            ? { rows: [], next_cursor: null }
            : path.includes("context-usage")
              ? {
                  thread_id: "one",
                  context_window: 1000,
                  latest_request_tokens: 100,
                }
              : { root: { tokens: [] } },
      })),
    },
  } as unknown as Transport;
  const content = (liveTokens: number, busy: boolean) => (
    <QueryClientProvider client={queries}>
      <TransportContext value={transport}>
        <ComposerStatus
          threadId="one"
          receipt="current"
          busy={busy}
          liveTokens={liveTokens}
        />
      </TransportContext>
    </QueryClientProvider>
  );
  const view = render(content(250, true));
  expect(screen.getByText("25%")).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Tokens details" }).textContent,
  ).toBe("Tokens 12.3K");
  expect(screen.getByText("0s")).toBeTruthy();
  expect(screen.queryByText("On")).toBeNull();
  act(() =>
    queries.setQueryData(configurationKey, {
      capture_source: "active_operation",
      receipt_id: "current",
      captured: { agent: { fast: "off" } },
    }),
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });
  expect(screen.getByText("2s")).toBeTruthy();
  expect(screen.getByText("Off")).toBeTruthy();
  view.rerender(content(400, true));
  expect(screen.getByText("40%")).toBeTruthy();
  expect(screen.getByText("12.3K")).toBeTruthy();
  act(() =>
    queries.setQueryData(usageKey, {
      ...usage,
      combined: {
        ...usage.combined,
        tokens: [
          ["input_tokens", 11000],
          ["output_tokens", 2345],
        ],
      },
    }),
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(screen.getByText("13.3K")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Tokens details" }));
  expect(screen.getByText("13,345")).toBeTruthy();
  expect(screen.getByText("11,000")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Context details" }));
  expect(screen.getByRole("heading", { name: "Context usage" })).toBeTruthy();
  expect(
    screen.queryByRole("heading", { name: "Conversation usage" }),
  ).toBeNull();
  expect(screen.getByText("400")).toBeTruthy();
  expect(screen.getByText("1,000")).toBeTruthy();
  expect(screen.getByText("600")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Cache details" }));
  expect(
    screen.getByRole("heading", { name: "Conversation usage" }),
  ).toBeTruthy();
  expect(screen.getByText("13,345")).toBeTruthy();
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });
  expect(screen.getByText("Off")).toBeTruthy();
  operation.status = "completed";
  operation.completed_at = "2026-01-01T00:00:02Z";
  act(() => {
    queries.setQueryData(key, { ...operation });
  });
  view.rerender(content(400, false));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(screen.getByText("2s")).toBeTruthy();
  view.unmount();
  queries.clear();
});
