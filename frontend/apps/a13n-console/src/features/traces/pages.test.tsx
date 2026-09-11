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
import { ApiError } from "@converge.ai/a13n";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { TraceDetail } from "./detail";
import { TracesPage } from "./page";

const http = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspaces/test",
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) =>
      options?.defaultValue ?? key,
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
function mount(element: React.ReactNode) {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  caches.push(cache);
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>{element}</MemoryRouter>
    </QueryClientProvider>,
  );
  return cache;
}
function response(value: unknown) {
  return { data: value, response: new Response() };
}
function observation(
  id: string,
  parent: string | null = null,
): Schema["Observation"] {
  return {
    id,
    parent_id: parent,
    type: "agent",
    name: id,
    started_at: "2026-09-11T00:00:00Z",
    ended_at: null,
    status: null,
    level: "error",
    status_message: "Diagnostic reason",
    model: { requested: "model-alias", response: "model-version" },
    usage: { input: 0, total: 3506 },
    cost_usd: null,
    input: { media_type: "application/json", value: null },
    output: null,
    attributes: { diagnostic: true },
    resource_attributes: { "service.name": "service" },
    scope: { name: "library", version: "1", attributes: {} },
    events: [],
    links: [],
  };
}
function trace(): Schema["Trace"] {
  return {
    id: "trace-1",
    provider: "langfuse",
    root: observation("root"),
    source_url: "https://trace.example/trace-1",
    correlation: {
      organization_id: "org",
      workspace_id: "ws_test",
      session_id: "session",
      thread_id: "thread",
      run_id: "run",
      run_attempt_id: "attempt",
      agent_id: "agent",
    },
  };
}
const descriptor = {
  provider: "langfuse",
  enabled: true,
  search_in: ["input", "output"],
  history_from: null,
};

it("uses advertised search targets and preserves empty-page continuation", async () => {
  const user = userEvent.setup();
  http.GET.mockImplementation(async (path, options) =>
    response(
      path.endsWith("trace-query")
        ? descriptor
        : options.params.query.cursor
          ? { items: [trace()], next_cursor: null }
          : { items: [], next_cursor: "next" },
    ),
  );
  mount(<TracesPage />);
  await screen.findByText("No traces in this range");
  await user.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByRole("link", { name: /root/ });
  await user.type(
    screen.getByRole("textbox", { name: "Search content" }),
    "needle",
  );
  await user.click(screen.getByRole("button", { name: "Apply filters" }));
  await waitFor(() =>
    expect(http.GET).toHaveBeenLastCalledWith(
      expect.stringContaining("/traces"),
      expect.objectContaining({
        params: expect.objectContaining({
          query: expect.objectContaining({
            query: "needle",
            search_in: "input",
            cursor: undefined,
          }),
        }),
      }),
    ),
  );
  await user.click(screen.getByRole("button", { name: "More filters" }));
  await user.click(screen.getByRole("combobox", { name: "Search in" }));
  expect(screen.queryByRole("option", { name: "Input and output" })).toBeNull();
  expect(screen.getByRole("option", { name: "Output" })).toBeTruthy();
});

it("does not call the backend data routes when query is disabled", async () => {
  http.GET.mockResolvedValue(
    response({ ...descriptor, enabled: false, search_in: [] }),
  );
  mount(<TracesPage />);
  await screen.findByText("Trace query disabled");
  expect(http.GET).toHaveBeenCalledTimes(1);
});

it("distinguishes a configured but unavailable backend", async () => {
  http.GET.mockImplementation(async (path) => {
    if (path.endsWith("trace-query")) return response(descriptor);
    throw new ApiError(503, "trace_query_unavailable", "Unavailable", {}, null);
  });
  mount(<TracesPage />);
  await screen.findByText("Trace query unavailable");
  expect(screen.queryByText("Trace query disabled")).toBeNull();
});

it("loads separate observation pages, deduplicates the root, and retains pagination after an empty page and retry", async () => {
  const user = userEvent.setup();
  let fail = true;
  http.GET.mockImplementation(async (path, options) => {
    if (!path.endsWith("observations")) return response(trace());
    const cursor = options.params.query.cursor;
    if (!cursor)
      return response({
        items: [observation("child", "parent"), observation("root")],
        next_cursor: "empty",
      });
    if (cursor === "empty")
      return response({ items: [], next_cursor: "parent" });
    if (fail) {
      fail = false;
      throw new Error("Temporary page failure");
    }
    return response({
      items: [observation("parent", "root")],
      next_cursor: null,
    });
  });
  mount(<TraceDetail traceId="trace-1" />);
  await screen.findByRole("button", { name: /child/ });
  expect(screen.getAllByRole("button", { name: /^root/ })).toHaveLength(1);
  expect(screen.queryByText("Attempt outcome")).toBeNull();
  expect(screen.getByText("Root cost (USD)")).toBeTruthy();
  expect(
    screen.getByRole("link", { name: "Open run" }).getAttribute("href"),
  ).toContain("/sessions/session/threads/thread/runs/run");
  await user.click(
    screen.getByRole("button", { name: "Load more observations" }),
  );
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Load more observations",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
  await user.click(
    screen.getByRole("button", { name: "Load more observations" }),
  );
  await screen.findByText("Temporary page failure");
  await user.click(screen.getByRole("button", { name: "Reload" }));
  await screen.findByRole("button", { name: /^parent/ });
  expect(
    screen.queryByRole("button", { name: "Load more observations" }),
  ).toBeNull();
  const labels = screen
    .getAllByRole("button")
    .map((button) => button.textContent);
  expect(labels.findIndex((label) => label?.startsWith("parent"))).toBeLessThan(
    labels.findIndex((label) => label?.startsWith("child")),
  );
  await user.click(screen.getByRole("button", { name: /^child/ }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText("Diagnostic reason")).toBeTruthy();
  expect(within(dialog).getByText("Requested model")).toBeTruthy();
  expect(within(dialog).getByText("model-alias")).toBeTruthy();
  expect(within(dialog).getByText("model-version")).toBeTruthy();
  expect(within(dialog).getByText("error")).toBeTruthy();
  expect(within(dialog).getByText("unavailable")).toBeTruthy();
  expect(
    within(dialog).getByRole("button", { name: "Resource attributes" }),
  ).toBeTruthy();
});

it("hides cached detail after observation authorization is revoked", async () => {
  const user = userEvent.setup();
  http.GET.mockImplementation(async (path, options) => {
    if (!path.endsWith("observations")) return response(trace());
    if (options.params.query.cursor)
      throw new ApiError(404, "trace_not_found", "Trace not found", {}, null);
    return response({
      items: [observation("private-child")],
      next_cursor: "next",
    });
  });
  mount(<TraceDetail traceId="trace-1" />);
  await screen.findByRole("button", { name: /private-child/ });
  await user.click(
    screen.getByRole("button", { name: "Load more observations" }),
  );
  await screen.findByText("Trace not found");
  expect(screen.queryByRole("button", { name: /private-child/ })).toBeNull();
  expect(screen.queryByRole("link", { name: "Open run" })).toBeNull();
});
