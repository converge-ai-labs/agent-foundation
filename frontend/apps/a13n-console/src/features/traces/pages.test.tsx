import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { ApiError } from "../../service-client";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { TraceDetail } from "./detail";
import { TracesPage } from "./page";
import { UNKNOWN } from "../../shared/unknown";
import { mockIntersections } from "../../../tests/intersection-observer";
const intersect = mockIntersections();

const http = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspaces/test",
    can: () => false,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string; provider?: string }) =>
      (options?.defaultValue ?? key).replace(
        "{{provider}}",
        options?.provider ?? "",
      ),
    i18n: { resolvedLanguage: "en" },
  }),
}));
const caches: QueryClient[] = [];
afterEach(() => {
  cleanup();
  caches.forEach((cache) => cache.clear());
  caches.length = 0;
  vi.resetAllMocks();
  vi.useRealTimers();
});
/** The list applies edited filters after a 350 ms pause; fake time skips it. */
function typingUser() {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  return userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
}
const pause = () => act(() => vi.advanceTimersByTimeAsync(350));
function mount(element: React.ReactNode, entry = "/", staleTime = 0) {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime } },
  });
  caches.push(cache);
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={[entry]}>{element}</MemoryRouter>
    </QueryClientProvider>,
  );
  return cache;
}
function response(value: unknown) {
  return { data: value, response: new Response() };
}
function observation(id: string, parent: string | null = null): Schema["Span"] {
  return {
    trace_id: "trace-1",
    id,
    parent_id: parent,
    kind: "agent",
    name: id,
    started_at: "2026-09-11T00:00:00Z",
    ended_at: null,
    status: "error",
    status_message: "Diagnostic reason",
    level: "error",
    model: "model-version",
    usage: { input: 0, total: 3506 },
    cost_usd: null,
    input: null,
    output: null,
    attributes: { diagnostic: true },
    resource_attributes: { "service.name": "service" },
    scope: { name: "library", version: "1" },
    events: [],
    links: [],
    source_url: null,
  };
}
/** A trace root: it carries the correlation the Service stamps on every span. */
function trace(): Schema["Span"] {
  return {
    ...observation("root"),
    source_url: "https://trace.example/trace-1",
    attributes: {
      "a13n.observation.metadata.organization_id": "org",
      "a13n.observation.metadata.workspace_id": "ws_test",
      "a13n.observation.metadata.session_id": "session",
      "a13n.observation.metadata.service_run_id": "run",
      "a13n.observation.metadata.run_attempt_id": "attempt",
      "a13n.observation.session.id": "thread",
    },
  };
}
const descriptor: Schema["TraceBackend"] = {
  type: "langfuse",
  queryable_since: null,
};
const isBackend = (path: string) => path.endsWith("/trace-backend");
const isSpans = (path: string) => path.endsWith("/spans");
const isSpan = (path: string) => path.endsWith("/spans/{span_id}");

function overview(value: Schema["Span"]): Schema["SpanOverview"] {
  const {
    trace_id,
    id,
    parent_id,
    name,
    kind,
    status,
    level,
    started_at,
    ended_at,
    model,
    cost_usd,
  } = value;
  return {
    view: "overview",
    trace_id,
    id,
    parent_id,
    name,
    kind,
    status,
    level,
    started_at,
    ended_at,
    model,
    cost_usd,
  };
}

function overviewResponse(
  items: Schema["Span"][],
  next_cursor: string | null = null,
) {
  return response({ items: items.map(overview), next_cursor });
}

it("loads the overview alongside the root and fetches full content only for the selected child", async () => {
  const user = userEvent.setup();
  let releaseRoot = () => {};
  let releaseChild = () => {};
  const rootReady = new Promise<void>((resolve) => (releaseRoot = resolve));
  const childReady = new Promise<void>((resolve) => (releaseChild = resolve));
  const child = {
    ...observation("child", "root"),
    name: "Full observation name ".repeat(20),
    input: "Private child input",
    output: "Private child output",
  };
  http.GET.mockImplementation(async (path, options) => {
    if (isBackend(path)) return response(descriptor);
    if (isSpans(path)) {
      expect(options.params.query).toMatchObject({
        view: "overview",
        limit: 50,
      });
      return overviewResponse([child]);
    }
    if (isSpan(path)) {
      await childReady;
      return response(child);
    }
    await rootReady;
    return response(trace());
  });
  mount(<TraceDetail traceId="trace-1" />);
  await waitFor(() =>
    expect(http.GET.mock.calls.some(([path]) => isSpans(path))).toBe(true),
  );
  expect(screen.queryByRole("treeitem")).toBeNull();
  expect(http.GET.mock.calls.some(([path]) => isSpan(path))).toBe(false);
  releaseRoot();
  await user.click(
    await screen.findByRole("treeitem", { name: /Full observation name/ }),
  );
  await waitFor(() =>
    expect(http.GET).toHaveBeenCalledWith(
      "/api/v1/traces/{trace_id}/spans/{span_id}",
      expect.objectContaining({
        params: { path: { trace_id: "trace-1", span_id: "child" } },
      }),
    ),
  );
  const panel = within(await screen.findByRole("complementary"));
  expect(panel.getByText(child.name.trim())).toBeTruthy();
  expect(panel.queryByText("Private child input")).toBeNull();
  releaseChild();
  expect(await panel.findByText("Private child input")).toBeTruthy();
  expect(panel.getByText("Private child output")).toBeTruthy();
  await user.click(screen.getByRole("treeitem", { name: /^root/ }));
  await waitFor(() =>
    expect(screen.queryByText("Private child input")).toBeNull(),
  );
  expect(http.GET.mock.calls.filter(([path]) => isSpan(path))).toHaveLength(1);
});

