import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { Link, MemoryRouter, Route, Routes, useNavigate } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";
import { ApiError } from "../../service-client";
import { DashboardsPage } from "./page";
import { usePanelData } from "./queries";
import type { Dashboard } from "./api";
const state = vi.hoisted(() => ({
  GET: vi.fn(),
  PATCH: vi.fn(),
  POST: vi.fn(),
  DELETE: vi.fn(),
  write: true,
  admin: true,
  width: 1200,
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ workspace: () => state }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/ws_test",
    can: (verb: string) =>
      verb === "admin" ? state.admin : verb === "write" ? state.write : true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      String(values?.defaultValue ?? key).replace(
        /\{\{(\w+)\}\}/g,
        (match, name: string) =>
          values?.[name] === undefined ? match : String(values[name]),
      ),
    i18n: { resolvedLanguage: "en" },
  }),
}));
// The calendar field has its own tests; here local values are typed directly.
vi.mock("../../shared/forms", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../shared/forms")>()),
  DateTimeField: ({
    label,
    value,
    onValueChange,
  }: {
    label: string;
    value: string;
    onValueChange: (value: string) => void;
  }) => (
    <input
      aria-label={label}
      value={value}
      onChange={(event) => onValueChange(event.target.value)}
    />
  ),
}));
vi.mock("./chart", () => ({ MetricsChart: () => null }));
vi.mock("react-grid-layout", () => ({
  default: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  useContainerWidth: () => ({
    width: state.width,
    mounted: true,
    containerRef: { current: null },
  }),
}));
const dashboard: Dashboard = {
  id: "dsh_test",
  name: "Overview",
  builtin: true,
  version: 1,
  organization_id: "org_test",
  workspace_id: "ws_test",
  created_by_id: "usr_test",
  updated_by_id: "usr_test",
  created_at: "2026-10-19T00:00:00Z",
  updated_at: "2026-10-19T00:00:00Z",
  config: {
    refresh_seconds: 0,
    panels: [
      {
        id: "cpu",
        title: "CPU",
        position: { x: 0, y: 0, w: 6, h: 5 },
        queries: [{ kind: "metric", metric: "system_cpu_busy_ratio" }],
      },
    ],
  },
};
async function setRange(
  user: ReturnType<typeof userEvent.setup>,
  start: string,
  end: string,
) {
  await user.click(screen.getByRole("button", { name: /Time range:/ }));
  await user.clear(screen.getByLabelText("Start time"));
  await user.type(screen.getByLabelText("Start time"), start);
  await user.clear(screen.getByLabelText("End time"));
  await user.type(screen.getByLabelText("End time"), end);
  await user.click(screen.getByRole("button", { name: "Apply" }));
}
beforeEach(() => {
  vi.clearAllMocks();
  state.write = true;
  state.admin = true;
  state.width = 1200;
  state.GET.mockImplementation(async (path: string) => ({
    data:
      path === "/api/v1/metrics-dashboards"
        ? { items: [structuredClone(dashboard)], next_cursor: null }
        : path === "/api/v1/metrics-dashboards/preset"
          ? dashboard.config
          : { items: [], next_cursor: null },
    response: new Response(),
  }));
  state.PATCH.mockResolvedValue({ data: dashboard, response: new Response() });
});
function setup() {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <DashboardsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { cache, user: userEvent.setup() };
}
it("stages panel changes, saves the opening ETag and preserves drafts on conflict", async () => {
  const { cache, user } = setup();
  await user.click(
    await screen.findByRole("button", { name: "Edit dashboard" }),
  );
  await user.clear(screen.getByRole("textbox", { name: "Dashboard name" }));
  await user.type(
    screen.getByRole("textbox", { name: "Dashboard name" }),
    "My overview",
  );
  cache.setQueryData(
    ["metrics-dashboards", "ws_test"],
    [{ ...dashboard, version: 2, name: "Other author" }],
  );
  state.PATCH.mockRejectedValueOnce(
    new ApiError(412, "precondition_failed", "Changed", {}, null),
  );
  await user.click(screen.getByRole("button", { name: "Save dashboard" }));
  await screen.findByText("Dashboard changed");
  expect(state.PATCH.mock.calls[0][1].headers).toEqual({
    "If-Match": '"dsh_test:1"',
  });
  expect(state.PATCH.mock.calls[0][1].body.name).toBe("My overview");
  expect(
    (
      screen.getByRole("textbox", {
        name: "Dashboard name",
      }) as HTMLInputElement
    ).value,
  ).toBe("My overview");
  await user.click(screen.getByRole("button", { name: "Discard" }));
  expect(screen.queryByRole("button", { name: "Save dashboard" })).toBeNull();
});
it("discards edited time defaults without leaking them into a later name-only save", async () => {
  const { cache, user } = setup();
  await screen.findByText("Select a target to explore metrics");
  cache.setQueryData(
    ["metrics-dashboards", "ws_test"],
    [
      {
        ...dashboard,
        config: {
          ...dashboard.config,
          range_seconds: 3600,
          refresh_seconds: 30,
        },
      },
    ],
  );
  await user.click(screen.getByRole("button", { name: "Edit dashboard" }));
  await user.click(screen.getByRole("button", { name: /Time range:/ }));
  await user.click(screen.getByRole("button", { name: "Last 15 minutes" }));
  await user.click(screen.getByRole("combobox", { name: "Auto refresh" }));
  await user.click(await screen.findByRole("option", { name: "Off" }));
  await setRange(user, "2026-10-19T10:00", "2026-10-19T12:00");
  expect(screen.getByRole("button", { name: "Back to live" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Discard" }));
  expect(screen.queryByRole("button", { name: "Back to live" })).toBeNull();
  expect(
    screen.getByRole("button", { name: /Time range:/ }).textContent,
  ).toContain("Last hour");
  expect(
    screen.getByRole("combobox", { name: "Auto refresh" }).textContent,
  ).toContain("30 seconds");
  await user.click(screen.getByRole("button", { name: "Edit dashboard" }));
  await user.type(
    screen.getByRole("textbox", { name: "Dashboard name" }),
    " renamed",
  );
  await user.click(screen.getByRole("button", { name: "Save dashboard" }));
  await waitFor(() => expect(state.PATCH).toHaveBeenCalledOnce());
  expect(state.PATCH.mock.calls[0][1].body.config).toMatchObject({
    range_seconds: 3600,
    refresh_seconds: 30,
  });
}, 15000);

it("keeps viewer time overrides separate from saved dashboard defaults", async () => {
  const { user } = setup();
  await screen.findByText("Select a target to explore metrics");
  await user.click(screen.getByRole("button", { name: /Time range:/ }));
  await user.click(screen.getByRole("button", { name: "Last 6 hours" }));
  await user.click(screen.getByRole("button", { name: "Edit dashboard" }));
  await user.click(screen.getByRole("button", { name: "Save dashboard" }));
  await waitFor(() => expect(state.PATCH).toHaveBeenCalledOnce());
  expect(state.PATCH.mock.calls[0][1].body.config).toEqual(dashboard.config);
});

it("applies panel editing to the draft and discards without a server write", async () => {
  const { user } = setup();
  await user.click(
    await screen.findByRole("button", { name: "Edit dashboard" }),
  );
  await user.click(screen.getByRole("button", { name: "Add panel" }));
  const dialog = await screen.findByRole("dialog", { name: "Add panel" });
  const title = within(dialog).getByRole("textbox", { name: "Title" });
  await user.clear(title);
  await user.type(title, "Extra CPU");
  await user.click(within(dialog).getByRole("button", { name: "Add panel" }));
  await screen.findByRole("heading", { name: "Extra CPU" });
  expect(state.PATCH).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Discard" }));
  expect(screen.queryByRole("heading", { name: "Extra CPU" })).toBeNull();
});
it("does not expose writes to a reader or execute saved PromQL for a non-admin", async () => {
  state.write = false;
  state.admin = false;
  state.GET.mockImplementation(async (path: string) => ({
    data:
      path === "/api/v1/metrics-dashboards"
        ? {
            items: [
              {
                ...dashboard,
                config: {
                  panels: [
                    {
                      ...dashboard.config.panels![0],
                      queries: [
                        {
                          kind: "promql",
                          source_id: "mds_x",
                          expression: "up",
                        },
                      ],
                    },
                  ],
                },
              },
            ],
            next_cursor: null,
          }
        : { items: [], next_cursor: null },
    response: new Response(),
  }));
  setup();
  await screen.findByRole("heading", { name: "CPU" });
  expect(screen.queryByRole("button", { name: "Edit dashboard" })).toBeNull();
  expect(state.POST).not.toHaveBeenCalled();
});
it("deduplicates identical queries and aborts the request when its panels disappear", async () => {
  state.GET.mockImplementation(async (path: string) => ({
    data:
      path === "/api/v1/metrics-dashboards"
        ? {
            items: [
              {
                ...dashboard,
                config: {
                  refresh_seconds: 0,
                  panels: [
                    {
                      ...dashboard.config.panels![0],
                      queries: [
                        {
                          kind: "promql",
                          source_id: "mds_x",
                          expression: "up",
                        },
                      ],
                    },
                    {
                      ...dashboard.config.panels![0],
                      id: "copy",
                      queries: [
                        {
                          kind: "promql",
                          source_id: "mds_x",
                          expression: "up",
                        },
                      ],
                    },
                  ],
                },
              },
            ],
            next_cursor: null,
          }
        : { items: [], next_cursor: null },
    response: new Response(),
  }));
  let signal: AbortSignal | undefined;
  state.POST.mockImplementation(
    (_path: string, args: { signal: AbortSignal }) => {
      signal = args.signal;
      return new Promise((_resolve, reject) =>
        args.signal.addEventListener("abort", () => reject(args.signal.reason)),
      );
    },
  );
  const { user } = setup();
  await waitFor(() => expect(state.POST).toHaveBeenCalledTimes(1));
  await user.click(screen.getByRole("button", { name: "Edit dashboard" }));
  await user.click(screen.getAllByRole("button", { name: "Remove" })[0]);
  expect(signal!.aborted).toBe(false);
  await user.click(screen.getByRole("button", { name: "Remove" }));
  await waitFor(() => expect(signal!.aborted).toBe(true));
});

it("freezes the dashboard while saving and while restoring its preset", async () => {
  let complete!: () => void;
  state.PATCH.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        complete = () => resolve({ data: dashboard, response: new Response() });
      }),
  );
  const { user } = setup();
  await user.click(
    await screen.findByRole("button", { name: "Edit dashboard" }),
  );
  await user.click(screen.getByRole("button", { name: "Save dashboard" }));
  await waitFor(() => expect(state.PATCH).toHaveBeenCalled());
  expect(
    (
      screen.getByRole("textbox", {
        name: "Dashboard name",
      }) as HTMLInputElement
    ).disabled,
  ).toBe(true);
  expect(
    (
      screen.getByRole("button", {
        name: "Restore defaults",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  complete();
  await screen.findByRole("button", { name: "Edit dashboard" });
  state.GET.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        complete = () =>
          resolve({ data: dashboard.config, response: new Response() });
      }),
  );
  await user.click(screen.getByRole("button", { name: "Edit dashboard" }));
  await user.click(screen.getByRole("button", { name: "Restore defaults" }));
  expect(
    (
      screen.getByRole("textbox", {
        name: "Dashboard name",
      }) as HTMLInputElement
    ).disabled,
  ).toBe(true);
  expect(
    (screen.getByRole("button", { name: "Discard" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  complete();
  await waitFor(() =>
    expect(
      (
        screen.getByRole("textbox", {
          name: "Dashboard name",
        }) as HTMLInputElement
      ).disabled,
    ).toBe(false),
  );
});

function HistoryControls() {
  const navigate = useNavigate();
  return (
    <>
      <button onClick={() => navigate(-1)}>History back</button>
      <button onClick={() => navigate(1)}>History forward</button>
    </>
  );
}
it("retains separate drafts across history and page navigation with the original save identity", async () => {
  const other = {
    ...dashboard,
    id: "dsh_other",
    name: "Other dashboard",
    builtin: false,
  };
  const base = "/workspace/ws_test/monitoring";
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  cache.setQueryData(["metrics-dashboards", "ws_test"], [dashboard, other]);
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter
        initialEntries={[
          `${base}?dashboard=dsh_test`,
          `${base}?dashboard=dsh_other`,
        ]}
      >
        <HistoryControls />
        <Routes>
          <Route path={base} element={<DashboardsPage />} />
          <Route
            path={`${base}/sources`}
            element={
              <Link to={`${base}?dashboard=dsh_other`}>Return to draft</Link>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await user.click(
    await screen.findByRole("button", { name: "Edit dashboard" }),
  );
  await user.clear(screen.getByRole("textbox", { name: "Dashboard name" }));
  await user.type(
    screen.getByRole("textbox", { name: "Dashboard name" }),
    "Retained draft",
  );
  await user.click(screen.getByRole("button", { name: "History back" }));
  expect(screen.queryByRole("textbox", { name: "Dashboard name" })).toBeNull();
  expect(
    screen.getByRole("combobox", { name: "Dashboard" }).textContent,
  ).toContain("Overview");
  await user.click(screen.getByRole("button", { name: "History forward" }));
  expect(
    (
      (await screen.findByRole("textbox", {
        name: "Dashboard name",
      })) as HTMLInputElement
    ).value,
  ).toBe("Retained draft");
  await user.click(screen.getByRole("button", { name: "Actions" }));
  await user.click(
    await screen.findByRole("menuitem", { name: "Data sources" }),
  );
  await user.click(
    await screen.findByRole("link", { name: "Return to draft" }),
  );
  expect(
    (
      (await screen.findByRole("textbox", {
        name: "Dashboard name",
      })) as HTMLInputElement
    ).value,
  ).toBe("Retained draft");
  await user.click(screen.getByRole("button", { name: "Save dashboard" }));
  await waitFor(() => expect(state.PATCH).toHaveBeenCalledOnce());
  expect(state.PATCH.mock.calls[0][1]).toMatchObject({
    params: { path: { dashboard_id: "dsh_other" } },
    headers: { "If-Match": '"dsh_other:1"' },
    body: { name: "Retained draft" },
  });
  await waitFor(() =>
    expect(
      cache.getQueryData(["metrics-dashboard-draft", "ws_test", "dsh_other"]),
    ).toBeNull(),
  );
});

it("keeps the initial view focused and applies preset, refresh and absolute time controls", async () => {
  const { user } = setup();
  await screen.findByText("Select a target to explore metrics");
  expect(screen.queryByRole("region", { name: "CPU" })).toBeNull();
  await user.click(screen.getByRole("button", { name: /Time range:/ }));
  await user.click(screen.getByRole("button", { name: "Last 6 hours" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await user.click(screen.getByRole("combobox", { name: "Auto refresh" }));
  await user.click(await screen.findByRole("option", { name: "10 seconds" }));
  await setRange(user, "2026-10-19T10:00", "2026-10-19T12:00");
  await user.click(screen.getByRole("button", { name: "Zoom out" }));
  await user.click(screen.getByRole("button", { name: "Back to live" }));
  expect(screen.queryByRole("button", { name: "Back to live" })).toBeNull();
  expect(state.PATCH).not.toHaveBeenCalled();
});

it.each(["2026-03-08", "2026-11-01"])(
  "round-trips an absolute range across the %s daylight-saving boundary",
  async (date) => {
    const { user } = setup();
    await screen.findByText("Select a target to explore metrics");
    const start = `${date}T00:30`;
    const end = `${date}T03:30`;
    await setRange(user, start, end);
    await user.click(screen.getByRole("button", { name: /Time range:/ }));
    expect(
      (screen.getByLabelText("Start time") as HTMLInputElement).value,
    ).toBe(start);
    expect((screen.getByLabelText("End time") as HTMLInputElement).value).toBe(
      end,
    );
    expect(state.PATCH).not.toHaveBeenCalled();
  },
);

it("opens secondary dashboard actions from the menu and cancels creation without writing", async () => {
  const { user } = setup();
  await screen.findByRole("button", { name: "Edit dashboard" });
  await user.click(screen.getByRole("button", { name: "Actions" }));
  expect(
    (
      await screen.findByRole("menuitem", { name: "Data sources" })
    ).getAttribute("href"),
  ).toBe("/workspace/ws_test/monitoring/sources");
  expect(
    screen.getByRole("menuitem", { name: "Devices" }).getAttribute("href"),
  ).toBe("/workspace/ws_test/monitoring/devices");
  await user.click(screen.getByRole("menuitem", { name: "New dashboard" }));
  const dialog = await screen.findByRole("dialog", { name: "New dashboard" });
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(state.POST).not.toHaveBeenCalled();
});

it("retains completed panel queries across an asynchronous unmount gap", async () => {
  state.POST.mockResolvedValue({
    data: { status: "success", data: { resultType: "matrix", result: [] } },
    response: new Response(),
  });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const panel = {
    ...dashboard.config.panels![0],
    queries: [
      { kind: "promql" as const, source_id: "mds_test", expression: "up" },
    ],
  };
  const range = { start: 1000, end: 2000 };
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={cache}>{children}</QueryClientProvider>
  );
  const first = renderHook(() => usePanelData(panel, null, range, 0), {
    wrapper,
  });
  await waitFor(() => expect(first.result.current.loading).toBe(false));
  expect(state.POST).toHaveBeenCalledOnce();
  vi.useFakeTimers();
  try {
    first.unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    const second = renderHook(() => usePanelData(panel, null, range, 0), {
      wrapper,
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(state.POST).toHaveBeenCalledOnce();
    expect(second.result.current.loading).toBe(false);
    second.unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(
      cache.getQueryCache().findAll({ queryKey: ["metric-series"] }),
    ).toHaveLength(0);
  } finally {
    vi.useRealTimers();
    cache.clear();
  }
});

it("refreshes a live dashboard when visible again and reuses results across responsive remounts", async () => {
  const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  state.POST.mockResolvedValue({
    data: { status: "success", data: { resultType: "matrix", result: [] } },
    response: new Response(),
  });
  const { cache } = setup();
  await screen.findByText("Select a target to explore metrics");
  const live: Dashboard = {
    ...dashboard,
    config: {
      refresh_seconds: 300,
      panels: [
        {
          ...dashboard.config.panels![0],
          queries: [
            { kind: "promql", source_id: "mds_test", expression: "up" },
          ],
        },
      ],
    },
  };
  try {
    await act(async () => {
      cache.setQueryData(["metrics-dashboards", "ws_test"], [live]);
    });
    await waitFor(() => expect(state.POST).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(
        screen.getByRole("region", { name: "CPU" }).getAttribute("aria-busy"),
      ).toBe("false"),
    );
    hidden.mockReturnValue(true);
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(state.POST).toHaveBeenCalledTimes(1);
    hidden.mockReturnValue(false);
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => expect(state.POST).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(
        screen.getByRole("region", { name: "CPU" }).getAttribute("aria-busy"),
      ).toBe("false"),
    );
    for (const width of [390, 1200]) {
      state.width = width;
      await act(async () => {
        cache.setQueryData(
          ["metrics-dashboards", "ws_test"],
          [{ ...live, name: `Width ${width}` }],
        );
      });
      await screen.findByText(`Width ${width}`);
      expect(state.POST).toHaveBeenCalledTimes(2);
    }
    await act(async () => {
      cache.setQueryData(
        ["metrics-dashboards", "ws_test"],
        [{ ...live, config: { ...live.config, refresh_seconds: 0 } }],
      );
    });
    await waitFor(() =>
      expect(
        screen.getByRole("combobox", { name: "Auto refresh" }).textContent,
      ).toContain("Off"),
    );
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(state.POST).toHaveBeenCalledTimes(2);
  } finally {
    hidden.mockRestore();
    cache.clear();
  }
});
