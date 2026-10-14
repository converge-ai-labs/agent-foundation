// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { createClient, type Client } from "../../../../service-client";
import type { Schema } from "../../../../shared/api";
import { invalidateConversation } from "../../api";
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
let recorded: Schema["ModelUsage"][];
let usageStatus: number;
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
  usageStatus = 200;
  recorded = [
    {
      model: "large",
      requests: 3,
      input_tokens: 24000,
      output_tokens: 900,
      cache_read_tokens: 18000,
      cache_write_tokens: 0,
      cost: "0.42",
      average_time_to_first_chunk_seconds: 0.84,
      output_tokens_per_second: 62.4,
    },
  ];
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
      if (url.pathname.endsWith("/usage")) {
        expect(url.searchParams.get("session_id")).toBe("ses_1");
        if (usageStatus !== 200)
          return Response.json({}, { status: usageStatus });
        return Response.json({
          // Another Run in this Session must never inflate this Run's totals.
          models: [...recorded, ...recorded],
          runs: [
            { run_id: "run_2", thread_id: "thr_1", models: recorded },
            { run_id: "run_other", thread_id: "thr_1", models: recorded },
          ],
        });
      }
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
    <DebugRunSection
      run={fixtureRun()}
      thread={fixtureThread()}
      timeline={fixtureTimeline()}
      index={2}
      {...props}
    />,
    {
      wrapper: ({ children }) => (
        <QueryClientProvider client={cache}>
          <MemoryRouter>{children}</MemoryRouter>
        </QueryClientProvider>
      ),
    },
  );
}

it("reads one run as the executor's record: input, steps and output", async () => {
  show();
  expect(screen.getByText("Run 2")).toBeTruthy();
  expect(screen.getByText("state.completed")).toBeTruthy();
  // The input names its author as a fact beside its label.
  const input = screen.getByText("Input").closest("section")!;
  expect(within(input).getByText("Run the checks")).toBeTruthy();
  expect(within(input).getByText("User")).toBeTruthy();
  // Totals come from reported usage; the duration is the run's own span.
  expect(screen.getByText("12s")).toBeTruthy();
  const calls = (await screen.findByText("Model calls")).parentElement!;
  expect(within(calls).getByText("3")).toBeTruthy();
  expect(screen.getByText("24k → 900")).toBeTruthy();
  expect(screen.getByText("$0.42")).toBeTruthy();
  expect(screen.getByText("3.1k → 210")).toBeTruthy();
  // Model requests are the skeleton; the edit and the reply are steps beneath.
  const steps = screen.getByText("Steps").closest("section")!;
  expect(within(steps).getByText("Model call 1")).toBeTruthy();
  expect(within(steps).getByText("gpt-5 · called 1 tools")).toBeTruthy();
  expect(within(steps).getByText("edit_file")).toBeTruthy();
  expect(within(steps).getByText("+2")).toBeTruthy();
  expect(within(steps).getByText("−1")).toBeTruthy();
  expect(within(steps).getByRole("button", { name: /Reply/ })).toBeTruthy();
  // Each line says how far into the run it happened; a finished run ends on
  // the line that says when it did.
  const end = screen.getByText("Run completed").closest("[data-kind]")!;
  expect(end.textContent).toContain("+12.0s");
  // What the run returned is read once, after its steps.
  const output = screen.getByText("Output").closest("section")!;
  expect(within(output).getByText("Patched the fold.")).toBeTruthy();
  // Routine lifecycle facts stay in the Details card: accepting the run and
  // starting its first attempt say nothing a reader can act on.
  expect(screen.queryByText(/Attempt 1/)).toBeNull();
  expect(screen.queryByText(/Run accepted/)).toBeNull();
  // Attempts are still loaded only when Details opens.
  expect(requests.filter((path) => path.includes("/attempts"))).toEqual([]);
});

it("keeps a closed run to its header and one line of input", async () => {
  show();
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Run 2/ }));
  expect(screen.getByText("Run the checks")).toBeTruthy();
  expect(screen.queryByText("Steps")).toBeNull();
  expect(screen.queryByText("Output")).toBeNull();
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
  // The head names the outcome; its reason and message follow it.
  expect(screen.getByText("Run failed")).toBeTruthy();
  expect(
    screen.getByText("model_rate_limited · Upstream said no."),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Retry run" })).toBeNull();
});