it("resets visible content and raw mode when switching cached observations", async () => {
  const user = userEvent.setup();
  const input = Array.from({ length: 12 }, (_, index) => ({
    role: "user",
    content: `Retained message ${index}`,
  }));
  const children = ["first-child", "second-child"].map((id) => ({
    ...observation(id, "root"),
    input,
  }));
  http.GET.mockImplementation(async (path, options) => {
    if (isBackend(path)) return response(descriptor);
    if (isSpans(path)) return overviewResponse(children);
    if (isSpan(path))
      return response(
        children.find((child) => child.id === options.params.path.span_id),
      );
    return response(trace());
  });
  const cache = mount(<TraceDetail traceId="trace-1" />);
  cache.setQueryDefaults(["trace-observation"], { staleTime: Infinity });
  await user.click(
    await screen.findByRole("treeitem", { name: /^first-child/ }),
  );
  await screen.findByText("Retained message 0");
  intersect(document.querySelector('[data-content-chunk="11"]')!, true);
  intersect(document.querySelector('[data-content-chunk="0"]')!, false);
  expect(screen.getByText("Retained message 11")).toBeTruthy();
  const scroll = screen
    .getByText("Retained message 11")
    .closest("aside")!
    .querySelector<HTMLElement>(".a13n-scrollbar")!;
  scroll.style.overflowY = "auto";
  scroll.scrollTop = 2000;
  await user.click(screen.getByRole("treeitem", { name: /^second-child/ }));
  await screen.findByText("Retained message 0");
  expect(screen.queryByText("Retained message 11")).toBeNull();
  expect(scroll.scrollTop).toBe(0);
  await user.click(screen.getByRole("button", { name: "Raw JSON" }));
  expect(screen.getByRole("button", { name: "Preview" })).toBeTruthy();
  await user.click(screen.getByRole("treeitem", { name: /^first-child/ }));
  await screen.findByText("Retained message 0");
  expect(screen.getByRole("button", { name: "Raw JSON" })).toBeTruthy();
  expect(http.GET.mock.calls.filter(([path]) => isSpan(path))).toHaveLength(2);
});

it("retries a selected observation failure without losing the loaded call tree", async () => {
  const user = userEvent.setup();
  let fail = true;
  const child = { ...observation("child", "root"), input: "Recovered input" };
  http.GET.mockImplementation(async (path) => {
    if (isBackend(path)) return response(descriptor);
    if (isSpans(path)) return overviewResponse([child]);
    if (isSpan(path)) {
      if (fail) throw new Error("Observation temporarily unavailable");
      return response(child);
    }
    return response(trace());
  });
  mount(<TraceDetail traceId="trace-1" />);
  await user.click(await screen.findByRole("treeitem", { name: /^child/ }));
  await screen.findByText("Observation temporarily unavailable");
  expect(screen.getAllByRole("treeitem")).toHaveLength(2);
  fail = false;
  await user.click(screen.getByRole("button", { name: "Reload" }));
  await screen.findByText("Recovered input");
});

it("hides cached observation content and the trace when detail access is revoked", async () => {
  const user = userEvent.setup();
  let revoked = false;
  const child = {
    ...observation("child", "root"),
    input: "Private cached input",
  };
  http.GET.mockImplementation(async (path) => {
    if (isBackend(path)) return response(descriptor);
    if (isSpans(path)) return overviewResponse([child]);
    if (isSpan(path)) {
      if (revoked)
        throw new ApiError(403, "forbidden", "Access revoked", {}, null);
      return response(child);
    }
    return response(trace());
  });
  const cache = mount(<TraceDetail traceId="trace-1" />);
  await user.click(await screen.findByRole("treeitem", { name: /^child/ }));
  await screen.findByText("Private cached input");
  revoked = true;
  await act(() => cache.invalidateQueries({ queryKey: ["trace-observation"] }));
  await screen.findByText("Access revoked");
  expect(screen.queryByText("Private cached input")).toBeNull();
  expect(screen.queryByRole("treeitem")).toBeNull();
  expect(screen.queryByRole("link", { name: "View run" })).toBeNull();
});

it("searches identifiers automatically and preserves empty-page continuation", async () => {
  const user = typingUser();
  http.GET.mockImplementation(async (path, options) =>
    isSpans(path)
      ? response({ items: [], next_cursor: null })
      : response(
          isBackend(path)
            ? descriptor
            : options.params.query.cursor
              ? { items: [trace()], next_cursor: null }
              : { items: [], next_cursor: "next" },
        ),
  );
  mount(<TracesPage />);
  await screen.findByText("No traces in this range");
  await user.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByRole("link", { name: /root/ }, { timeout: 3000 });
  const search = screen.getByRole("searchbox", { name: "Search by ID" });
  await user.type(search, "thread_abc");
  await pause();
  await waitFor(() =>
    expect(http.GET).toHaveBeenLastCalledWith(
      expect.stringContaining("/traces"),
      expect.objectContaining({
        params: expect.objectContaining({
          query: expect.objectContaining({
            thread_id: "thread_abc",
            run_id: undefined,
            session_id: undefined,
            cursor: undefined,
          }),
        }),
      }),
    ),
  );
  await user.clear(search);
  await user.type(search, "sess_123");
  await pause();
  await waitFor(() =>
    expect(http.GET).toHaveBeenLastCalledWith(
      expect.stringContaining("/traces"),
      expect.objectContaining({
        params: expect.objectContaining({
          query: expect.objectContaining({
            session_id: "sess_123",
            thread_id: undefined,
            run_id: undefined,
          }),
        }),
      }),
    ),
  );
  await user.clear(search);
  await user.type(search, "run_xyz");
  await pause();
  await waitFor(() =>
    expect(http.GET).toHaveBeenLastCalledWith(
      expect.stringContaining("/traces"),
      expect.objectContaining({
        params: expect.objectContaining({
          query: expect.objectContaining({
            thread_id: undefined,
            run_id: "run_xyz",
          }),
        }),
      }),
    ),
  );
});

