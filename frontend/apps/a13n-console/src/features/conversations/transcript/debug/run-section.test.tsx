// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { createClient, type Client } from "../../../../service-client";
import { DebugRunSection } from "./run-section";
import {
  fixtureAttempt,
  fixtureExecution,
  fixtureRun,
  fixtureThread,
  fixtureTimeline,
} from "../fixture";

let client: Client;
let cache: QueryClient;
const requests: string[] = [];
vi.mock("../../../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "workspace" },
    basePath: "/workspace/design",
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) =>
      options
        ? key.replace(/{{(\w+)}}/g, (_, name) => String(options[name] ?? ""))
        : key,
    i18n: { resolvedLanguage: "en" },
  }),
}));

beforeEach(() => {
  requests.length = 0;
  cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch: async (input, init) => {
      const url = new URL(new Request(input, init).url);
      requests.push(url.pathname);
      if (url.pathname.endsWith("/threads"))
        return Response.json({ items: [], next_cursor: null });
      if (url.pathname.endsWith("/threads/thr_1"))
        return Response.json(fixtureThread());
      if (url.pathname.endsWith("/attempts"))
        return Response.json({
          items: [
            {
              id: "att_1",
              run_id: "run_2",
              number: 1,
              status: "succeeded",
              started_at: "2026-09-20T10:00:00.000Z",
              finished_at: "2026-09-20T10:00:12.000Z",
              created_at: "2026-09-20T10:00:00.000Z",
              start_reason: "initial",
              yield_reason: null,
              failure: null,
              harness_run_id: "harness_1",
              worker_build: "build",
              replaces_attempt_id: null,
            },
          ],
        });
      if (url.pathname.endsWith("/lineage"))
        return Response.json({ items: [fixtureRun()], next_cursor: null });
      if (url.pathname.endsWith("/environments"))
        return Response.json({ items: [], next_cursor: null });
      if (url.pathname.includes("/runs/")) return Response.json(fixtureRun());
      throw new Error(`Unexpected request: ${url.pathname}`);
    },
  });
});
afterEach(() => {
  cleanup();
  cache.clear();
  client.close();
});

