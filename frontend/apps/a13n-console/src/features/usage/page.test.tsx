import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { UsagePage } from "./page";
import type { Schema } from "../../shared/api";

const http = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_usage" } }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { count: number }) =>
      key.replace("{{count}}", String(options?.count ?? "")),
    i18n: { resolvedLanguage: "en" },
  }),
}));
const caches: QueryClient[] = [];
afterEach(() => {
  cleanup();
  caches.forEach((cache) => cache.clear());
  caches.length = 0;
  vi.resetAllMocks();
});
const metrics: Schema["ModelMetrics"] = {
  requests: 2,
  input_tokens: 1000,
  output_tokens: 100,
  cache_read_tokens: 500,
  cache_hit_rate: 0.5,
  cost: "0.012",
  unpriced_requests: 1,
};
const overview: Schema["UsageOverview"] = {
  usage: metrics,
  runs: { runs: 1, average_duration_seconds: 10 },
  daily: [{ date: "2026-09-28", usage: metrics }],
};
function mount() {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  caches.push(cache);
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <UsagePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
function setup() {
  http.GET.mockImplementation(async (path, options) => ({
    response: new Response(),
    data: path.endsWith("overview")
      ? overview
      : {
          items: path.endsWith("models")
            ? [
                {
                  model: "unknown",
                  name: "Unpriced model",
                  usage: { ...metrics, cost: null },
                },
              ]
            : [
                {
                  agent_id: "agent_one",
                  name: options.params.query.cursor
                    ? "Second page"
                    : "Support assistant",
                  usage: metrics,
                  runs: overview.runs,
                },
              ],
          next_cursor:
            path.endsWith("agents") && !options.params.query.cursor
              ? "cursor_next"
              : null,
        },
  }));
}
it("loads summaries, switches dimensions, pages, and keeps chart metric switches local", async () => {
  setup();
  mount();
  const user = userEvent.setup();
  expect(await screen.findByText("Support assistant")).toBeTruthy();
  expect(screen.getByText(/1 requests could not be priced\./)).toBeTruthy();
  const calls = http.GET.mock.calls.length;
  await user.click(
    within(screen.getByRole("group", { name: "Trend metric" })).getByRole(
      "button",
      { name: "Tokens" },
    ),
  );
  expect(http.GET.mock.calls).toHaveLength(calls);
  await user.click(screen.getByRole("button", { name: "Next" }));
  expect(await screen.findByText("Second page")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "By model" }));
  expect(await screen.findByText("Unpriced model")).toBeTruthy();
  expect(
    screen.queryByRole("columnheader", { name: "Avg. run time" }),
  ).toBeNull();
  expect(http.GET.mock.calls.at(-1)?.[1].params.query.cursor).toBeUndefined();
});
it("recovers from a query failure and displays empty usage", async () => {
  http.GET.mockRejectedValue(new Error("Usage unavailable"));
  mount();
  expect(
    (await screen.findAllByText("Usage unavailable")).length,
  ).toBeGreaterThan(0);
  http.GET.mockResolvedValue({
    response: new Response(),
    data: { items: [], next_cursor: null },
  });
  http.GET.mockImplementation(async (path) => ({
    response: new Response(),
    data: path.endsWith("overview")
      ? {
          ...overview,
          usage: { ...metrics, requests: 0, cost: "0", unpriced_requests: 0 },
          daily: [],
        }
      : { items: [], next_cursor: null },
  }));
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: "Refresh" }));
  expect(await screen.findByText("No usage in this period")).toBeTruthy();
  await waitFor(() =>
    expect(screen.queryByText("Usage unavailable")).toBeNull(),
  );
});