it("filters by attributes picked from the listed traces", async () => {
  const user = typingUser();
  const root = {
    ...trace(),
    attributes: { ...trace().attributes, scenario: "review", synthetic: true },
  };
  http.GET.mockImplementation(async (path) =>
    isSpans(path)
      ? response({ items: [], next_cursor: null })
      : response(
          isBackend(path) ? descriptor : { items: [root], next_cursor: null },
        ),
  );
  mount(<TracesPage />);
  await screen.findByRole("link", { name: /root/ }, { timeout: 3000 });
  await user.click(screen.getByRole("button", { name: "Metadata" }));
  await user.click(screen.getByRole("combobox", { name: "Metadata key 1" }));
  // Service correlation is searched by ID, so only the other attributes are offered.
  expect(
    (await screen.findAllByRole("option")).map((item) => item.textContent),
  ).toEqual(["scenario", "synthetic"]);
  await user.click(screen.getByRole("option", { name: "scenario" }));
  await user.type(
    screen.getByRole("textbox", { name: "Metadata value 1" }),
    "review",
  );
  await user.click(screen.getByRole("button", { name: "Add filter" }));
  await user.click(screen.getByRole("combobox", { name: "Metadata key 2" }));
  await user.click(await screen.findByRole("option", { name: "synthetic" }));
  await user.type(
    screen.getByRole("textbox", { name: "Metadata value 2" }),
    "true",
  );
  await pause();
  await waitFor(() =>
    expect(http.GET).toHaveBeenCalledWith(
      expect.stringContaining("/traces"),
      expect.objectContaining({
        params: expect.objectContaining({
          query: expect.objectContaining({
            limit: 25,
            attribute: ["scenario:review", "synthetic:true"],
          }),
        }),
      }),
    ),
  );
  await user.click(screen.getByRole("button", { name: "Remove filter 1" }));
  await pause();
  await waitFor(() =>
    expect(http.GET).toHaveBeenCalledWith(
      expect.stringContaining("/traces"),
      expect.objectContaining({
        params: expect.objectContaining({
          query: expect.objectContaining({
            limit: 25,
            attribute: ["synthetic:true"],
          }),
        }),
      }),
    ),
  );
});

it("does not call the backend data routes when query is disabled", async () => {
  http.GET.mockResolvedValue(response({ type: null, queryable_since: null }));
  mount(<TracesPage />);
  await screen.findByText("Trace query disabled");
  expect(http.GET).toHaveBeenCalledTimes(1);
});

it("distinguishes a configured but unavailable backend", async () => {
  http.GET.mockImplementation(async (path) => {
    if (isBackend(path)) return response(descriptor);
    throw new ApiError(503, "unavailable", "Unavailable", {}, null);
  });
  mount(<TracesPage />);
  await screen.findByText("Trace query unavailable");
  expect(screen.queryByText("Trace query disabled")).toBeNull();
});

it.each([
  ["session_id", "sess_linked"],
  ["thread_id", "thread_linked"],
  ["run_id", "run_linked"],
])("opens filtered by a linked %s", async (filter, id) => {
  http.GET.mockImplementation(async (path) =>
    response(isBackend(path) ? descriptor : { items: [], next_cursor: null }),
  );
  mount(<TracesPage />, `/?${filter}=${id}`);
  await screen.findByText("No traces in this range");
  expect(
    screen.getByRole("searchbox", { name: "Search by ID" }),
  ).toHaveProperty("value", id);
  expect(http.GET).toHaveBeenCalledWith(
    "/api/v1/traces",
    expect.objectContaining({
      params: expect.objectContaining({
        query: expect.objectContaining({
          session_id: undefined,
          thread_id: undefined,
          run_id: undefined,
          [filter]: id,
        }),
      }),
    }),
  );
});

