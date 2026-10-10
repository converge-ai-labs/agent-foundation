import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TooltipProvider } from "a13n-ui";
import { FindingDetail } from "./detail";
import { FindingsPage } from "./page";

const http = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  PATCH: vi.fn(),
}));
const composer = vi.hoisted(() => ({ start: vi.fn() }));
const backend = vi.hoisted(() => ({ type: null as null | "logfire" }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/ws_test",
    can: () => true,
  }),
}));
vi.mock("../agents/composer", () => ({
  useAgentComposer: () => ({ ...composer, available: true, pending: false }),
}));
vi.mock("../traces/backend", () => ({
  useTraceBackend: () => ({ data: { type: backend.type }, isSuccess: true }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/{{(\w+)}}/g, (_, name) => String(values?.[name] ?? name)),
    i18n: { resolvedLanguage: "en" },
  }),
}));
const finding = {
  id: "fnd_test",
  version: 1,
  workspace_id: "ws_test",
  agent_id: "ap_target",
  agent_revision_id: "apr_cited",
  title: "Tool unavailable",
  severity: "critical",
  assessment: "unreviewed",
  assessment_note: "",
  closed: false,
  category: "execution",
  explanation: "The tool could not complete the task.",
  suggestion: "Check tool configuration.",
  evidence: [{ run_id: "run_target", trace_id: "a".repeat(32), span_ids: [] }],
  limitations: "Partial capture",
  source_key: "finding-1",
  analysis_id: null,
  source_run_id: null,
  created_by_id: "usr_test",
  created_at: "2026-10-09T10:00:00Z",
  updated_at: "2026-10-09T10:00:00Z",
};
beforeEach(() => {
  backend.type = null;
  http.GET.mockImplementation(async (path: string) => ({
    data:
      path === "/api/v1/findings/{finding_id}"
        ? finding
        : path === "/api/v1/agents/{agent_id}/revisions/{revision_id}"
          ? { id: "apr_cited", number: 3 }
          : path === "/api/v1/runs/{run_id}"
            ? { session_id: "sess_target", thread_id: "thread_target" }
            : {
                items:
                  path === "/api/v1/agents"
                    ? [{ id: "ap_target", name: "Research", preset_kind: null }]
                    : path === "/api/v1/models"
                      ? [{ key: "test", name: "Fixture model", enabled: true }]
                      : [],
                next_cursor: null,
              },
  }));
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
function mount(path = "/workspace/ws_test/findings") {
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      <TooltipProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route
              path="/workspace/ws_test/findings"
              element={<FindingsPage />}
            />
            <Route
              path="/workspace/ws_test/findings/:findingId"
              element={<FindingDetail />}
            />
          </Routes>
        </MemoryRouter>
      </TooltipProvider>
    </QueryClientProvider>,
  );
  return userEvent.setup();
}
it("does not infer health from empty findings and blocks analysis when trace query is disabled", async () => {
  const user = mount();
  await screen.findByText("No findings in this view");
  expect(
    screen.getByText(/No findings does not mean every trace was analyzed/),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Analyze traces" }));
  expect(
    await screen.findByText(
      /Configure a trace query provider before starting an analysis/,
    ),
  ).toBeTruthy();
  expect(
    (
      screen.getByRole("button", {
        name: "Start analysis",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(http.POST).not.toHaveBeenCalled();
});
it("submits selected rules and a bounded scope through the managed preset and ordinary-run command", async () => {
  backend.type = "logfire";
  http.POST.mockResolvedValue({ data: { id: "fan_test" } });
  const user = mount();
  await screen.findByText("No findings in this view");
  await user.click(screen.getByRole("button", { name: "Analyze traces" }));
  expect(
    screen.queryByRole("combobox", { name: "Agent to analyze" }),
  ).toBeNull();
  await user.click(screen.getByRole("checkbox", { name: /Answer quality/ }));
  expect(
    screen.queryByRole("combobox", { name: "Finding Agent model" }),
  ).toBeNull();
  expect(http.GET.mock.calls.some(([path]) => path === "/api/v1/models")).toBe(
    false,
  );
  await user.click(screen.getByRole("button", { name: "Start analysis" }));
  await waitFor(() =>
    expect(http.POST).toHaveBeenCalledWith("/api/v1/finding-agent"),
  );
  await waitFor(() =>
    expect(http.POST).toHaveBeenCalledWith(
      "/api/v1/finding-analyses",
      expect.objectContaining({
        body: expect.objectContaining({
          max_traces: 10,
          presets: ["execution", "recovery"],
        }),
        params: { header: { "Idempotency-Key": expect.any(String) } },
      }),
    ),
  );
  expect(http.POST.mock.calls[0][0]).toBe("/api/v1/finding-agent");
  expect(
    http.POST.mock.calls.find(
      ([path]) => path === "/api/v1/finding-analyses",
    )?.[1].body,
  ).not.toHaveProperty("agent_id");
  await waitFor(() =>
    expect(
      screen
        .getByRole("tab", { name: "Analysis history" })
        .getAttribute("aria-selected"),
    ).toBe("true"),
  );
});
it("analyzes a trace-page selection without choosing an agent", async () => {
  backend.type = "logfire";
  http.POST.mockResolvedValue({ data: { id: "fan_test" } });
  const user = mount("/workspace/ws_test/findings?trace=" + "a".repeat(32));
  await screen.findByRole("dialog", { name: "Analyze traces" });
  expect(
    screen.queryByRole("combobox", { name: "Agent to analyze" }),
  ).toBeNull();
  await user.click(screen.getByRole("button", { name: "Start analysis" }));
  await waitFor(() =>
    expect(http.POST).toHaveBeenCalledWith(
      "/api/v1/finding-analyses",
      expect.objectContaining({
        body: expect.objectContaining({
          trace_id: "a".repeat(32),
          max_traces: 1,
          started_after: undefined,
          started_before: undefined,
        }),
      }),
    ),
  );
  expect(
    http.POST.mock.calls.find(
      ([path]) => path === "/api/v1/finding-analyses",
    )?.[1].body,
  ).not.toHaveProperty("agent_id");
});

it("reviews impact independently and sends the exact revision and evidence to Composer", async () => {
  http.PATCH.mockImplementation(async (_path, options) => ({
    data: { ...finding, ...options.body, version: 2 },
  }));
  const user = mount("/workspace/ws_test/findings/fnd_test");
  await screen.findByText(/Critical.*Unconfirmed/);
  await user.click(screen.getByRole("combobox", { name: "Assessment" }));
  await user.click(await screen.findByRole("option", { name: "Confirmed" }));
  await user.click(screen.getByRole("button", { name: "Save assessment" }));
  await waitFor(() =>
    expect(http.PATCH).toHaveBeenCalledWith(
      "/api/v1/findings/{finding_id}",
      expect.objectContaining({
        body: { assessment: "confirmed", assessment_note: "" },
        params: {
          path: { finding_id: "fnd_test" },
          header: { "If-Match": '"fnd_test:1"' },
        },
      }),
    ),
  );
  await user.click(screen.getByRole("button", { name: "Fix with Composer" }));
  expect(composer.start).toHaveBeenCalledWith(
    expect.objectContaining({
      agent: { id: "ap_target", name: "Research" },
      revision: { id: "apr_cited", number: 3 },
      context: expect.stringContaining(
        "Review finding fnd_test using read_finding",
      ),
    }),
  );
  expect(http.POST).not.toHaveBeenCalled();
});

it("switches collections without stacking tables and preserves analysis evidence in row details", async () => {
  const original = http.GET.getMockImplementation()!;
  http.GET.mockImplementation(async (path: string, ...args: unknown[]) =>
    path === "/api/v1/finding-analyses"
      ? {
          data: {
            items: [
              {
                id: "fan_test",
                agent_id: null,
                session_id: "sess_analysis",
                thread_id: "thread_analysis",
                run_id: "run_analysis",
                run_status: "completed",
                selected_traces: [
                  { trace_id: "a".repeat(32), run_id: "run_a" },
                  { trace_id: "b".repeat(32), run_id: "run_b" },
                ],
                read_trace_ids: ["a".repeat(32)],
                finding_count: 3,
                cited_trace_count: 1,
                selection_truncated: true,
                created_at: "2026-10-09T10:00:00Z",
              },
            ],
            next_cursor: null,
          },
        }
      : original(path, ...args),
  );
  const user = mount();
  await screen.findByText("No findings in this view");
  expect(screen.queryByRole("table", { name: "Analysis history" })).toBeNull();
  await user.click(screen.getByRole("tab", { name: "Analysis history" }));
  await screen.findByRole("table", { name: "Analysis history" });
  expect(screen.queryByText("No findings in this view")).toBeNull();
  expect(screen.queryByText(/Coverage not reported/)).toBeNull();
  expect(screen.queryByRole("link", { name: "Open analysis run" })).toBeNull();
  expect(screen.getByRole("button", { name: "Analyze traces" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Workspace traces" }));
  await screen.findByRole("dialog", { name: "Analysis details" });
  expect(screen.getByText("Selected traces: 2")).toBeTruthy();
  expect(screen.getByText("Findings: 3")).toBeTruthy();
  expect(screen.getByText("Evidence-cited traces: 1")).toBeTruthy();
  expect(screen.queryByText(/Coverage not reported/)).toBeNull();
  expect(screen.getByText(/Selection capped/)).toBeTruthy();
  expect(
    screen
      .getByRole("link", { name: "Open analysis run" })
      .getAttribute("href"),
  ).toBe(
    "/workspace/ws_test/sessions/sess_analysis/threads/thread_analysis/runs/run_analysis",
  );
  expect(screen.getByText(/See the analysis run's final reply/)).toBeTruthy();
  expect(
    screen.getByRole("link", { name: "a".repeat(32) }).getAttribute("href"),
  ).toBe("/workspace/ws_test/traces/" + "a".repeat(32));
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await user.click(screen.getByRole("tab", { name: "Findings" }));
  await screen.findByText("No findings in this view");
  await waitFor(() =>
    expect(
      screen.queryByRole("table", { name: "Analysis history" }),
    ).toBeNull(),
  );
});

it("edits a false-positive assessment and its rationale together, clears stale rationale on a new outcome, and preserves saved feedback on close", async () => {
  let current = {
    ...finding,
    assessment: "false_positive",
    assessment_note: "The tool recovered.",
  };
  const original = http.GET.getMockImplementation()!;
  http.GET.mockImplementation(async (path: string, ...args: unknown[]) =>
    path === "/api/v1/findings/{finding_id}"
      ? { data: current }
      : original(path, ...args),
  );
  http.PATCH.mockImplementation(async (_path, options) => {
    current = { ...current, ...options.body, version: current.version + 1 };
    return { data: current };
  });
  const user = mount("/workspace/ws_test/findings/fnd_test");
  const note = await screen.findByRole("textbox", { name: "Review note" });
  expect((note as HTMLTextAreaElement).value).toBe("The tool recovered.");
  expect(
    (
      screen.getByRole("button", {
        name: "Fix with Composer",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  await user.click(screen.getByRole("combobox", { name: "Assessment" }));
  await user.click(await screen.findByRole("option", { name: "Confirmed" }));
  expect((note as HTMLTextAreaElement).value).toBe("");
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect((note as HTMLTextAreaElement).value).toBe("The tool recovered.");
  await user.clear(note);
  await user.type(note, "The retry succeeded, so this diagnosis is incorrect.");
  await user.click(screen.getByRole("button", { name: "Save assessment" }));
  await waitFor(() =>
    expect(http.PATCH).toHaveBeenCalledWith(
      "/api/v1/findings/{finding_id}",
      expect.objectContaining({
        body: {
          assessment: "false_positive",
          assessment_note:
            "The retry succeeded, so this diagnosis is incorrect.",
        },
        params: {
          path: { finding_id: "fnd_test" },
          header: { "If-Match": '"fnd_test:1"' },
        },
      }),
    ),
  );
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Save assessment",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true),
  );
  await user.click(screen.getByRole("button", { name: "Close finding" }));
  await screen.findByRole("button", { name: "Reopen finding" });
  expect(
    (
      screen.getByRole("textbox", {
        name: "Review note",
      }) as HTMLTextAreaElement
    ).value,
  ).toBe("The retry succeeded, so this diagnosis is incorrect.");
  expect(screen.getByText(finding.explanation)).toBeTruthy();
});