it("opens the run details in a dialog, on demand, starting with usage", async () => {
  show();
  const user = userEvent.setup();
  expect(requests.filter((path) => path.endsWith("/attempts"))).toEqual([]);
  await user.click(screen.getByRole("button", { name: "Details" }));
  const dialog = await screen.findByRole("dialog", { name: /Run 2/ });
  const usage = await within(dialog).findByRole("table", {
    name: "Usage by model",
  });
  expect(within(usage).getAllByRole("row")[1]!.textContent).toMatch(
    /^large3 requests24k18k75\.0%900840ms62 tok\/s\$0\.42$/,
  );
  const attempts = await within(dialog).findByText("Attempt 1");
  expect(
    within(attempts.closest("li")!).getByText("state.succeeded"),
  ).toBeTruthy();
  expect(within(dialog).getByText("Overview")).toBeTruthy();
  expect(await within(dialog).findByText("Continued run")).toBeTruthy();
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
  expect(screen.getByText("Waiting for approval")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Retry run" })).toBeNull();
});

it("summarizes a call wait without assuming who supplies the result", () => {
  const run = fixtureRun({
    status: "waiting",
    sealed_at: "2026-09-20T10:00:05.000Z",
    wait_reason: "call",
  });
  show({ run, timeline: fixtureTimeline({ run }) });
  expect(screen.getByText("Waiting for call results")).toBeTruthy();
});

it("leaves a model request without reported tokens unstated, never zeroed", async () => {
  recorded = recorded.map((usage) => ({
    ...usage,
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    cost: null,
  }));
  const execution = fixtureExecution();
  execution.steps = execution.steps.map((step) =>
    step.kind === "llm" ? { ...step, usage: undefined } : step,
  );
  execution.usage = { model: [], provider: [], recordIds: [] };
  show({ timeline: fixtureTimeline({ execution }) });
  expect(
    await within(
      (await screen.findByText("Model calls")).parentElement!,
    ).findByText("3"),
  ).toBeTruthy();
  expect(screen.queryByText(/→/)).toBeNull();
});

it("keeps complete Run usage while earlier execution pages load", async () => {
  recorded = [
    {
      ...recorded[0]!,
      requests: 40,
      input_tokens: 2493365,
      output_tokens: 22276,
      cache_read_tokens: 2405888,
      cost: "0.033704814",
    },
  ];
  const partial = fixtureTimeline({
    execution: fixtureExecution({ coverage: "partial" }),
  });
  const view = show({ timeline: partial });
  expect(
    await within(
      (await screen.findByText("Model calls")).parentElement!,
    ).findByText("40"),
  ).toBeTruthy();
  const heading = () => within(view.container.querySelector("header")!);
  expect(heading().getByText("2.49M → 22.3k")).toBeTruthy();
  expect(heading().getByText("96.5%")).toBeTruthy();
  expect(heading().getByText("$0.03")).toBeTruthy();

  view.rerender(
    <DebugRunSection
      run={fixtureRun()}
      thread={fixtureThread()}
      timeline={fixtureTimeline()}
      index={2}
    />,
  );
  expect(
    within(heading().getByText("Model calls").parentElement!).getByText("40"),
  ).toBeTruthy();
  expect(heading().getByText("2.49M → 22.3k")).toBeTruthy();

  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: "Details" }));
  const dialog = await screen.findByRole("dialog", { name: /Run 2/ });
  expect(await within(dialog).findByText("40 requests")).toBeTruthy();
  expect(within(dialog).getByText("2.49M")).toBeTruthy();
  expect(within(dialog).getByText("22.3k")).toBeTruthy();
  expect(within(dialog).getByText("96.5%")).toBeTruthy();
});

it("refreshes the heading and open Details together after a Session change", async () => {
  show();
  expect(
    await within(
      (await screen.findByText("Model calls")).parentElement!,
    ).findByText("3"),
  ).toBeTruthy();
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: "Details" }));
  const dialog = await screen.findByRole("dialog", { name: /Run 2/ });
  expect(await within(dialog).findByText("3 requests")).toBeTruthy();
  recorded = recorded.map((usage) => ({
    ...usage,
    requests: 4,
    input_tokens: 30000,
  }));
  await invalidateConversation(cache, "workspace", { sessionId: "ses_1" });
  expect(
    await within(
      (await screen.findByText("Model calls")).parentElement!,
    ).findByText("4"),
  ).toBeTruthy();
  expect(screen.getByText("30k → 900")).toBeTruthy();
  expect(await within(dialog).findByText("4 requests")).toBeTruthy();
  expect(within(dialog).getByText("30k")).toBeTruthy();
});

it("does not substitute loaded-page totals when accounting is unavailable", async () => {
  usageStatus = 503;
  const view = show();
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: "Details" }));
  const dialog = await screen.findByRole("dialog", { name: /Run 2/ });
  expect(await within(dialog).findByRole("alert")).toBeTruthy();
  const heading = within(view.container.querySelector("header")!);
  expect(heading.queryByText(/Model calls|→|Cache|\$/)).toBeNull();
  // A visible model call still shows its own received usage.
  expect(screen.getByText("3.1k → 210")).toBeTruthy();
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
