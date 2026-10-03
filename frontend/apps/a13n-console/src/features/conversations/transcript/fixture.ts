import { createClient } from "../../../service-client";
import type { Schema } from "../../../shared/api";
import { emptyExecution } from "../execution";
import type { PresentedItem } from "../projection";
import type { RunExecution } from "../run-display";
import { runTimeline, type RunTimeline } from "../timeline";
import type { StepUsage } from "../usage";

/** One observed Run, shaped the way the stream folds it, for tests. */
const START = "2026-09-20T10:00:00.000Z";
const at = (seconds: number) =>
  new Date(Date.parse(START) + seconds * 1000).toISOString();

/** A message payload of one text part, as a Run's source entry carries it. */
export const textInput = (text: string) => ({
  content: [{ type: "text", text }],
});

export function fixtureRun(
  overrides: Partial<Schema["RunView"]> = {},
): Schema["RunView"] {
  return {
    id: "run_2",
    workspace_id: "ws_1",
    agent_id: "agt_1",
    agent_revision_id: "rev_1",
    revision_selection: "default",
    principal_id: "usr_1",
    attempts: 1,
    max_attempts: 3,
    cancel_requested_at: null,
    created_at: START,
    current_attempt_id: null,
    environment_mounts: [],
    failure: null,
    input: textInput("Run the checks"),
    labels: {},
    lineage: "continue",
    memory_mounts: [],
    options: {},
    output: "Patched the fold.",
    parent_run_id: "run_1",
    pending: null,
    resume: null,
    resumed_by_id: null,
    sealed_at: at(12),
    session_id: "ses_1",
    source_entry_id: "inb_1",
    started_at: START,
    status: "completed",
    thread_id: "thr_1",
    trigger: "input",
    updated_at: at(12),
    usage_at_seal: null,
    version: 3,
    wait_reason: null,
    ...overrides,
  };
}

export function fixtureThread(
  overrides: Partial<Schema["ThreadView"]> = {},
): Schema["ThreadView"] {
  return {
    archived_at: null,
    created_at: START,
    current_run_id: null,
    id: "thr_1",
    labels: {},
    last_run_id: "run_2",
    message_history: [],
    mcp_headers: {},
    origin: "new",
    origin_run_id: null,
    origin_thread_id: null,
    origin_tool_call_id: null,
    session_id: "ses_1",
    subagent: null,
    updated_at: at(12),
    version: 4,
    workspace_id: "ws_1",
    ...overrides,
  };
}

/** An attempt of the fixture Run: the first starts it, a later one recovers it. */
export function fixtureAttempt(
  number: number,
  overrides: Partial<Schema["AttemptView"]> = {},
): Schema["AttemptView"] {
  return {
    id: `att_${number}`,
    run_id: "run_2",
    number,
    status: "succeeded",
    start_reason: number === 1 ? "initial" : "recovery",
    yield_reason: null,
    failure: null,
    harness_run_id: `harness_${number}`,
    worker_build: "build",
    replaces_attempt_id: number === 1 ? null : `att_${number - 1}`,
    started_at: at(number),
    finished_at: null,
    created_at: at(number),
    ...overrides,
  };
}

/** The Thread a Run delegated to, and the Thread a Run was forked into. */
export const fixtureChildThread = fixtureThread({
  id: "thr_child",
  origin: "child",
  origin_thread_id: "thr_1",
  origin_run_id: "run_2",
  origin_tool_call_id: "call_delegate",
  subagent: "researcher",
  last_run_id: "run_child",
});
export const fixtureForkThread = fixtureThread({
  id: "thr_fork",
  origin: "fork",
  origin_thread_id: "thr_1",
  origin_run_id: "run_2",
  last_run_id: "run_fork",
});

/**
 * The Session those Threads belong to, served: a root Thread of two Runs, a
 * child Thread its second Run delegated to, and a fork of that same Run.
 */
