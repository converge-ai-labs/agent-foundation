// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
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
  queries.setQueryData(["thread", "one", "context-usage"], {
    thread_id: "one",
    context_window: 1000,
    latest_request_tokens: 100,
  });
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
  expect(screen.getByText("0s")).toBeTruthy();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });
  expect(screen.getByText("2s")).toBeTruthy();
  view.rerender(content(400, true));
  expect(screen.getByText("40%")).toBeTruthy();
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
