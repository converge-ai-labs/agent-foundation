// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TransportContext } from "../transport/context";
import type { Schema, Transport } from "../transport/client";
import { DebugProvider } from "../shell/debug";
import { ComposerStatus } from "./composer-status";

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.useRealTimers();
});
it("ticks the exact receipt and refreshes paired context without mixing live tokens with a stale limit", async () => {
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
    descendants: { model_requests: 0, tokens: [] },
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
        ["cache_read_tokens", 5000],
      ],
    },
  };
  queries.setQueryData(usageKey, usage);
  let contextResponse = {
    thread_id: "one",
    context_window: 2000,
    latest_request_tokens: 250,
  };
  const transport = {
    client: {
      GET: vi.fn(async (path: string) => ({
        data: path.includes("operations")
          ? operation
          : path.includes("activity")
            ? { rows: [], next_cursor: null }
            : path.includes("context-usage")
              ? contextResponse
              : { root: { tokens: [] } },
      })),
    },
  } as unknown as Transport;
  const content = (liveTokens: number, busy: boolean) => (
    <QueryClientProvider client={queries}>
      <TransportContext value={transport}>
        <DebugProvider>
          <ComposerStatus
            threadId="one"
            receipt="current"
            busy={busy}
            liveTokens={liveTokens}
          />
        </DebugProvider>
      </TransportContext>
    </QueryClientProvider>
  );
  const view = render(content(250, true));
  const trigger = screen.getByRole("button", { name: "Context details" });
  expect(trigger.textContent).toBe("100 (10%)");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(trigger.textContent).toBe("250 (13%)");
  expect(screen.queryByText("0.025")).toBeNull();
  expect(screen.queryByText("Speed")).toBeNull();
  fireEvent.click(trigger);
  expect(screen.getByRole("heading", { name: "Context usage" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Chat usage" })).toBeTruthy();
  expect(screen.getByText("$0.0250")).toBeTruthy();
  expect(screen.getByText("12,345")).toBeTruthy();
  fireEvent.keyDown(window, { code: "KeyD", ctrlKey: true, altKey: true });
  expect(screen.getByText("0s")).toBeTruthy();
  act(() =>
    queries.setQueryData(configurationKey, {
      capture_source: "active_operation",
      receipt_id: "current",
      captured: { agent: { fast: "default" } },
    }),
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });
  expect(screen.getByText("2s")).toBeTruthy();
  expect(screen.getByText("Standard")).toBeTruthy();
  contextResponse = {
    thread_id: "one",
    context_window: 1000,
    latest_request_tokens: 400,
  };
  view.rerender(content(400, true));
  expect(trigger.textContent).toBe("250 (13%)");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(trigger.textContent).toBe("400 (40%)");
  expect(within(screen.getByRole("dialog")).getByText("400")).toBeTruthy();
  expect(screen.getByText("1,000")).toBeTruthy();
  act(() =>
    queries.setQueryData(["thread", "one", "context-usage"], {
      thread_id: "one",
      latest_request_tokens: 400,
      context_window: null,
    }),
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(trigger.textContent).toBe("400 (—)");
  act(() =>
    queries.setQueryData(["thread", "one", "context-usage"], {
      thread_id: "one",
      latest_request_tokens: 222950,
      context_window: 350000,
    }),
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(trigger.textContent).toBe("222.95k (64%)");
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
