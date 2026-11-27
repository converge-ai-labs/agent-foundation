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
import { GuestAccessContext, TransportContext } from "../transport/context";
import type { Schema, Transport } from "../transport/client";
import { DebugProvider } from "../shell/debug";
import { ComposerStatus } from "./composer-status";

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.useRealTimers();
});
it("ticks the exact current receipt and paints live context and speed without waiting for a saved transcript", async () => {
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
        ["cache_read_tokens", 5000],
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
  expect(
    screen.getByRole("button", { name: "Context details" }).title,
  ).toContain("25%");
  expect(screen.queryByText("(25%)")).toBeNull();
  expect(screen.queryByRole("button", { name: "Tokens details" })).toBeNull();
  expect(
    screen.getByRole("button", { name: "Cost details" }).textContent,
  ).toContain("0.025");
  expect(screen.queryByText("Speed")).toBeNull();
  expect(screen.getByText("0.25k")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Cache details" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Context details" }));
  expect(screen.getByRole("heading", { name: "Context usage" })).toBeTruthy();
  expect(screen.getByText("Cache rate")).toBeTruthy();
  expect(screen.getByText("40.5%")).toBeTruthy();
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });
  fireEvent.keyDown(window, { code: "KeyD", ctrlKey: true, altKey: true });
  expect(
    screen.getByRole("button", { name: "Tokens details" }).textContent,
  ).toBe("Tokens 12.3K");
  expect(screen.getByText("0s")).toBeTruthy();
  expect(screen.getByText("Speed").textContent).toBe("Speed —");
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
  expect(screen.getByText("Speed").textContent).toBe("Speed Standard");
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
          ["cache_read_tokens", 6000],
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
  expect(screen.getByText("11,000", { selector: "strong" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Context details" }));
  expect(screen.getByRole("heading", { name: "Context usage" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Chat usage" })).toBeNull();
  expect(screen.getByText("400")).toBeTruthy();
  expect(screen.getByText("1,000")).toBeTruthy();
  expect(screen.getByText("600")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Cost details" }));
  expect(screen.getByRole("heading", { name: "Chat usage" })).toBeTruthy();
  expect(screen.getByText("13,345")).toBeTruthy();
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });
  expect(screen.getByText("Speed").textContent).toBe("Speed Standard");
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
  fireEvent.keyDown(window, { code: "KeyD", ctrlKey: true, altKey: true });
  expect(screen.queryByRole("button", { name: "Tokens details" })).toBeNull();
  expect(screen.queryByText("Time")).toBeNull();
  expect(screen.queryByText("Speed")).toBeNull();
  expect(screen.getByText("0.4k")).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Context details" }).title,
  ).toContain("40%");
  expect(screen.queryByText("(40%)")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Context details" }));
  expect(screen.getByText("45.0%")).toBeTruthy();
  view.unmount();
  queries.clear();
});

it("handles external ACP tri-state context occupancy, honest hiding, and accurate HelpTip copy", () => {
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  queries.setQueryData(["thread", "acp-one", "context-usage"], {
    thread_id: "acp-one",
    context_window: 1000,
    latest_request_tokens: 500,
  });
  queries.setQueryData(["thread", "acp-one", "usage"], {
    first_observed_at: "2026-01-01T00:00:00Z",
    root: { model_requests: 1, tokens: [] },
    models: [],
    other_models: { model_requests: 0 },
    combined: {
      provider_receipts: 0,
      model_requests: 1,
      unknown_model_costs: 0,
      model_cost_usd: "0.01",
      tokens: [["input_tokens", 500]],
    },
  });
  queries.setQueryData(["thread", "acp-one", "configuration"], {
    capture_source: "selected_continuation",
  });
  const transport = {
    client: {
      GET: vi.fn(async () => ({ data: {} })),
    },
  } as unknown as Transport;
  const renderStatus = (acpContext?: { used: number; size: number } | null) => (
    <QueryClientProvider client={queries}>
      <TransportContext value={transport}>
        <DebugProvider>
          <ComposerStatus threadId="acp-one" external acpContext={acpContext} />
        </DebugProvider>
      </TransportContext>
    </QueryClientProvider>
  );

  // undefined falls back to persisted context.data and shows percentage for external ACP.
  const view = render(renderStatus(undefined));
  expect(
    screen.getByRole("button", { name: "Context details" }).textContent,
  ).toContain("50%");

  // null explicitly clears stale persisted context.data -> hidden in non-debug mode.
  view.rerender(renderStatus(null));
  expect(screen.queryByRole("button", { name: "Context details" })).toBeNull();

  // In debug mode, Context metric stays visible for diagnostics with ACP unavailable HelpTip.
  fireEvent.keyDown(window, { code: "KeyD", ctrlKey: true, altKey: true });
  const debugTrigger = screen.getByRole("button", { name: "Context details" });
  expect(debugTrigger.textContent).toContain("—");
  fireEvent.click(debugTrigger);
  fireEvent.click(screen.getByRole("button", { name: "About context" }));
  expect(
    screen.getByText(
      /Context occupancy reported by the external ACP Agent\..*has not reported its current context window/,
    ),
  ).toBeTruthy();

  // Live acpContext overrides even when persisted context.data lacks context_window,
  // and HelpTip does not falsely claim the window is missing.
  act(() =>
    queries.setQueryData(["thread", "acp-one", "context-usage"], {
      thread_id: "acp-one",
      context_window: null,
      latest_request_tokens: null,
    }),
  );
  view.rerender(renderStatus({ used: 250, size: 1000 }));
  expect(
    screen.getByRole("button", { name: "Context details" }).textContent,
  ).toContain("25%");
  expect(
    screen.queryByText(/has not reported its current context window/),
  ).toBeNull();
  expect(
    screen.getByText(/Context occupancy reported by the external ACP Agent\./),
  ).toBeTruthy();

  view.unmount();
  queries.clear();
});

it("renders context and cost for guests without requesting owner configuration or activity routes", () => {
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  queries.setQueryData(["thread", "shared-one", "context-usage"], {
    thread_id: "shared-one",
    context_window: 200000,
    latest_request_tokens: 18500,
  });
  queries.setQueryData(["thread", "shared-one", "usage"], {
    first_observed_at: "2026-01-01T00:00:00Z",
    root: { model_requests: 1, tokens: [] },
    models: [],
    other_models: { model_requests: 0 },
    combined: {
      provider_receipts: 0,
      model_requests: 1,
      unknown_model_costs: 0,
      model_cost_usd: "0.042",
      tokens: [
        ["input_tokens", 18500],
        ["output_tokens", 1200],
      ],
    },
  });
  const get = vi.fn(async (path: string) => ({
    data: path.includes("context-usage")
      ? {
          thread_id: "shared-one",
          context_window: 200000,
          latest_request_tokens: 18500,
        }
      : { root: { tokens: [] } },
  }));
  const transport = {
    shareId: "share-one",
    client: { GET: get },
  } as unknown as Transport;
  const guest: Schema<"ShareAccess"> = {
    title: "Shared chat",
    grant: {
      share_id: "share-one",
      scope: "thread",
      target_id: "shared-one",
      permission: "view",
      created_at: "2026-01-01T00:00:00Z",
      expires_at: "2026-01-02T00:00:00Z",
    },
  };
  const view = render(
    <QueryClientProvider client={queries}>
      <TransportContext value={transport}>
        <GuestAccessContext value={guest}>
          <DebugProvider>
            <ComposerStatus threadId="shared-one" />
          </DebugProvider>
        </GuestAccessContext>
      </TransportContext>
    </QueryClientProvider>,
  );

  expect(screen.getByText("18.5k")).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Cost details" }).textContent,
  ).toContain("0.042");
  fireEvent.click(screen.getByRole("button", { name: "Context details" }));
  fireEvent.click(screen.getByRole("button", { name: "Refresh usage" }));
  const requested = get.mock.calls.map(([path]) => path);
  expect(requested).not.toContain("/api/threads/{thread_id}/configuration");
  expect(requested).not.toContain("/api/threads/activity");
  expect(requested).toContain("/api/threads/{thread_id}/usage");
  expect(requested).toContain("/api/threads/{thread_id}/context-usage");

  view.unmount();
  queries.clear();
});
