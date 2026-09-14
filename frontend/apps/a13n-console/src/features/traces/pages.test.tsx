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
});
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
    path.endsWith("observations")
      ? response({ items: [], next_cursor: null })
      : response(
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
  expect(await screen.findByRole("option", { name: "Output" })).toBeTruthy();
  expect(screen.queryByRole("option", { name: "Input and output" })).toBeNull();
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
  expect(screen.getByText("Loaded cost")).toBeTruthy();
  expect(
    screen.getByRole("link", { name: "View run" }).getAttribute("href"),
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
  await user.click(within(dialog).getByRole("tab", { name: "Metadata" }));
  expect(within(dialog).getByText("Diagnostic reason")).toBeTruthy();
  expect(within(dialog).getByText("Requested model")).toBeTruthy();
  expect(within(dialog).getByText("model-alias")).toBeTruthy();
  expect(within(dialog).getByText("model-version")).toBeTruthy();
  expect(within(dialog).getByText("Error")).toBeTruthy();
  expect(within(dialog).getByText("Telemetry status")).toBeTruthy();
  expect(within(dialog).getAllByText("-").length).toBeGreaterThan(0);
  expect(
    within(dialog).getByRole("button", { name: "Resource attributes" }),
  ).toBeTruthy();
});

it.each(["langfuse", "logfire"] as const)(
  "keeps Run navigation local and names the external %s destination",
  async (provider) => {
    http.GET.mockImplementation(async (path) =>
      response(
        path.endsWith("observations")
          ? { items: [], next_cursor: null }
          : { ...trace(), provider },
      ),
    );
    mount(<TraceDetail traceId="trace-1" />);
    const run = await screen.findByRole("link", { name: "View run" });
    expect(run.getAttribute("href")).toBe(
      "/workspaces/test/sessions/session/threads/thread/runs/run",
    );
    expect(run.getAttribute("target")).toBeNull();
    const backend = screen.getByRole("link", {
      name: `View in ${provider === "langfuse" ? "Langfuse" : "Logfire"}`,
    });
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
        path.endsWith("observations")
          ? { items: [], next_cursor: null }
          : { ...trace(), source_url },
      ),
    );
    mount(<TraceDetail traceId="trace-1" />);
    await screen.findByRole("link", { name: "View run" });
    expect(screen.queryByRole("link", { name: /View in/ })).toBeNull();
  },
);

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
  expect(screen.queryByRole("link", { name: "View run" })).toBeNull();
});

