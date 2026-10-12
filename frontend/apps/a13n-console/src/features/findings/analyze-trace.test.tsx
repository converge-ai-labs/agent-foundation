import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { AnalyzeTrace } from "./analyze-trace";

const http = vi.hoisted(() => ({ GET: vi.fn() }));
const access = vi.hoisted(() => ({ permissions: ["run", "write"] }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/ws_test",
    can: (verb: string) => access.permissions.includes(verb),
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

const caches: QueryClient[] = [];
afterEach(() => {
  cleanup();
  caches.forEach((cache) => cache.clear());
  caches.length = 0;
  vi.resetAllMocks();
  access.permissions = ["run", "write"];
});

function mount(runId: string | null = "run_test") {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  caches.push(cache);
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <AnalyzeTrace runId={runId} traceId="trace_test" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return cache;
}

it.each(["completed", "failed", "cancelled"])(
  "links a %s run's trace directly to analysis",
  async (status) => {
    http.GET.mockResolvedValue({
      data: { status },
      response: new Response(),
    });
    mount();
    const link = await screen.findByRole("link", { name: "Analyze trace" });
    expect(link.getAttribute("href")).toBe(
      "/workspace/ws_test/findings?trace=trace_test",
    );
    expect(http.GET).toHaveBeenCalledWith(
      "/api/v1/runs/{run_id}",
      expect.objectContaining({
        params: { path: { run_id: "run_test" } },
      }),
    );
  },
);

it.each([
  { permissions: ["run"] },
  { permissions: ["write"] },
  { permissions: [] },
])(
  "does not read the run or offer analysis with permissions $permissions",
  ({ permissions }) => {
    access.permissions = permissions;
    mount();
    expect(http.GET).not.toHaveBeenCalled();
    expect(screen.queryByRole("link", { name: "Analyze trace" })).toBeNull();
  },
);

it("does not read a run when the trace has no Service run reference", () => {
  mount(null);
  expect(http.GET).not.toHaveBeenCalled();
  expect(screen.queryByRole("link", { name: "Analyze trace" })).toBeNull();
});

it("does not offer analysis for an active run", async () => {
  http.GET.mockResolvedValue({
    data: { status: "running" },
    response: new Response(),
  });
  const cache = mount();
  await waitFor(() =>
    expect(cache.getQueryState(["run", "ws_test", "run_test"])?.status).toBe(
      "success",
    ),
  );
  expect(screen.queryByRole("link", { name: "Analyze trace" })).toBeNull();
});
