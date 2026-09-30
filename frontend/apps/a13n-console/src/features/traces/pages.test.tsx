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

const http = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspaces/test",
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
function mount(element: React.ReactNode, entry = "/") {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
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

it("applies metadata key=value filters from the popover", async () => {
  const user = typingUser();
  http.GET.mockImplementation(async (path) =>
    isSpans(path)
      ? response({ items: [], next_cursor: null })
      : response(
          isBackend(path)
            ? descriptor
            : { items: [trace()], next_cursor: null },
        ),
  );
  mount(<TracesPage />);
  await screen.findByRole("link", { name: /root/ }, { timeout: 3000 });
  await user.click(screen.getByRole("button", { name: "Metadata" }));
  await user.type(
    screen.getByRole("textbox", { name: "Metadata key 1" }),
    "scenario",
  );
  await user.type(
    screen.getByRole("textbox", { name: "Metadata value 1" }),
    "review",
  );
  await user.click(screen.getByRole("button", { name: "Add filter" }));
  await user.type(
    screen.getByRole("textbox", { name: "Metadata key 2" }),
    "synthetic",
  );
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
    if (!isSpans(path)) return response(trace());
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
  expect(panel.getByText("Diagnostic reason")).toBeTruthy();
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
    return response({
      items: [observation("private-child")],
      next_cursor: "next",
    });
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
      return response({
        items: [root, { ...observation("chat", "root"), cost_usd: "0.2" }],
        next_cursor: "next",
      });
    if (fail) {
      fail = false;
      throw new Error("Cost page failed");
    }
    return response({
      items: [
        { ...observation("chat", "root"), cost_usd: "0.2" },
        { ...observation("later", "root"), cost_usd: "0.05" },
      ],
      next_cursor: null,
    });
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
    expect(options.params.query).toMatchObject({ limit: 100 });
    if (!options.params.query.cursor)
      return response({
        items: [root, { ...observation("chat"), cost_usd: "0.2" }],
        next_cursor: "empty",
      });
    if (options.params.query.cursor === "empty")
      return response({ items: [], next_cursor: "last" });
    return response({
      items: [
        { ...observation("chat"), cost_usd: "0.2" },
        { ...observation("tool"), cost_usd: "0.05" },
      ],
      next_cursor: null,
    });
  });
  mount(<TracesPage />);
  await screen.findByText("$0.35");
  expect(screen.getByText("2.5 s")).toBeTruthy();
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
      return response({ items: [root], next_cursor: "same" });
    if (options.params.query.cursor) throw new Error("Cost read failed");
    return response({ items: [root], next_cursor: "next" });
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
    return response({
      items: [{ ...observation(`part-${reads}`), cost_usd: "1" }],
      next_cursor: `cursor-${reads}`,
    });
  });
  const cache = mount(<TracesPage />);
  await screen.findByRole("columnheader", { name: "Cost" });
  await waitFor(() => expect(cache.isFetching()).toBe(0));
  expect(reads).toBe(20);
  expect(
    within(screen.getAllByRole("row")[1]).getAllByRole("cell")[4].textContent,
  ).toBe(UNKNOWN);
});

it("uses seconds in the detail overview, timeline and observation dialog", async () => {
  const user = userEvent.setup();
  const root = { ...trace(), ended_at: "2026-09-11T00:00:02.5Z" };
  http.GET.mockImplementation(async (path) =>
    response(
      isBackend(path)
        ? descriptor
        : isSpans(path)
          ? { items: [root], next_cursor: null }
          : root,
    ),
  );
  mount(<TraceDetail traceId="trace-1" />);
  await screen.findByRole("treeitem", { name: /^root/ });
  expect(screen.getByText("0 s").parentElement?.textContent).toBe("0 s2.5 s");
  expect(
    within(screen.getByRole("treeitem", { name: /^root/ })).getByText("2.5 s"),
  ).toBeTruthy();
  expect(screen.queryByText("Root status")).toBeNull();
  await user.click(screen.getByRole("treeitem", { name: /^root/ }));
  const panel = within(await screen.findByRole("complementary"));
  expect(panel.getByText("2.5 s")).toBeTruthy();
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

it("sorts aggregate costs rather than root costs and keeps the order after pagination", async () => {
  const user = userEvent.setup();
  const makeTrace = (id: string, cost: string | null) => ({
    ...trace(),
    id: `${id}-span`,
    trace_id: id,
    name: id,
    cost_usd: cost,
  });
  http.GET.mockImplementation(async (path, options) => {
    if (isBackend(path)) return response(descriptor);
    if (isSpans(path))
      return response({
        items: [
          {
            ...observation("child"),
            cost_usd: options.params.path.trace_id === "low-root" ? "10" : null,
          },
        ],
        next_cursor: null,
      });
    const next = options.params.query.cursor;
    return response({
      items: next
        ? [makeTrace("next-low", "1"), makeTrace("next-high", "2")]
        : [
            makeTrace("high-root", "5"),
            makeTrace("low-root", "1"),
            makeTrace("unknown", null),
          ],
      next_cursor: next ? null : "next",
    });
  });
  mount(<TracesPage />);
  await screen.findByText("$11.00");
  const names = () =>
    screen
      .getAllByRole("row")
      .slice(1)
      .map((row) => within(row).getAllByRole("link")[0].textContent);
  await user.click(screen.getByRole("button", { name: "Cost" }));
  expect(names()).toEqual(["low-root", "high-root", "unknown"]);
  await user.click(screen.getByRole("button", { name: "Cost" }));
  expect(names()).toEqual(["high-root", "low-root", "unknown"]);
  await user.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByText("$2.00");
  expect(names()).toEqual(["next-low", "next-high"]);
  expect(
    screen
      .getByRole("columnheader", { name: "Cost" })
      .getAttribute("aria-sort"),
  ).toBe("ascending");
});

it("opens the root content tab without substituting child output", async () => {
  const user = userEvent.setup();
  http.GET.mockImplementation(async (path) =>
    response(
      isBackend(path)
        ? descriptor
        : isSpans(path)
          ? {
              items: [
                {
                  ...observation("child", "root"),
                  output: "Child only output",
                },
              ],
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
  expect(childPanel.getByRole("heading", { name: "Output" })).toBeTruthy();
  expect(childPanel.getByText("Child only output")).toBeTruthy();
});
