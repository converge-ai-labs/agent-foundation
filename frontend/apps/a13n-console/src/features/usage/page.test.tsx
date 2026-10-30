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

const http = vi.hoisted(() => ({ GET: vi.fn(), admin: false }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_usage" },
    can: (verb: string) => verb === "admin" && http.admin,
  }),
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
  http.admin = false;
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
  Element.prototype.scrollIntoView = vi.fn();
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
      : path === "/api/v1/models"
        ? {
            items: [
              {
                key: "anthropic-claude-opus-5.5",
                name: "NexusHub Claude Opus 5.5",
                config: { model_name: "claude-opus-5.5" },
                catalog_ref: {
                  provider: "anthropic",
                  model: "claude-opus-5.5",
                },
              },
            ],
            next_cursor: null,
          }
        : {
            items: path.endsWith("members")
              ? [
                  {
                    principal_id: "usr_alice",
                    name: "Alice",
                    kind: "user",
                    status: "active",
                    usage: metrics,
                    runs: overview.runs,
                  },
                  {
                    principal_id: "sa_bot",
                    name: "Shared bot",
                    kind: "service_account",
                    status: "disabled",
                    usage: metrics,
                    runs: overview.runs,
                  },
                ]
              : path.endsWith("models")
                ? [
                    {
                      model: "anthropic-claude-opus-5.5",
                      name: "NexusHub Claude Opus 5.5",
                      usage: metrics,
                    },
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
  const table = screen.getByRole("table", { name: "Usage breakdown" });
  await waitFor(() =>
    expect(table.querySelector("img")?.getAttribute("src")).toContain(
      "claude-color.svg",
    ),
  );
  expect(table.querySelectorAll("img")).toHaveLength(1);
  expect(
    screen.queryByRole("columnheader", { name: "Avg. run time" }),
  ).toBeNull();
  expect(
    http.GET.mock.calls
      .filter(([path]) => path === "/api/v1/usage/models")
      .at(-1)?.[1].params.query.cursor,
  ).toBeUndefined();
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

it("keeps member analytics out of the non-admin view", async () => {
  setup();
  mount();
  await screen.findByRole("table", { name: "Usage breakdown" });
  expect(screen.queryByRole("button", { name: "By member" })).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Member: All members" }),
  ).toBeNull();
  expect(http.GET.mock.calls.some(([path]) => path.endsWith("members"))).toBe(
    false,
  );
});

it("lets an admin inspect a member across overview, model and agent usage and return to all members", async () => {
  http.admin = true;
  setup();
  mount();
  const user = userEvent.setup();
  await screen.findByRole("table", { name: "Usage breakdown" });
  await user.click(screen.getByRole("button", { name: "By member" }));
  await screen.findByText("Alice");
  expect(screen.getByText("User")).toBeTruthy();
  expect(screen.getByText("Service account · Disabled")).toBeTruthy();
  await user.click(
    within(screen.getByRole("row", { name: /Shared bot/ })).getByRole(
      "button",
      { name: "View usage" },
    ),
  );
  expect(screen.getByText("Viewing usage for")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "By member" })).toBeNull();
  expect(
    within(
      screen
        .getAllByRole("status")
        .find((node) => node.textContent?.includes("Viewing usage for"))!,
    ).getByText("Shared bot"),
  ).toBeTruthy();
  await waitFor(() => {
    for (const endpoint of ["overview", "agents"]) {
      expect(
        http.GET.mock.calls
          .filter(([path]) => path === `/api/v1/usage/${endpoint}`)
          .at(-1)?.[1].params.query.principal_id,
      ).toBe("sa_bot");
    }
  });
  await user.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByText("Second page");
  await user.click(screen.getByRole("button", { name: "By model" }));
  await screen.findByText("Unpriced model");
  const modelQuery = http.GET.mock.calls
    .filter(([path]) => path === "/api/v1/usage/models")
    .at(-1)?.[1].params.query;
  expect(modelQuery.principal_id).toBe("sa_bot");
  expect(modelQuery.cursor).toBeUndefined();
  await user.click(screen.getByRole("button", { name: "Refresh" }));
  await waitFor(() =>
    expect(
      http.GET.mock.calls
        .filter(([path]) => path === "/api/v1/usage/overview")
        .at(-1)?.[1].params.query.principal_id,
    ).toBe("sa_bot"),
  );
  await user.click(screen.getByRole("button", { name: "Clear member filter" }));
  expect(screen.queryByText("Viewing usage for")).toBeNull();
  await user.click(screen.getByRole("button", { name: "By member" }));
  await screen.findByText("Alice");
  await waitFor(() =>
    expect(
      http.GET.mock.calls
        .filter(([path]) => path === "/api/v1/usage/overview")
        .at(-1)?.[1].params.query.principal_id,
    ).toBeUndefined(),
  );
  expect(
    screen.queryByRole("button", { name: "Clear member filter" }),
  ).toBeNull();
});

it("filters the whole page from the top member picker and switches or clears identities", async () => {
  http.admin = true;
  setup();
  mount();
  const user = userEvent.setup();
  await screen.findByText("Support assistant");
  await user.click(screen.getByRole("button", { name: "By model" }));
  await screen.findByText("Unpriced model");
  await user.click(screen.getByRole("button", { name: "Member: All members" }));
  await user.click(await screen.findByRole("button", { name: "Alice · User" }));
  await waitFor(() => {
    for (const endpoint of ["overview", "models"]) {
      expect(
        http.GET.mock.calls
          .filter(([path]) => path === `/api/v1/usage/${endpoint}`)
          .at(-1)?.[1].params.query.principal_id,
      ).toBe("usr_alice");
    }
  });
  expect(screen.getByText("Viewing usage for")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "By member" })).toBeNull();
  expect(screen.getByRole("button", { name: "Member: Alice" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Member: Alice" }));
  await user.click(
    await screen.findByRole("button", {
      name: "Shared bot · Service account · Disabled",
    }),
  );
  await waitFor(() =>
    expect(
      http.GET.mock.calls
        .filter(([path]) => path === "/api/v1/usage/overview")
        .at(-1)?.[1].params.query.principal_id,
    ).toBe("sa_bot"),
  );
  await user.click(screen.getByRole("button", { name: "Member: Shared bot" }));
  await user.click(screen.getByRole("button", { name: "All members" }));
  await waitFor(() =>
    expect(
      http.GET.mock.calls
        .filter(([path]) => path === "/api/v1/usage/overview")
        .at(-1)?.[1].params.query.principal_id,
    ).toBeUndefined(),
  );
  expect(screen.queryByText("Viewing usage for")).toBeNull();
  expect(
    screen
      .getByRole("button", { name: "By model" })
      .getAttribute("aria-pressed"),
  ).toBe("true");
});