export function fixtureBranchedSession() {
  return createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch: async (input, init) => {
      const url = new URL(new Request(input, init).url);
      const name = url.pathname.split("/").at(-1) ?? "";
      if (url.pathname.endsWith("/runs/run_2/lineage"))
        return Response.json({
          items: [fixtureRun({ lineage: "root", parent_run_id: null })],
          next_cursor: null,
        });
      if (url.pathname.endsWith("/runs/run_fork/lineage"))
        return Response.json({
          items: [
            fixtureRun({ id: "run_fork", thread_id: "thr_fork" }),
            fixtureRun(),
            fixtureRun({ id: "run_1", lineage: "root", parent_run_id: null }),
          ],
          next_cursor: null,
        });
      if (url.pathname.includes("/agents/"))
        return Response.json({
          id: name,
          key: name,
          name: name === "agt_child" ? "Researcher" : "Release Bot",
        });
      if (url.pathname.endsWith("/threads/thr_1"))
        return Response.json(fixtureThread());
      if (
        url.pathname.endsWith("/threads") &&
        url.searchParams.get("session_id") === "ses_1"
      )
        return Response.json({
          items: [fixtureThread(), fixtureChildThread, fixtureForkThread],
          next_cursor: null,
        });
      if (url.pathname.endsWith("/threads/thr_fork/runs"))
        return Response.json({
          items: [
            fixtureRun({
              id: "run_fork",
              thread_id: "thr_fork",
              lineage: "fork",
              input: textInput("Alternative proposal"),
            }),
          ],
          next_cursor: null,
        });
      if (url.pathname.endsWith("/threads/thr_1/runs"))
        return Response.json({
          items: [
            fixtureRun({
              id: "run_2",
              input: textInput("Write the marker"),
              status: "waiting",
            }),
            fixtureRun({
              id: "run_1",
              input: textInput("Review the release"),
            }),
          ],
          next_cursor: null,
        });
      if (url.pathname.endsWith("/threads/thr_child/runs"))
        return Response.json({
          items: [
            fixtureRun({
              id: "run_child",
              thread_id: "thr_child",
              agent_id: "agt_child",
              trigger: "spawned",
              input: textInput("Find prior incidents"),
            }),
          ],
          next_cursor: null,
        });
      throw new Error(`Unexpected request: ${url.pathname}`);
    },
  });
}

const usage: StepUsage = {
  model: "gpt-5",
  provider: "openai",
  inputTokens: 3100,
  outputTokens: 210,
  cacheReadTokens: 900,
  cacheWriteTokens: 0,
  costUsd: "0.0041",
  pricingStatus: "priced",
};

/** A model request that read a file, edited it, and answered. */
export function fixtureExecution(
  overrides: Partial<RunExecution> = {},
): RunExecution {
  return {
    ...emptyExecution(),
    steps: [
      {
        id: "step_model",
        scope: "1",
        kind: "llm",
        state: "completed",
        items: [],
        position: "1-0",
        startedAt: at(1),
        endedAt: at(2),
        messageCount: 2,
        usage,
      },
      {
        id: "step_edit",
        scope: "1",
        kind: "tool",
        name: "edit_file",
        state: "completed",
        items: ["item_edit"],
        position: "2-0",
        startedAt: at(2),
        endedAt: at(3),
        parentId: "step_model",
        edit: {
          filePath: "src/stream.ts",
          before: "const a = 1;\n",
          after: "const a = 2;\nconst b = 3;\n",
        },
      },
    ],
    usage: { model: [usage], provider: [], recordIds: ["rec_1"] },
    coverage: "complete",
    ...overrides,
  };
}

function fixtureItems(): PresentedItem[] {
  return [
    {
      id: "item_edit",
      kind: "tool_call",
      state: "completed",
      firstPosition: "2-0",
      lastPosition: "2-1",
      startedAt: at(2),
      endedAt: at(3),
      text: "",
      role: "assistant",
      toolName: "edit_file",
      arguments: '{"path":"src/stream.ts"}',
      result: "Applied 1 hunk",
      protectedReasoning: false,
    },
    {
      id: "item_reply",
      kind: "text_message",
      state: "completed",
      firstPosition: "3-0",
      lastPosition: "3-1",
      startedAt: at(3),
      endedAt: at(4),
      text: "Patched the fold.",
      role: "assistant",
      toolName: "",
      arguments: "",
      protectedReasoning: false,
    },
  ];
}

/** The one reading of a Run both disclosure levels render. */
export function fixtureTimeline({
  run = fixtureRun(),
  attempts = [fixtureAttempt(1)],
  items = fixtureItems(),
  execution = fixtureExecution(),
}: {
  run?: Schema["RunView"];
  attempts?: Schema["AttemptView"][];
  items?: PresentedItem[];
  execution?: RunExecution;
} = {}): RunTimeline {
  return runTimeline({
    run,
    attempts,
    items,
    execution,
    coverage: execution.coverage,
  });
}

/** A Run read from retained Items alone, with no execution history. */
export function retainedTimeline(
  items: PresentedItem[],
  run = fixtureRun(),
): RunTimeline {
  return runTimeline({
    run,
    items,
    execution: emptyExecution(),
    coverage: "unavailable",
  });
}
