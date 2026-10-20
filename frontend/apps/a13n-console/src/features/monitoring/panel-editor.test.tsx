import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import type { Panel, TimeRange } from "./api";
import { PanelEditor } from "./panel-editor";

const state = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ workspace: () => state }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" }, can: () => true }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("./chart", () => ({ MetricsChart: () => null }));
const panel: Panel = {
  id: "preview",
  title: "Preview",
  position: { x: 0, y: 0, w: 6, h: 5 },
  queries: [{ kind: "promql", source_id: "mds_test", expression: "up" }],
};

beforeEach(() => {
  vi.clearAllMocks();
  // jsdom has no text layout; keep the real CodeMirror extension graph.
  Object.defineProperty(Range.prototype, "getClientRects", {
    configurable: true,
    value: () => [],
  });
  Object.defineProperty(Range.prototype, "getBoundingClientRect", {
    configurable: true,
    value: () => new DOMRect(),
  });
  state.GET.mockResolvedValue({
    data: { items: [], next_cursor: null },
    response: new Response(),
  });
  state.POST.mockResolvedValue({
    data: { status: "success", data: { resultType: "matrix", result: [] } },
    response: new Response(),
  });
});

it("runs preview queries only on explicit submission using a snapshot of the current range", async () => {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const user = userEvent.setup();
  const editor = (range: TimeRange) => (
    <QueryClientProvider client={cache}>
      <PanelEditor
        panel={panel}
        target={null}
        range={range}
        onApply={vi.fn()}
        onClose={vi.fn()}
      />
    </QueryClientProvider>
  );
  const { rerender, unmount } = render(editor({ start: 0, end: 3600 }));
  await screen.findByLabelText("PromQL expression 1", {}, { timeout: 5000 });
  expect(state.POST).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Run queries" }));
  await waitFor(() => expect(state.POST).toHaveBeenCalledTimes(1));
  expect(state.POST.mock.calls[0][1].body).toMatchObject({
    start: 0,
    end: 3600,
  });
  await act(async () => rerender(editor({ start: 30, end: 3630 })));
  expect(state.POST).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole("button", { name: "Run queries" }));
  await waitFor(() => expect(state.POST).toHaveBeenCalledTimes(2));
  expect(state.POST.mock.calls[1][1].body).toMatchObject({
    start: 30,
    end: 3630,
  });
  unmount();
  cache.clear();
});

it("retains the remaining PromQL editor when an earlier query is removed", async () => {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const apply = vi.fn(),
    user = userEvent.setup();
  const { unmount } = render(
    <QueryClientProvider client={cache}>
      <PanelEditor
        panel={{
          ...panel,
          queries: [
            panel.queries[0],
            {
              kind: "promql",
              source_id: "mds_test",
              expression: "rate(foo[5m])",
            },
          ],
        }}
        target={null}
        range={{ start: 0, end: 3600 }}
        onApply={apply}
        onClose={vi.fn()}
      />
    </QueryClientProvider>,
  );
  const second = await screen.findByLabelText("PromQL expression 2");
  await user.click(screen.getAllByRole("button", { name: "Remove query" })[0]);
  expect(screen.getByLabelText("PromQL expression 1")).toBe(second);
  expect(second.textContent).toBe("rate(foo[5m])");
  await user.click(screen.getByRole("button", { name: "Apply panel" }));
  expect(apply.mock.calls[0][0].queries).toEqual([
    { kind: "promql", source_id: "mds_test", expression: "rate(foo[5m])" },
  ]);
  unmount();
  cache.clear();
});

it("defaults PromQL to an enabled query-capable source", async () => {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  cache.setQueryData(
    ["metrics-data-sources", "ws_test"],
    [
      {
        id: "disabled",
        name: "Disabled",
        enabled: false,
        config: { query: {} },
      },
      { id: "enabled", name: "Enabled", enabled: true, config: { query: {} } },
    ],
  );
  state.GET.mockResolvedValue({
    data: {
      items: [
        {
          id: "disabled",
          name: "Disabled",
          enabled: false,
          config: { query: {} },
        },
        {
          id: "enabled",
          name: "Enabled",
          enabled: true,
          config: { query: {} },
        },
      ],
      next_cursor: null,
    },
    response: new Response(),
  });
  const user = userEvent.setup();
  const { unmount } = render(
    <QueryClientProvider client={cache}>
      <PanelEditor
        panel={{
          ...panel,
          queries: [{ kind: "metric", metric: "system_cpu_busy_ratio" }],
        }}
        target={null}
        range={{ start: 0, end: 3600 }}
        onApply={vi.fn()}
        onClose={vi.fn()}
      />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(state.GET).toHaveBeenCalled());
  await user.click(screen.getByRole("combobox", { name: "Query type" }));
  await user.click(screen.getByRole("option", { name: "PromQL" }));
  await screen.findByLabelText("PromQL expression 1", {}, { timeout: 5000 });
  expect(
    screen.getByRole("combobox", { name: "Data source" }).textContent,
  ).toContain("Enabled");
  unmount();
  cache.clear();
});
