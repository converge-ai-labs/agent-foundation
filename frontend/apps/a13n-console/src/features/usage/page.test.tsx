import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Link, MemoryRouter, Route, Routes, useNavigate } from "react-router";
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
      <MemoryRouter initialEntries={["/workspace/ws_usage/usage"]}>
        <HistoryForward />
        <Routes>
          <Route path="/workspace/:workspaceId/usage" element={<UsagePage />} />
          <Route
            path="/workspace/:workspaceId/sessions/:sessionId"
            element={<SessionDestination />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
function HistoryForward() {
  const navigate = useNavigate();
  return <button onClick={() => void navigate(1)}>Browser Forward</button>;
}
function SessionDestination() {
  const navigate = useNavigate();
  return (
    <>
      <h1>Session Debug</h1>
      <button onClick={() => void navigate(-1)}>Browser Back</button>
      <Link to="/workspace/ws_usage/usage">Fresh usage visit</Link>
    </>
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
it("loads summaries, switches dimensions, pages, and requests ranked series for chart metrics", async () => {
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
  await waitFor(() => expect(http.GET.mock.calls).toHaveLength(calls + 1));
  expect(http.GET.mock.calls.at(-1)?.[1].params.query).toMatchObject({
    group_by: "agents",
    metric: "tokens",
  });
  await user.click(screen.getByRole("button", { name: "Next" }));
  expect(await screen.findByText("Second page")).toBeTruthy();
  await user.click(
    within(screen.getByRole("group", { name: "Group usage by" })).getByRole(
      "button",
      { name: "By model" },
    ),
  );
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

function setupSessions() {
  setup();
  const original = http.GET.getMockImplementation()!;
  http.GET.mockImplementation(async (path, options) => {
    if (path !== "/api/v1/usage/sessions") return original(path, options);
    const next = !!options.params.query.cursor;
    return {
      response: new Response(),
      data: {
        items: [
          {
            session_id: next ? "sess_second" : "sess_first",
            title: next ? null : "Expensive session",
            usage: { ...metrics, cost: next ? null : "9.5" },
            runs: overview.runs,
          },
        ],
        next_cursor: next ? null : "sessions_next",
      },
    };
  });
}

it("opens a session usage sheet and preserves the agent list, member scope and keyboard focus", async () => {
  http.admin = true;
  setupSessions();
  mount();
  const user = userEvent.setup();
  await screen.findByRole("button", { name: "Support assistant" });
  expect(http.GET.mock.calls.some(([path]) => path.endsWith("sessions"))).toBe(
    false,
  );
  await user.click(screen.getByRole("button", { name: "Member: All members" }));
  await user.click(await screen.findByRole("button", { name: "Alice · User" }));
  await user.click(screen.getByRole("button", { name: "Next" }));
  const agentButton = await screen.findByRole("button", {
    name: "Second page",
  });
  const agentTable = screen.getByRole("table", { name: "Usage breakdown" });
  await user.click(agentButton);
  const dialog = await screen.findByRole("dialog", { name: "Session usage" });
  expect(within(dialog).getByText("Second page")).toBeTruthy();
  expect(within(dialog).getByText(/Alice/)).toBeTruthy();
  expect(document.body.contains(agentTable)).toBe(true);
  const table = await within(dialog).findByRole("table", {
    name: "Session usage",
  });
  expect(
    within(table)
      .getByRole("columnheader", { name: "Spend" })
      .getAttribute("aria-sort"),
  ).toBe("descending");
  expect(
    within(dialog)
      .getByRole("link", { name: "Expensive session" })
      .getAttribute("href"),
  ).toBe("/workspace/ws_usage/sessions/sess_first?view=debug");
  const queries = () =>
    http.GET.mock.calls
      .filter(([path]) => path.endsWith("sessions"))
      .map(([, options]) => options.params.query);
  expect(queries().at(-1)).toMatchObject({
    agent_id: "agent_one",
    principal_id: "usr_alice",
    limit: 50,
  });
  await user.click(within(dialog).getByRole("button", { name: "Next" }));
  await within(dialog).findByRole("link", { name: "Untitled session" });
  expect(queries().at(-1).cursor).toBe("sessions_next");
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(screen.getByRole("table", { name: "Usage breakdown" })).toBe(
    agentTable,
  );
  await waitFor(() => expect(document.activeElement).toBe(agentButton));
  expect(screen.getByRole("button", { name: "Second page" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Refresh" }));
  await screen.findByRole("button", { name: "Support assistant" });
  await user.click(screen.getByRole("button", { name: "Clear member filter" }));
  const row = await screen.findByRole("row", { name: /Support assistant/ });
  row.focus();
  await user.keyboard("{Enter}");
  const reopened = await screen.findByRole("dialog", { name: "Session usage" });
  await within(reopened).findByRole("link", { name: "Expensive session" });
  expect(queries().at(-1).principal_id).toBeUndefined();
  expect(queries().at(-1).cursor).toBeUndefined();
  await user.click(within(reopened).getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await user.click(
    within(screen.getByRole("group", { name: "Group usage by" })).getByRole(
      "button",
      { name: "By model" },
    ),
  );
  await screen.findByText("Unpriced model");
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
  await user.click(
    within(screen.getByRole("group", { name: "Group usage by" })).getByRole(
      "button",
      { name: "By model" },
    ),
  );
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
  await user.click(
    within(screen.getByRole("group", { name: "Group usage by" })).getByRole(
      "button",
      { name: "By model" },
    ),
  );
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
    within(screen.getByRole("group", { name: "Group usage by" }))
      .getByRole("button", { name: "By model" })
      .getAttribute("aria-pressed"),
  ).toBe("true");
});

it.each(["link", "row"])(
  "restores the usage sheet and both pages after Session navigation via %s",
  async (activation) => {
    http.admin = true;
    setupSessions();
    mount();
    const user = userEvent.setup();
    await screen.findByText("Support assistant");
    await user.click(
      screen.getByRole("button", { name: "Time range: Last 7 days" }),
    );
    await user.click(screen.getByRole("button", { name: "Last 30 days" }));
    await user.click(screen.getByRole("button", { name: "Tokens" }));
    await user.click(
      screen.getByRole("button", { name: "Member: All members" }),
    );
    await user.click(
      await screen.findByRole("button", { name: "Alice · User" }),
    );
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(
      await screen.findByRole("button", { name: "Second page" }),
    );
    const sheet = await screen.findByRole("dialog", { name: "Session usage" });
    await within(sheet).findByRole("link", { name: "Expensive session" });
    await user.click(within(sheet).getByRole("button", { name: "Next" }));
    await within(sheet).findByRole("link", { name: "Untitled session" });
    const queries = () =>
      http.GET.mock.calls
        .filter(([path]) => path.endsWith("sessions"))
        .map(([, options]) => options.params.query);
    const before = queries().at(-1);
    if (activation === "link") {
      await user.click(
        within(sheet).getByRole("link", { name: "Untitled session" }),
      );
    } else {
      within(sheet)
        .getByRole("row", { name: /Untitled session/ })
        .focus();
      await user.keyboard("{Enter}");
    }
    await screen.findByRole("heading", { name: "Session Debug" });
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Browser Back" }));
    const restored = await screen.findByRole("dialog", {
      name: "Session usage",
    });
    await within(restored).findByRole("link", { name: "Untitled session" });
    expect(within(restored).getByText("Second page")).toBeTruthy();
    expect(within(restored).getByText(/Alice/)).toBeTruthy();
    expect(queries().at(-1)).toEqual(before);
    await user.click(
      within(restored).getByRole("button", { name: "Previous" }),
    );
    await within(restored).findByRole("link", { name: "Expensive session" });
    await user.click(within(restored).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByRole("button", { name: "Second page" })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Time range: Last 30 days" }),
    ).toBeTruthy();
    expect(
      screen
        .getByRole("button", { name: "Tokens" })
        .getAttribute("aria-pressed"),
    ).toBe("true");
    await user.click(screen.getByRole("button", { name: "Browser Forward" }));
    await user.click(
      await screen.findByRole("button", { name: "Browser Back" }),
    );
    await screen.findByRole("button", { name: "Second page" });
    expect(screen.queryByRole("dialog")).toBeNull();
    // Explicitly reopening resets only the Session page; a new visit has no stale sheet.
    await user.click(screen.getByRole("button", { name: "Second page" }));
    await user.click(
      await screen.findByRole("link", { name: "Expensive session" }),
    );
    await user.click(
      await screen.findByRole("link", { name: "Fresh usage visit" }),
    );
    await screen.findByRole("button", { name: "Support assistant" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Member: All members" }),
    ).toBeTruthy();
  },
);

it("drops restored member filters and their cursors when admin access is lost", async () => {
  http.admin = true;
  setupSessions();
  mount();
  const user = userEvent.setup();
  await screen.findByText("Support assistant");
  await user.click(screen.getByRole("button", { name: "Member: All members" }));
  await user.click(await screen.findByRole("button", { name: "Alice · User" }));
  await user.click(
    await screen.findByRole("button", { name: "Support assistant" }),
  );
  const sheet = await screen.findByRole("dialog", { name: "Session usage" });
  await within(sheet).findByRole("link", { name: "Expensive session" });
  await user.click(within(sheet).getByRole("button", { name: "Next" }));
  await user.click(
    await within(sheet).findByRole("link", { name: "Untitled session" }),
  );
  await screen.findByRole("heading", { name: "Session Debug" });
  http.admin = false;
  http.GET.mockClear();
  await user.click(screen.getByRole("button", { name: "Browser Back" }));
  const restored = await screen.findByRole("dialog", { name: "Session usage" });
  await within(restored).findByRole("link", { name: "Expensive session" });
  expect(within(restored).queryByText(/Alice/)).toBeNull();
  const queries = http.GET.mock.calls.filter(([path]) =>
    path.startsWith("/api/v1/usage/"),
  );
  expect(queries.some(([path]) => path.endsWith("sessions"))).toBe(true);
  for (const [path, options] of queries) {
    expect(path.endsWith("members")).toBe(false);
    expect(options.params.query.principal_id).toBeUndefined();
    expect(options.params.query.cursor).toBeUndefined();
  }
});
