import type { Schema } from "../../../shared/api";
import { emptyExecution } from "../execution";
import type { PresentedItem } from "../projection";
import type { RunExecution } from "../run-stream";
import { runTimeline, type RunTimeline } from "../timeline";
import type { StepUsage } from "../usage";

/** One observed Run, shaped the way the stream folds it, for tests. */
const START = "2026-09-20T10:00:00.000Z";
const at = (seconds: number) =>
  new Date(Date.parse(START) + seconds * 1000).toISOString();

export function fixtureRun(
  overrides: Partial<Schema["RunResource"]> = {},
): Schema["RunResource"] {
  return {
    id: "run_2",
    agent_id: "agt_1",
    agent_revision_id: "rev_1",
    completed_at: at(12),
    created_at: START,
    effective_agent_config_digest: "sha256:abc",
    environment_id: null,
    environment_working_directory: null,
    failure: null,
    input: { input: { content: [{ type: "text", text: "Run the checks" }] } },
    input_kind: "agent_input",
    input_text: "Run the checks",
    labels: {},
    lineage_kind: "continue",
    output: null,
    output_text: "Patched the fold.",
    parent_run_id: "run_1",
    pending: null,
    retry_of_run_id: null,
    sealed_at: at(12),
    sealed_state_digest_sha256: null,
    session_id: "ses_1",
    started_at: START,
    status: "completed",
    thread_id: "thr_1",
    trigger_type: "user_input",
    updated_at: at(12),
    version: 3,
    wait_reason: null,
    waiting_at: null,
    ...overrides,
  };
}

export function fixtureThread(
  overrides: Partial<Schema["ThreadResource"]> = {},
): Schema["ThreadResource"] {
  return {
    created_at: START,
    current_run_id: "run_2",
    default_environment_id: null,
    default_environment_working_directory: null,
    head_run_id: "run_2",
    id: "thr_1",
    labels: {},
    origin_kind: "new",
    origin_run_id: null,
    origin_thread_id: null,
    queue_version: 1,
    role: "root",
    session_id: "ses_1",
    session_purpose: "debug",
    updated_at: at(12),
    version: 4,
    ...overrides,
  };
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
        scope: "attempt/harness",
        kind: "llm",
        state: "completed",
        items: [],
        cursor: "1-0",
        startedAt: at(1),
        endedAt: at(2),
        messageCount: 2,
        usage,
      },
      {
        id: "step_edit",
        scope: "attempt/harness",
        kind: "tool",
        name: "edit_file",
        state: "completed",
        items: ["item_edit"],
        cursor: "2-0",
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
    events: [
      {
        id: "event_1",
        type: "run_attempt.started",
        cursor: "0-1",
        occurredAt: START,
        code: null,
        message: null,
        attempt: 1,
        maxAttempts: null,
        delaySeconds: null,
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
      parentId: null,
      firstCursor: "2-0",
      lastCursor: "2-1",
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
      parentId: null,
      firstCursor: "3-0",
      lastCursor: "3-1",
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
  items = fixtureItems(),
  execution = fixtureExecution(),
}: {
  run?: Schema["RunResource"];
  items?: PresentedItem[];
  execution?: RunExecution;
} = {}): RunTimeline {
  return runTimeline({ run, items, execution, coverage: execution.coverage });
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