function show(props: Partial<Parameters<typeof DebugRunSection>[0]> = {}) {
  return render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <DebugRunSection
          run={fixtureRun()}
          thread={fixtureThread()}
          timeline={fixtureTimeline()}
          index={2}
          {...props}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it("reads one run as a heading, its request and one timeline", () => {
  show();
  expect(screen.getByText("Run 2")).toBeTruthy();
  expect(screen.getByText("state.completed")).toBeTruthy();
  expect(screen.getByText("Run the checks")).toBeTruthy();
  expect(screen.getByText("User")).toBeTruthy();
  // Totals come from reported usage; the duration is the run's own span.
  expect(screen.getByText("12s")).toBeTruthy();
  expect(screen.getByText("1 model calls")).toBeTruthy();
  expect(screen.getAllByText("3.1k → 210").length).toBe(2);
  expect(screen.getByText("$0.0041")).toBeTruthy();
  // Model requests are the skeleton, the edit nests beneath, the reply is prose.
  expect(screen.getByText("Model call 1")).toBeTruthy();
  expect(screen.getByText("gpt-5 · called 1 tools")).toBeTruthy();
  expect(screen.getByText("edit_file")).toBeTruthy();
  expect(screen.getByText("+2")).toBeTruthy();
  expect(screen.getByText("−1")).toBeTruthy();
  expect(screen.getByText("Patched the fold.")).toBeTruthy();
  // Routine lifecycle facts stay in the Details card: accepting the run and
  // starting its first attempt say nothing a reader can act on.
  expect(screen.queryByText(/Attempt 1/)).toBeNull();
  expect(screen.queryByText(/Run accepted/)).toBeNull();
  // Nothing was requested to render the section itself.
  expect(requests.filter((path) => path.includes("/attempts"))).toEqual([]);
});

it("opens a tool row in place with its arguments, result and patch", async () => {
  show();
  const user = userEvent.setup();
  const row = screen.getByRole("button", { name: /edit_file/ });
  expect(row.getAttribute("aria-expanded")).toBe("false");
  await user.click(row);
  expect(row.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByText("Arguments")).toBeTruthy();
  expect(screen.getByText("Result")).toBeTruthy();
  expect(screen.getByText("Applied 1 hunk")).toBeTruthy();
  expect(screen.getByText("Patch")).toBeTruthy();
  expect(screen.getAllByText("src/stream.ts").length).toBeGreaterThan(0);
  expect(screen.getByText("+const a = 2;")).toBeTruthy();
  expect(screen.getByText("-const a = 1;")).toBeTruthy();
});

it("says so when the execution history is incomplete", () => {
  show({
    timeline: fixtureTimeline({
      execution: fixtureExecution({ coverage: "partial" }),
    }),
  });
  expect(
    screen.getByText(
      "Execution history is incomplete; showing retained messages.",
    ),
  ).toBeTruthy();
});

it("names the failure of a failed run and leaves continuing to the dock", () => {
  const run = fixtureRun({
    status: "failed",
    failure: { code: "model_rate_limited", message: "Upstream said no." },
  });
  show({ run, timeline: fixtureTimeline({ run }) });
  expect(
    screen.getByText("Run failed · model_rate_limited · Upstream said no."),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Retry run" })).toBeNull();
});

it("opens the run details in place, on demand", async () => {
  show();
  const user = userEvent.setup();
  expect(requests.filter((path) => path.includes("/attempts"))).toEqual([]);
  await user.click(screen.getByRole("button", { name: "Details" }));
  const attempts = await screen.findByText("Attempt 1");
  expect(
    within(attempts.closest("li")!).getByText("state.succeeded"),
  ).toBeTruthy();
  expect(screen.getByText("Overview")).toBeTruthy();
  expect(await screen.findByText("Continued run")).toBeTruthy();
});

it("pauses an unresolved call while the run waits for a person", () => {
  const run = fixtureRun({
    status: "waiting",
    sealed_at: "2026-09-20T10:00:05.000Z",
    wait_reason: "approval",
  });
  const execution = fixtureExecution();
  execution.steps = execution.steps.map((step) =>
    step.kind === "llm" ? step : { ...step, state: "running", endedAt: null },
  );
  show({ run, timeline: fixtureTimeline({ run, execution }) });
  // The call is paused, not interrupted, and the run says what it waits for.
  expect(screen.getAllByLabelText("Waiting").length).toBeGreaterThan(0);
  expect(screen.queryByLabelText("Failed")).toBeNull();
  expect(screen.getByText("Waiting for approval · approval")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Retry run" })).toBeNull();
});

it("summarizes a call wait without assuming who supplies the result", () => {
  const run = fixtureRun({
    status: "waiting",
    sealed_at: "2026-09-20T10:00:05.000Z",
    wait_reason: "call",
  });
  show({ run, timeline: fixtureTimeline({ run }) });
  expect(screen.getByText("Waiting for call results · call")).toBeTruthy();
});

it("leaves a model request without reported tokens unstated, never zeroed", () => {
  const execution = fixtureExecution();
  execution.steps = execution.steps.map((step) =>
    step.kind === "llm" ? { ...step, usage: undefined } : step,
  );
  execution.usage = { model: [], provider: [], recordIds: [] };
  show({ timeline: fixtureTimeline({ execution }) });
  expect(screen.queryByText(/→/)?.textContent).toBe("— → —");
  expect(screen.queryByText("0 → 0")).toBeNull();
});

it("reads an asynchronous child's result as the child's own reply", () => {
  const run = fixtureRun({
    trigger: "child_result",
    input: {
      child_run_id: "run_child",
      subagent: "Researcher",
      status: "completed",
      output: "INC-118 matches this fold.",
      failure: null,
    },
  });
  show({ run, timeline: fixtureTimeline({ run }) });
  expect(screen.getByText("Subagent result · Researcher")).toBeTruthy();
  expect(screen.getByText("INC-118 matches this fold.")).toBeTruthy();
  // The child's terminal status is the quiet second line of the request.
  expect(screen.getAllByText("state.completed").length).toBe(2);
  expect(screen.queryByText(/child_run_id/)).toBeNull();
});

it("reads a child thread's run as the task its parent delegated", () => {
  const run = fixtureRun({
    input: {
      content: [
        {
          type: "text",
          text: JSON.stringify({
            delegated_task: "Find prior incidents for cursor folds",
            parent_task: "Run the checks and fix whatever fails",
          }),
        },
      ],
    },
  });
  show({
    run,
    thread: fixtureThread({ origin: "child" }),
    timeline: fixtureTimeline({ run }),
  });
  expect(screen.getByText("Delegated task")).toBeTruthy();
  expect(
    screen.getByText("Find prior incidents for cursor folds"),
  ).toBeTruthy();
  expect(
    screen.getByText("From: Run the checks and fix whatever fails"),
  ).toBeTruthy();
});

it("shows a later attempt, and nothing for the first", () => {
  const run = fixtureRun({ attempts: 2 });
  show({
    run,
    timeline: fixtureTimeline({
      run,
      attempts: [fixtureAttempt(1), fixtureAttempt(2)],
    }),
  });
  expect(screen.getByText("Attempt 2 started · recovery")).toBeTruthy();
  expect(screen.queryByText(/Attempt 1/)).toBeNull();
  expect(screen.getByText("2 attempts")).toBeTruthy();
});