it("shows a loaded cost subtotal until pagination succeeds, without counting the root twice", async () => {
  const user = userEvent.setup();
  const root = { ...observation("root"), cost_usd: "0.1" };
  let fail = true;
  http.GET.mockImplementation(async (path, options) => {
    if (!path.endsWith("observations")) return response({ ...trace(), root });
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
  await screen.findByText("$0.3");
  expect(screen.getByText("Loaded cost")).toBeTruthy();
  expect(screen.queryByText("Cost")).toBeNull();
  await user.click(
    screen.getByRole("button", { name: "Load more observations" }),
  );
  await screen.findByText("Cost page failed");
  expect(screen.getByText("Loaded cost")).toBeTruthy();
  expect(screen.getByText("$0.3")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Reload" }));
  await screen.findByText("Cost");
  expect(screen.getByText("$0.35")).toBeTruthy();
});

it("shows full root previews, seconds, normalized levels and paginated aggregate cost in the list", async () => {
  const root = {
    ...observation("root"),
    level: "default",
    cost_usd: "0.1",
    ended_at: "2026-09-11T00:00:02.5Z",
    input: {
      media_type: null,
      value: { content: [{ type: "text", text: "Input preview" }] },
    },
    output: { media_type: null, value: "Output preview" },
  };
  http.GET.mockImplementation(async (path, options) => {
    if (path.endsWith("trace-query")) return response(descriptor);
    if (!path.endsWith("observations"))
      return response({ items: [{ ...trace(), root }], next_cursor: null });
    expect(options.params.query).toMatchObject({ view: "compact", limit: 100 });
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
  expect(screen.getByText("Input preview")).toBeTruthy();
  expect(screen.getByText("Output preview")).toBeTruthy();
  expect(screen.getByText("2.5 s")).toBeTruthy();
  expect(screen.getByText("Info")).toBeTruthy();
  expect(
    screen.getAllByRole("columnheader").map((cell) => cell.textContent?.trim()),
  ).toEqual(["Trace", "Started", "Duration", "Cost", "Input", "Output"]);
  expect(
    screen.getByRole("link", { name: "Input preview" }).getAttribute("href"),
  ).toContain("?tab=content");
  expect(screen.queryByText("Root status")).toBeNull();
  expect(screen.queryByText("Severity")).toBeNull();
  expect(screen.queryByText(/USD|Unavailable/)).toBeNull();
  expect(http.GET).toHaveBeenCalledWith(
    "/api/v1/workspaces/{workspace}/traces",
    expect.objectContaining({
      params: expect.objectContaining({
        query: expect.objectContaining({ view: "full", limit: 25 }),
      }),
    }),
  );
});

it("does not expose root-only or partial list costs when a later page fails or repeats", async () => {
  const root = { ...observation("root"), input: null, cost_usd: "9" };
  http.GET.mockImplementation(async (path, options) => {
    if (path.endsWith("trace-query")) return response(descriptor);
    if (!path.endsWith("observations"))
      return response({
        items: [
          { ...trace(), root },
          { ...trace(), id: "repeated", root },
        ],
        next_cursor: null,
      });
    if (options.params.path.trace_id === "repeated")
      return response({ items: [root], next_cursor: "same" });
    if (options.params.query.cursor) throw new Error("Cost read failed");
    return response({ items: [root], next_cursor: "next" });
  });
  const cache = mount(<TracesPage />);
  await screen.findByRole("columnheader", { name: "Input" });
  await waitFor(() => expect(cache.isFetching()).toBe(0));
  expect(screen.queryByText("$9")).toBeNull();
  for (const row of screen.getAllByRole("row").slice(1)) {
    const cells = within(row).getAllByRole("cell");
    expect(cells[2].textContent).toBe("-");
    expect(cells[3].textContent).toBe("-");
  }
  expect(
    http.GET.mock.calls.filter(([path]) => path.endsWith("observations")),
  ).toHaveLength(4);
});

it("hides list previews after a cost read reports revoked access", async () => {
  http.GET.mockImplementation(async (path) => {
    if (path.endsWith("trace-query")) return response(descriptor);
    if (path.endsWith("observations"))
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
    if (path.endsWith("trace-query")) return response(descriptor);
    if (!path.endsWith("observations"))
      return response({
        items: Array.from({ length: 6 }, (_, index) => ({
          ...trace(),
          id: `trace-${index}`,
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
    if (path.endsWith("trace-query")) return response(descriptor);
    if (!path.endsWith("observations"))
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
    within(screen.getAllByRole("row")[1]).getAllByRole("cell")[3].textContent,
  ).toBe("-");
});

it("uses seconds in the detail overview, timeline and observation dialog", async () => {
  const user = userEvent.setup();
  const root = { ...observation("root"), ended_at: "2026-09-11T00:00:02.5Z" };
  http.GET.mockImplementation(async (path) =>
    response(
      path.endsWith("observations")
        ? { items: [root], next_cursor: null }
        : { ...trace(), root },
    ),
  );
  mount(<TraceDetail traceId="trace-1" />);
  await screen.findByRole("button", { name: /^root/ });
  expect(screen.getByText("0 s").parentElement?.textContent).toBe("0 s2.5 s");
  expect(
    within(screen.getByRole("button", { name: /^root/ })).getByText("2.5 s"),
  ).toBeTruthy();
  expect(screen.queryByText("Root status")).toBeNull();
  await user.click(screen.getByRole("button", { name: /^root/ }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText("2.5 s")).toBeTruthy();
  expect(within(dialog).queryByText(/\d ms\b/)).toBeNull();
});

it("immediately hides revoked content without waiting for unrelated cost reads or starting more pages", async () => {
  const pending: (() => void)[] = [];
  http.GET.mockImplementation(async (path, options) => {
    if (path.endsWith("trace-query")) return response(descriptor);
    if (!path.endsWith("observations"))
      return response({
        items: Array.from({ length: 5 }, (_, index) => ({
          ...trace(),
          id: `trace-${index}`,
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
  expect(
    http.GET.mock.calls.filter(([path]) => path.endsWith("observations")),
  ).toHaveLength(4);
});

it("sorts aggregate costs rather than root costs and keeps the order after pagination and view changes", async () => {
  const user = userEvent.setup();
  const makeTrace = (id: string, cost: string | null) => ({
    ...trace(),
    id,
    root: { ...observation(id), cost_usd: cost },
  });
  http.GET.mockImplementation(async (path, options) => {
    if (path.endsWith("trace-query")) return response(descriptor);
    if (path.endsWith("observations"))
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
  const cache = mount(<TracesPage />);
  await screen.findByText("$11");
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
  await screen.findByText("$2");
  expect(names()).toEqual(["next-low", "next-high"]);
  expect(
    screen
      .getByRole("columnheader", { name: "Cost" })
      .getAttribute("aria-sort"),
  ).toBe("ascending");
  await user.click(screen.getByRole("combobox", { name: "Content" }));
  await user.click(await screen.findByRole("option", { name: "Compact" }));
  await screen.findByRole("link", { name: "high-root" });
  await waitFor(() => expect(cache.isFetching()).toBe(0));
  expect(names()).toEqual(["high-root", "low-root", "unknown"]);
  expect(screen.queryByRole("columnheader", { name: "Input" })).toBeNull();
  expect(http.GET).toHaveBeenCalledWith(
    expect.stringContaining("/traces"),
    expect.objectContaining({
      params: expect.objectContaining({
        query: expect.objectContaining({ view: "compact", cursor: undefined }),
      }),
    }),
  );
});

it("opens the root content tab from preview links without substituting child output", async () => {
  const user = userEvent.setup();
  http.GET.mockImplementation(async (path) =>
    response(
      path.endsWith("observations")
        ? {
            items: [
              {
                ...observation("child", "root"),
                output: {
                  media_type: "text/plain",
                  value: "Child only output",
                },
              },
            ],
            next_cursor: null,
          }
        : {
            ...trace(),
            root: {
              ...observation("root"),
              input: { media_type: "text/plain", value: "Root input" },
            },
          },
    ),
  );
  mount(<TraceDetail traceId="trace-1" />, "/?tab=content");
  await screen.findByText("Root input");
  expect(
    screen
      .getByRole("tab", { name: "Input and output" })
      .getAttribute("aria-selected"),
  ).toBe("true");
  expect(screen.queryByText("Child only output")).toBeNull();
  await user.click(screen.getByRole("tab", { name: "Observations" }));
  await user.click(await screen.findByRole("button", { name: /^child/ }));
  const childDialog = within(await screen.findByRole("dialog"));
  await user.click(childDialog.getByRole("tab", { name: "Output" }));
  expect(childDialog.getByText("Child only output")).toBeTruthy();
});