it("preserves a short linked Run window when opening and applying its time filter", async () => {
  const user = userEvent.setup();
  http.GET.mockImplementation(async (path) =>
    response(isBackend(path) ? descriptor : { items: [], next_cursor: null }),
  );
  const params = new URLSearchParams({
    run_id: "run_short",
    from: "2026-10-21T20:40:51.802025+08:00",
    to: "2026-10-21T12:40:54.600Z",
  });
  mount(<TracesPage />, `/?${params}`);
  await screen.findByText("No traces in this range");
  const expected = expect.objectContaining({
    params: expect.objectContaining({
      query: expect.objectContaining({
        run_id: "run_short",
        started_after: "2026-10-21T12:40:51.802Z",
        started_before: "2026-10-21T12:40:54.600Z",
      }),
    }),
  });
  expect(http.GET).toHaveBeenCalledWith("/api/v1/traces", expected);
  await user.click(screen.getByRole("button", { name: /Time range:/ }));
  expect(screen.queryByRole("alert")).toBeNull();
  // Even opening a nested field and accepting it without edits must preserve milliseconds.
  await user.click(screen.getByRole("button", { name: "Start time" }));
  const field = within(screen.getByRole("dialog", { name: "Start time" }));
  expect(field.getByRole("combobox", { name: "Second" })).toBeTruthy();
  await user.click(field.getByRole("button", { name: "Apply" }));
  await waitFor(() =>
    expect(screen.queryByRole("dialog", { name: "Start time" })).toBeNull(),
  );
  await user.click(screen.getByRole("button", { name: "Apply" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await user.click(screen.getByRole("button", { name: "Refresh" }));
  await waitFor(() =>
    expect(
      http.GET.mock.calls.filter(([path]) => path === "/api/v1/traces").length,
    ).toBeGreaterThan(1),
  );
  for (const [path, options] of http.GET.mock.calls)
    if (path === "/api/v1/traces") expect(options).toEqual(expected);
});

it.each([
  ["equal", "2026-10-21T12:40:51Z"],
  ["reversed", "2026-10-21T12:40:50Z"],
  ["over 31 days", "2026-11-22T12:40:51Z"],
])(
  "falls back to the default window for an invalid %s linked range",
  async (_, to) => {
    http.GET.mockImplementation(async (path) =>
      response(isBackend(path) ? descriptor : { items: [], next_cursor: null }),
    );
    const params = new URLSearchParams({
      run_id: "run_linked",
      from: "2026-10-21T12:40:51Z",
      to,
    });
    mount(<TracesPage />, `/?${params}`);
    await screen.findByText("No traces in this range");
    expect(
      screen.getByRole("button", { name: "Time range: Last 24 hours" }),
    ).toBeTruthy();
    for (const [path, options] of http.GET.mock.calls) {
      if (path !== "/api/v1/traces") continue;
      const query = options.params.query;
      expect(query.run_id).toBe("run_linked");
      expect(
        Date.parse(query.started_before) - Date.parse(query.started_after),
      ).toBe(86_400_000);
    }
  },
);

it("names the backend and how far back it finds traces", async () => {
  http.GET.mockImplementation(async (path) =>
    response(
      isBackend(path)
        ? { type: "logfire", queryable_since: "2026-08-24T00:00:00Z" }
        : { items: [], next_cursor: null },
    ),
  );
  mount(<TracesPage />);
  const chip = await screen.findByTitle(/^Queryable since /);
  expect(chip.textContent).toBe("by Logfire");
});

it("loads separate observation pages, deduplicates the root, and retains pagination after an empty page and retry", async () => {
  const user = userEvent.setup();
  let fail = true;
  http.GET.mockImplementation(async (path, options) => {
    if (isBackend(path)) return response(descriptor);
    if (isSpan(path)) return response(observation("child", "parent"));
    if (!isSpans(path)) return response(trace());
    expect(options.params.query.view).toBe("overview");
    const cursor = options.params.query.cursor;
    if (!cursor)
      return overviewResponse(
        [observation("child", "parent"), observation("root")],
        "empty",
      );
    if (cursor === "empty")
      return response({ items: [], next_cursor: "parent" });
    if (fail) {
      fail = false;
      throw new Error("Temporary page failure");
    }
    return overviewResponse([observation("parent", "root")]);
  });
  mount(<TraceDetail traceId="trace-1" />);
  await screen.findByRole("treeitem", { name: /child/ });
  expect(screen.getAllByRole("treeitem", { name: /^root/ })).toHaveLength(1);
  expect(screen.queryByText("Attempt outcome")).toBeNull();
  expect(screen.getByText("Loaded cost")).toBeTruthy();
  expect(
    screen.getByRole("link", { name: "View run" }).getAttribute("href"),
  ).toContain(
    "/workspaces/test/sessions/session/threads/thread/runs/run?view=debug",
  );
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
  await screen.findByRole("treeitem", { name: /^parent/ });
  expect(
    screen.queryByRole("button", { name: "Load more observations" }),
  ).toBeNull();
  const labels = screen.getAllByRole("treeitem").map((row) => row.textContent);
  expect(labels.findIndex((label) => label?.startsWith("parent"))).toBeLessThan(
    labels.findIndex((label) => label?.startsWith("child")),
  );
  await user.click(screen.getByRole("treeitem", { name: /^child/ }));
  const panel = within(await screen.findByRole("complementary"));
  expect(await panel.findByText("Diagnostic reason")).toBeTruthy();
  expect(panel.getByText("Model")).toBeTruthy();
  expect(panel.getAllByText(/model-version/).length).toBeGreaterThan(0);
  expect(panel.getByText("Error")).toBeTruthy();
  expect(panel.getByText("error")).toBeTruthy();
  expect(panel.getByText("Telemetry status")).toBeTruthy();
  expect(panel.getAllByText(UNKNOWN).length).toBeGreaterThan(0);
  for (const name of ["Resource attributes", "Scope", "Events", "Links"])
    expect(panel.getByRole("button", { name })).toBeTruthy();
});

it.each(["langfuse", "logfire"] as const)(
  "keeps Run navigation local and names the external %s destination",
  async (type) => {
    http.GET.mockImplementation(async (path) =>
      response(
        isBackend(path)
          ? { ...descriptor, type }
          : isSpans(path)
            ? { items: [], next_cursor: null }
            : trace(),
      ),
    );
    mount(<TraceDetail traceId="trace-1" />);
    const backend = await screen.findByRole("link", {
      name: `View in ${type === "langfuse" ? "Langfuse" : "Logfire"}`,
    });
    const run = screen.getByRole("link", { name: "View run" });
    expect(run.getAttribute("href")).toBe(
      "/workspaces/test/sessions/session/threads/thread/runs/run?view=debug",
    );
    expect(run.getAttribute("target")).toBeNull();
    expect(backend.getAttribute("href")).toBe(trace().source_url);
    expect(backend.getAttribute("target")).toBe("_blank");
    expect(backend.getAttribute("rel")).toBe("noopener noreferrer");
    expect(
      document.getElementById(backend.getAttribute("aria-describedby")!)
        ?.textContent,
    ).toBe("Opens in a new tab");
    run.focus();
    await userEvent.setup().tab();
    expect(document.activeElement).toBe(backend);
  },
);

it.each([null, "javascript:alert(1)", "https://user:password@trace.example"])(
  "omits the backend action when the source is unavailable or unsafe: %s",
  async (source_url) => {
    http.GET.mockImplementation(async (path) =>
      response(
        isBackend(path)
          ? descriptor
          : isSpans(path)
            ? { items: [], next_cursor: null }
            : { ...trace(), source_url },
      ),
    );
    mount(<TraceDetail traceId="trace-1" />);
    await screen.findByRole("link", { name: "View run" });
    await waitFor(() =>
      expect(http.GET).toHaveBeenCalledWith(
        "/api/v1/trace-backend",
        expect.anything(),
      ),
    );
    expect(screen.queryByRole("link", { name: /View in/ })).toBeNull();
  },
);

it("derives the correlation from the root without reading the run", async () => {
  const user = userEvent.setup();
  http.GET.mockImplementation(async (path) =>
    response(
      isBackend(path)
        ? descriptor
        : isSpans(path)
          ? { items: [], next_cursor: null }
          : trace(),
    ),
  );
  mount(<TraceDetail traceId="trace-1" />);
  await screen.findByRole("link", { name: "View run" });
  await user.click(screen.getByRole("tab", { name: "Metadata" }));
  await user.click(screen.getByRole("button", { name: "Correlation" }));
  for (const [key, value] of [
    ["session_id", "session"],
    ["thread_id", "thread"],
    ["run_id", "run"],
  ]) {
    const row = screen.getByText(key).parentElement;
    expect(row && within(row).getByText(value)).toBeTruthy();
  }
  expect(
    http.GET.mock.calls.some(([path]) => String(path).includes("/runs/")),
  ).toBe(false);
});

it("offers no run link when the root does not name the run's session", async () => {
  const root = trace();
  delete root.attributes["a13n.observation.metadata.session_id"];
  http.GET.mockImplementation(async (path) =>
    response(
      isBackend(path)
        ? descriptor
        : isSpans(path)
          ? { items: [], next_cursor: null }
          : root,
    ),
  );
  mount(<TraceDetail traceId="trace-1" />);
  await screen.findByRole("link", { name: "View in Langfuse" });
  expect(screen.queryByRole("link", { name: "View run" })).toBeNull();
});

it("hides cached detail after observation authorization is revoked", async () => {
  const user = userEvent.setup();
  http.GET.mockImplementation(async (path, options) => {
    if (isBackend(path)) return response(descriptor);
    if (!isSpans(path)) return response(trace());
    if (options.params.query.cursor)
      throw new ApiError(404, "trace_not_found", "Trace not found", {}, null);
    return overviewResponse([observation("private-child")], "next");
  });
  mount(<TraceDetail traceId="trace-1" />);
  await screen.findByRole("treeitem", { name: /private-child/ });
  await user.click(
    screen.getByRole("button", { name: "Load more observations" }),
  );
  await screen.findByText("Trace not found");
  expect(screen.queryByRole("treeitem", { name: /private-child/ })).toBeNull();
  expect(screen.queryByRole("link", { name: "View run" })).toBeNull();
});

it("shows a loaded cost subtotal until pagination succeeds, without counting the root twice", async () => {
  const user = userEvent.setup();
  const root = { ...trace(), cost_usd: "0.1" };
  let fail = true;
  http.GET.mockImplementation(async (path, options) => {
    if (isBackend(path)) return response(descriptor);
    if (!isSpans(path)) return response(root);
    if (!options.params.query.cursor)
      return overviewResponse(
        [root, { ...observation("chat", "root"), cost_usd: "0.2" }],
        "next",
      );
    if (fail) {
      fail = false;
      throw new Error("Cost page failed");
    }
    return overviewResponse([
      { ...observation("chat", "root"), cost_usd: "0.2" },
      { ...observation("later", "root"), cost_usd: "0.05" },
    ]);
  });
  mount(<TraceDetail traceId="trace-1" />);
  await screen.findByText("$0.30");
  expect(screen.getByText("Loaded cost")).toBeTruthy();
  expect(screen.queryByText("Cost")).toBeNull();
  await user.click(
    screen.getByRole("button", { name: "Load more observations" }),
  );
  await screen.findByText("Cost page failed");
  expect(screen.getByText("Loaded cost")).toBeTruthy();
  expect(screen.getByText("$0.30")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Reload" }));
  await screen.findByText("Cost");
  expect(screen.getByText("$0.35")).toBeTruthy();
});

it("shows seconds, normalized levels and paginated aggregate cost in the list", async () => {
  const root = {
    ...trace(),
    level: "default",
    cost_usd: "0.1",
    ended_at: "2026-09-11T00:00:02.5Z",
    input: { content: [{ type: "text", text: "Input preview" }] },
    output: "Output preview",
  };
  http.GET.mockImplementation(async (path, options) => {
    if (isBackend(path)) return response(descriptor);
    if (!isSpans(path)) return response({ items: [root], next_cursor: null });
    expect(options.params.query).toMatchObject({
      view: "overview",
      limit: 100,
    });
    if (!options.params.query.cursor)
      return overviewResponse(
        [root, { ...observation("chat"), cost_usd: "0.2" }],
        "empty",
      );
    if (options.params.query.cursor === "empty")
      return response({ items: [], next_cursor: "last" });
    return overviewResponse([
      { ...observation("chat"), cost_usd: "0.2" },
      { ...observation("tool"), cost_usd: "0.05" },
    ]);
  });
  mount(<TracesPage />);
  await screen.findByText("$0.35");
  expect(screen.getByText("2.50 s")).toBeTruthy();
  expect(screen.getByText("Info")).toBeTruthy();
  expect(
    screen.getAllByRole("columnheader").map((cell) => cell.textContent?.trim()),
  ).toEqual(["Trace", "Run", "Level", "Duration", "Cost", "Started"]);
  expect(
    screen.getByRole("link", { name: "run" }).getAttribute("href"),
  ).toContain("/workspaces/test/sessions/session/threads/thread/runs/run");
  expect(screen.queryByText("Root status")).toBeNull();
  expect(screen.queryByText("Severity")).toBeNull();
  expect(screen.queryByText(/USD|Unavailable/)).toBeNull();
  expect(http.GET).toHaveBeenCalledWith(
    "/api/v1/traces",
    expect.objectContaining({
      params: expect.objectContaining({
        query: expect.objectContaining({ limit: 25 }),
      }),
    }),
  );
  expect(
    http.GET.mock.calls.some(([path]) => String(path).includes("/runs/")),
  ).toBe(false);
});

it("does not expose root-only or partial list costs when a later page fails or repeats", async () => {
  const root = { ...trace(), cost_usd: "9" };
  http.GET.mockImplementation(async (path, options) => {
    if (isBackend(path)) return response(descriptor);
    if (!isSpans(path))
      return response({
        items: [root, { ...root, id: "repeated-root", trace_id: "repeated" }],
        next_cursor: null,
      });
    if (options.params.path.trace_id === "repeated")
      return overviewResponse([root], "same");
    if (options.params.query.cursor) throw new Error("Cost read failed");
    return overviewResponse([root], "next");
  });
  const cache = mount(<TracesPage />);
  await screen.findByRole("columnheader", { name: "Trace" });
  await waitFor(() => expect(cache.isFetching()).toBe(0));
  expect(screen.queryByText("$9.00")).toBeNull();
  for (const row of screen.getAllByRole("row").slice(1)) {
    const cells = within(row).getAllByRole("cell");
    expect(cells[3].textContent).toBe(UNKNOWN);
    expect(cells[4].textContent).toBe(UNKNOWN);
  }
  expect(http.GET.mock.calls.filter(([path]) => isSpans(path))).toHaveLength(4);
});

it("hides the list after a cost read reports revoked access", async () => {
  http.GET.mockImplementation(async (path) => {
    if (isBackend(path)) return response(descriptor);
    if (isSpans(path))
      throw new ApiError(404, "trace_not_found", "Trace not found", {}, null);
    return response({ items: [trace()], next_cursor: null });
  });
  mount(<TracesPage />);
  await screen.findByText("Trace not found");
  expect(screen.queryByRole("table")).toBeNull();
});

it("bounds list cost concurrency and stops pending reads when leaving the list", async () => {
  const pending: { signal: AbortSignal; finish: () => void }[] = [];
  http.GET.mockImplementation(async (path, options) => {
    if (isBackend(path)) return response(descriptor);
    if (!isSpans(path))
      return response({
        items: Array.from({ length: 6 }, (_, index) => ({
          ...trace(),
          id: `root-${index}`,
          trace_id: `trace-${index}`,
        })),
        next_cursor: null,
      });
    await new Promise<void>((resolve) =>
      pending.push({ signal: options.signal, finish: resolve }),
    );
    return response({ items: [], next_cursor: null });
  });
  mount(<TracesPage />);
  await waitFor(() => expect(pending).toHaveLength(4));
  pending[0].finish();
  await waitFor(() => expect(pending).toHaveLength(5));
  cleanup();
  expect(pending.every(({ signal }) => signal.aborted)).toBe(true);
  pending.forEach(({ finish }) => finish());
  await waitFor(() => expect(pending).toHaveLength(5));
});

it("caps advancing cost pagination without exposing a partial total", async () => {
  let reads = 0;
  http.GET.mockImplementation(async (path) => {
    if (isBackend(path)) return response(descriptor);
    if (!isSpans(path))
      return response({ items: [trace()], next_cursor: null });
    reads++;
    return overviewResponse(
      [{ ...observation(`part-${reads}`), cost_usd: "1" }],
      `cursor-${reads}`,
    );
  });
  const cache = mount(<TracesPage />);
  await screen.findByRole("columnheader", { name: "Cost" });
  await waitFor(() => expect(cache.isFetching()).toBe(0));
  expect(reads).toBe(20);
  expect(
    within(screen.getAllByRole("row")[1]).getAllByRole("cell")[4].textContent,
  ).toBe(UNKNOWN);
});

it("uses one duration format in the detail overview, timeline and observation panel", async () => {
  const user = userEvent.setup();
  const root = { ...trace(), ended_at: "2026-09-11T00:00:02.5Z" };
  http.GET.mockImplementation(async (path) =>
    response(
      isBackend(path)
        ? descriptor
        : isSpans(path)
          ? { items: [overview(root)], next_cursor: null }
          : root,
    ),
  );
  mount(<TraceDetail traceId="trace-1" />);
  await screen.findByRole("treeitem", { name: /^root/ });
  const scale = screen
    .getAllByText("2.50 s")
    .find((node) => node.closest("[aria-hidden]"));
  expect(scale?.parentElement?.textContent).toBe("02.50 s");
  expect(
    within(screen.getByRole("treeitem", { name: /^root/ })).getByText("2.50 s"),
  ).toBeTruthy();
  expect(screen.queryByText("Root status")).toBeNull();
  await user.click(screen.getByRole("treeitem", { name: /^root/ }));
  const panel = within(await screen.findByRole("complementary"));
  expect(panel.getByText("2.50 s")).toBeTruthy();
  expect(panel.queryByText(/\d ms\b/)).toBeNull();
});

it("immediately hides revoked content without waiting for unrelated cost reads or starting more pages", async () => {
  const pending: (() => void)[] = [];
  http.GET.mockImplementation(async (path, options) => {
    if (isBackend(path)) return response(descriptor);
    if (!isSpans(path))
      return response({
        items: Array.from({ length: 5 }, (_, index) => ({
          ...trace(),
          id: `root-${index}`,
          trace_id: `trace-${index}`,
        })),
        next_cursor: null,
      });
    if (options.params.path.trace_id === "trace-0")
      throw new ApiError(404, "trace_not_found", "Access revoked", {}, null);
    await new Promise<void>((resolve) => pending.push(resolve));
    return response({ items: [], next_cursor: "more" });
  });
  const cache = mount(<TracesPage />);
  try {
    await screen.findByText("Access revoked");
    expect(screen.queryByRole("table")).toBeNull();
    expect(pending).toHaveLength(3);
  } finally {
    pending.forEach((resolve) => resolve());
  }
  await waitFor(() => expect(cache.isFetching()).toBe(0));
  expect(http.GET.mock.calls.filter(([path]) => isSpans(path))).toHaveLength(4);
});

it("requests whole-range sorting and resets pagination when the sort changes", async () => {
  const user = userEvent.setup();
  const root = (name: string) => ({
    ...trace(),
    id: name,
    trace_id: name,
    name,
  });
  http.GET.mockImplementation(async (path, options) => {
    if (isBackend(path)) return response(descriptor);
    if (isSpans(path)) return response({ items: [], next_cursor: null });
    const { cursor, order_by, direction } = options.params.query;
    if (path === "/api/v1/traces/search")
      return response({
        items: [
          {
            root: root(cursor ? "second" : `${order_by}-${direction}`),
            cost_usd: "0.25",
            duration_seconds: "10",
          },
        ],
        total: 2,
        collected_at: "2026-09-11T00:00:00Z",
        next_cursor: cursor ? null : "ranked-next",
      });
    return response({ items: [root("latest")], next_cursor: "next" });
  });
  mount(<TracesPage />, "/", 15_000);
  await screen.findByRole("link", { name: "latest" });
  await user.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() =>
    expect(
      http.GET.mock.calls.some(
        ([, opts]) => opts.params?.query?.cursor === "next",
      ),
    ).toBe(true),
  );
  await user.click(screen.getByRole("button", { name: "Duration" }));
  await screen.findByRole("link", { name: "duration-desc" });
  expect(http.GET).toHaveBeenCalledWith(
    "/api/v1/traces/search",
    expect.objectContaining({
      params: {
        query: expect.objectContaining({
          order_by: "duration",
          direction: "desc",
          cursor: undefined,
        }),
      },
      signal: expect.any(AbortSignal),
    }),
  );
  await user.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByRole("link", { name: "second" });
  const rankedCalls = http.GET.mock.calls.filter(
    ([path]) => path === "/api/v1/traces/search",
  ).length;
  await user.click(screen.getByRole("button", { name: "Previous" }));
  await screen.findByRole("link", { name: "duration-desc" });
  await waitFor(() =>
    expect(
      http.GET.mock.calls.filter(([path]) => path === "/api/v1/traces/search"),
    ).toHaveLength(rankedCalls + 1),
  );
  await user.click(screen.getByRole("button", { name: "Cost" }));
  await screen.findByRole("link", { name: "cost-desc" });
  await user.click(screen.getByRole("button", { name: "Cost" }));
  await screen.findByRole("link", { name: "cost-asc" });
  const searches = http.GET.mock.calls.filter(
    ([path]) => path === "/api/v1/traces/search",
  );
  expect(searches[0][1].params.query.snapshot).toBeUndefined();
  expect(
    searches
      .slice(1)
      .every(([, options]) => options.params.query.snapshot === undefined),
  ).toBe(true);
  expect(
    screen
      .getByRole("columnheader", { name: "Cost" })
      .getAttribute("aria-sort"),
  ).toBe("ascending");
  expect(screen.getByText("$0.25")).toBeTruthy();
});

it("applies duration and exact USD bounds together and validates the draft", async () => {
  const user = userEvent.setup();
  http.GET.mockImplementation(async (path) =>
    response(
      isBackend(path) ? descriptor : { items: [], next_cursor: null, total: 0 },
    ),
  );
  mount(<TracesPage />);
  await screen.findByText("No traces in this range");
  await user.click(
    screen.getByRole("button", { name: "Filter by duration or cost" }),
  );
  await user.type(
    screen.getByRole("spinbutton", { name: "Duration Minimum" }),
    "60",
  );
  await user.type(
    screen.getByRole("spinbutton", { name: "Duration Maximum" }),
    "30",
  );
  await user.click(screen.getByRole("button", { name: "Apply" }));
  expect(screen.getByRole("alert").textContent).toBe(
    "Minimum must not exceed maximum.",
  );
  await user.clear(
    screen.getByRole("spinbutton", { name: "Duration Maximum" }),
  );
  await user.type(
    screen.getByRole("spinbutton", { name: "Duration Maximum" }),
    "120",
  );
  await user.type(
    screen.getByRole("spinbutton", { name: "Cost Minimum" }),
    "0.0012",
  );
  expect(
    http.GET.mock.calls.some(([path]) => path === "/api/v1/traces/search"),
  ).toBe(false);
  await user.click(screen.getByRole("button", { name: "Apply" }));
  await waitFor(() =>
    expect(http.GET).toHaveBeenCalledWith(
      "/api/v1/traces/search",
      expect.objectContaining({
        params: {
          query: expect.objectContaining({
            min_duration_seconds: "60",
            max_duration_seconds: "120",
            min_cost_usd: "0.0012",
            cursor: undefined,
          }),
        },
        signal: expect.any(AbortSignal),
      }),
    ),
  );
  await user.click(
    screen.getByRole("button", { name: /^Remove filter Duration:/ }),
  );
  await waitFor(() =>
    expect(http.GET).toHaveBeenLastCalledWith(
      "/api/v1/traces/search",
      expect.objectContaining({
        params: {
          query: expect.objectContaining({
            min_duration_seconds: undefined,
            max_duration_seconds: undefined,
            min_cost_usd: "0.0012",
          }),
        },
      }),
    ),
  );
  await user.click(
    screen.getByRole("button", { name: "Filter by duration or cost" }),
  );
  await user.type(
    screen.getByRole("spinbutton", { name: "Duration Minimum" }),
    "90",
  );
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(
    screen.queryByRole("button", { name: /^Remove filter Duration:/ }),
  ).toBeNull();
});

it("restarts the whole-range query when its cursor is rejected", async () => {
  const user = userEvent.setup();
  let searches = 0;
  http.GET.mockImplementation(async (path, options) => {
    if (isBackend(path)) return response(descriptor);
    if (isSpans(path)) return response({ items: [], next_cursor: null });
    if (path === "/api/v1/traces/search") {
      if (options.params.query.cursor)
        throw new ApiError(
          400,
          "invalid_cursor",
          "Invalid trace search cursor",
          {},
          null,
        );
      expect(options.params.query.snapshot).toBeUndefined();
      searches++;
      return response({
        items: [
          {
            root: { ...trace(), name: `search-${searches}` },
            cost_usd: "0",
            duration_seconds: "10",
          },
        ],
        total: 2,
        next_cursor: "invalid",
      });
    }
    return response({ items: [trace()], next_cursor: null });
  });
  mount(<TracesPage />);
  await screen.findByRole("button", { name: "Duration" });
  await user.click(screen.getByRole("button", { name: "Duration" }));
  await screen.findByRole("link", { name: "search-1" });
  await user.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByText("Invalid trace search cursor");
  await user.click(screen.getByRole("button", { name: "Reload" }));
  await screen.findByRole("link", { name: "search-2" });
});

it("shows a placeholder while costs load, then one decimal count for the page", async () => {
  let release = () => {};
  const loaded = new Promise<void>((resolve) => (release = resolve));
  const makeTrace = (id: string) => ({
    ...trace(),
    id: `${id}-span`,
    trace_id: id,
    name: id,
  });
  http.GET.mockImplementation(async (path, options) => {
    if (isBackend(path)) return response(descriptor);
    if (isSpans(path)) {
      await loaded;
      const id = options.params.path.trace_id;
      return overviewResponse([
        { ...makeTrace(id), cost_usd: id === "small" ? "0.0035" : "0.03" },
      ]);
    }
    return response({
      items: [makeTrace("small"), makeTrace("large")],
      next_cursor: null,
    });
  });
  mount(<TracesPage />);
  await screen.findByRole("link", { name: "small" });
  const costs = () =>
    screen
      .getAllByRole("row")
      .slice(1)
      .map((row) => within(row).getAllByRole("cell")[4]);
  expect(
    costs().every((cell) => cell.querySelector('[aria-busy="true"]')),
  ).toBe(true);
  release();
  await screen.findByText("$0.0035");
  expect(costs().map((cell) => cell.textContent)).toEqual([
    "$0.0035",
    "$0.0300",
  ]);
});

it("opens the root content tab without substituting child output", async () => {
  const user = userEvent.setup();
  const child = {
    ...observation("child", "root"),
    output: "Child only output",
  };
  http.GET.mockImplementation(async (path) =>
    response(
      isBackend(path)
        ? descriptor
        : isSpan(path)
          ? child
          : isSpans(path)
            ? {
                items: [overview(child)],
                next_cursor: null,
              }
            : { ...trace(), input: "Root input" },
    ),
  );
  mount(<TraceDetail traceId="trace-1" />, "/?tab=content");
  await screen.findByText("Root input");
  expect(
    screen
      .getByRole("tab", { name: "Input & output" })
      .getAttribute("aria-selected"),
  ).toBe("true");
  expect(screen.queryByText("Child only output")).toBeNull();
  await user.click(screen.getByRole("tab", { name: "Observations" }));
  await user.click(await screen.findByRole("treeitem", { name: /^child/ }));
  const childPanel = within(await screen.findByRole("complementary"));
  expect(await childPanel.findByText("Child only output")).toBeTruthy();
  expect(childPanel.getByRole("heading", { name: "Output" })).toBeTruthy();
});
