import type { RunEvent } from "../../service-client";
import {
  applyRunEvent,
  compareCursors,
  isObject,
  parseItemValue,
  type PresentedItem,
} from "./projection";
import { applyExecutionObservation } from "./execution-observations";
import type { StepUsage } from "./usage";

export type ExecutionKind =
  | "llm"
  | "tool"
  | "hitl"
  | "subagent"
  | "compaction"
  | "handoff"
  | "memory"
  | "codeact";

/** Actual text before and after one applied edit, as the Toolset reported it. */
export interface StepEdit {
  filePath: string;
  before: string;
  after: string;
}

export interface ExecutionStep {
  id: string;
  scope: string;
  kind: ExecutionKind;
  name?: string;
  state: string;
  items: string[];
  /** Cursor and timestamps of the creating and terminal observations. */
  cursor: string;
  startedAt: string;
  endedAt: string | null;
  callId?: string;
  parentId?: string;
  childScope?: string;
  dispatchOnly?: boolean;
  /** Execution identity returned by an asynchronous delegation dispatch. */
  childExecutionId?: string;
  /** A provider-native call observed from model part boundaries. */
  native?: boolean;
  /** Human interaction presentation, when a tool specializes into one. */
  hitl?: "question" | "approval";
  waitingReason?: string;
  /** Model request facts. */
  messageCount?: number;
  errorCode?: string;
  contextTokens?: number;
  usage?: StepUsage;
  providerUsage?: StepUsage;
  edit?: StepEdit;
  /** CodeAct execution facts. */
  durationMs?: number;
  callCount?: number;
  outcome?: string;
  detail?: unknown;
}
export interface ExecutionObservation {
  id: string;
  name: string;
  occurredAt: string;
  detail: unknown;
}
/** One Run or RunAttempt lifecycle fact observed on the stream. */
export interface ExecutionEvent {
  id: string;
  type: string;
  cursor: string;
  occurredAt: string;
  code: string | null;
  message: string | null;
  attempt: number | null;
  /** Only a scheduled model retry reports a budget and a delay. */
  maxAttempts: number | null;
  delaySeconds: number | null;
}
export interface ExecutionUsage {
  /** Every model record in report order; Run totals sum these. */
  model: StepUsage[];
  /** Every provider receipt in report order, whether or not it named a call. */
  provider: StepUsage[];
  /** Applied record IDs, because reports repeat across replay. */
  recordIds: string[];
}
/**
 * How much of a Run's execution history a consumer actually observed.
 * `complete` requires an uninterrupted attachment from the stream origin;
 * retained Items alone never establish it.
 */
export type ExecutionCoverage = "complete" | "partial" | "unavailable";

export interface Execution {
  steps: ExecutionStep[];
  observations: ExecutionObservation[];
  events: ExecutionEvent[];
  usage: ExecutionUsage;
  /** Context snapshots keyed by the model-request step they precede. */
  contextTokens: Record<string, number>;
  lastCursor?: string;
}

export function emptyExecution(): Execution {
  return {
    steps: [],
    observations: [],
    events: [],
    usage: { model: [], provider: [], recordIds: [] },
    contextTokens: {},
  };
}

export function executionScope(
  entry: RunEvent,
  harnessRunId = entry.event.harness_run_id ?? "",
) {
  return `${entry.event.run_attempt_id ?? ""}/${harnessRunId}`;
}

/** An action is settled once a terminal observation fixed its outcome. */
function settled(state: string) {
  return !["running", "waiting", "prepared", "in_progress"].includes(state);
}

export function updateStep(
  execution: Execution,
  entry: RunEvent,
  id: string,
  update: Partial<ExecutionStep>,
): Execution {
  return {
    ...execution,
    steps: execution.steps.map((step) =>
      step.id === id
        ? {
            ...step,
            ...update,
            endedAt:
              update.state !== undefined && settled(update.state)
                ? entry.event.occurred_at
                : (update.endedAt ?? step.endedAt),
            detail:
              isObject(step.detail) && isObject(update.detail)
                ? { ...step.detail, ...update.detail }
                : (update.detail ?? step.detail),
          }
        : step,
    ),
  };
}

export function addStep(
  execution: Execution,
  entry: RunEvent,
  step: Omit<ExecutionStep, "cursor" | "startedAt" | "endedAt">,
): Execution {
  return execution.steps.some((existing) => existing.id === step.id)
    ? execution
    : {
        ...execution,
        steps: [
          ...execution.steps,
          {
            ...step,
            cursor: entry.cursor,
            startedAt: entry.event.occurred_at,
            endedAt: null,
          },
        ],
      };
}

export function observe(
  execution: Execution,
  entry: RunEvent,
  name: string,
  detail: unknown,
): Execution {
  return {
    ...execution,
    observations: [
      ...execution.observations,
      {
        id: entry.event.event_id,
        name,
        occurredAt: entry.event.occurred_at,
        detail,
      },
    ],
  };
}

const LIFECYCLE_EVENTS = [
  "run.accepted",
  "run_attempt.leased",
  "run.recovery",
  "run.waiting",
  "run.completed",
  "run.failed",
  "run.cancelled",
];
const TERMINAL_EVENTS = ["run.completed", "run.failed", "run.cancelled"];

/** Retain one lifecycle fact without creating an execution action or count. */
export function recordEvent(
  execution: Execution,
  entry: RunEvent,
  type: string,
  fields: Pick<
    ExecutionEvent,
    "code" | "message" | "attempt" | "maxAttempts" | "delaySeconds"
  >,
): Execution {
  return {
    ...execution,
    events: [
      ...execution.events,
      {
        id: entry.event.event_id,
        type,
        cursor: entry.cursor,
        occurredAt: entry.event.occurred_at,
        ...fields,
      },
    ],
  };
}

function lifecycleFact(entry: RunEvent): ExecutionEvent {
  const { event } = entry;
  const data = isObject(event.payload.data) ? event.payload.data : {};
  const failure = isObject(data.failure) ? data.failure : {};
  // Why this fact happened, in the order the observation reports it: a
  // failure, a recovery reason, a wait reason, or why an attempt started.
  const code =
    typeof failure.code === "string"
      ? failure.code
      : typeof event.payload.reason === "string"
        ? event.payload.reason
        : typeof data.wait_reason === "string"
          ? data.wait_reason
          : typeof data.start_reason === "string"
            ? data.start_reason
            : null;
  return {
    id: event.event_id,
    type: event.event_type,
    cursor: entry.cursor,
    occurredAt: event.occurred_at,
    code,
    message: typeof failure.message === "string" ? failure.message : null,
    attempt:
      typeof data.attempt_number === "number" ? data.attempt_number : null,
    maxAttempts: null,
    delaySeconds: null,
  };
}

/**
 * Content attaches to the model request that was open when the Item began, so
 * a later request never adopts content it did not emit.
 */
function attachToRequest(
  execution: Execution,
  entry: RunEvent,
  scope: string,
  item: PresentedItem,
): Execution {
  const itemId = item.id;
  if (
    item.firstCursor !== entry.cursor ||
    execution.steps.some(
      (step) => step.kind === "llm" && step.items.includes(itemId),
    )
  )
    return execution;
  const model = execution.steps.findLast(
    (step) => step.scope === scope && step.kind === "llm",
  );
  if (!model) return execution;
  return {
    ...execution,
    steps: execution.steps.map((step) =>
      step.id === model.id ? { ...step, items: [...step.items, itemId] } : step,
    ),
  };
}

/**
 * Fold one Run event into the execution view. `items` is the shared projection
 * already updated with this event; execution keeps no second copy.
 */
function applyExecution(
  current: Execution,
  entry: RunEvent,
  items: ReadonlyMap<string, PresentedItem>,
): Execution {
  if (
    current.lastCursor &&
    compareCursors(entry.cursor, current.lastCursor) <= 0
  )
    return current;
  const { event } = entry;
  const scope = executionScope(entry);
  let next: Execution = { ...current, lastCursor: entry.cursor };
  const item = event.item_id ? items.get(event.item_id) : undefined;
  if (item?.kind === "tool_call" && item.display !== false) {
    const id = `${scope}/item/${item.id}`;
    if (event.event_type === "agui.tool_call_start") {
      const question = item.toolName === "ask_user_question";
      next = addStep(next, entry, {
        id,
        scope,
        kind: question
          ? "hitl"
          : item.toolName === "summarize"
            ? "handoff"
            : "tool",
        name: item.toolName,
        state: "running",
        items: [item.id],
        ...(question ? { hitl: "question" as const } : {}),
        callId:
          typeof event.payload.source_tool_call_id === "string"
            ? event.payload.source_tool_call_id
            : typeof event.payload.toolCallId === "string"
              ? event.payload.toolCallId
              : undefined,
      });
    }
    next = attachToRequest(next, entry, scope, item);
    if (
      item.toolName === "delegate" &&
      event.event_type === "agui.tool_call_result"
    ) {
      const result = parseItemValue(item.result);
      if (
        isObject(result) &&
        typeof result.execution_id === "string" &&
        typeof result.subagent_name === "string"
      ) {
        next = updateStep(next, entry, id, {
          kind: "subagent",
          name: result.subagent_name,
          state: item.state,
          dispatchOnly: true,
          childExecutionId: result.execution_id,
        });
      }
    }
    const step = next.steps.find((step) => step.id === id);
    if (
      step &&
      (["tool", "hitl"].includes(step.kind) ||
        (step.kind === "handoff" && !isObject(step.detail)))
    ) {
      const state = item.state === "in_progress" ? "running" : item.state;
      if (step.state !== state) next = updateStep(next, entry, id, { state });
    }
  } else if (
    item &&
    item.display !== false &&
    (item.kind === "reasoning_message" ||
      (item.kind === "text_message" && item.role === "assistant"))
  ) {
    next = attachToRequest(next, entry, scope, item);
  }
  if (event.event_type === "agui.custom")
    next = applyExecutionObservation(next, entry, items);
  if (LIFECYCLE_EVENTS.includes(event.event_type))
    next = { ...next, events: [...next.events, lifecycleFact(entry)] };
  if (TERMINAL_EVENTS.includes(event.event_type)) {
    next = {
      ...next,
      steps: next.steps.map((step) =>
        step.state === "running" || step.state === "waiting"
          ? {
              ...step,
              state:
                event.event_type === "run.completed"
                  ? "unknown"
                  : "interrupted",
              endedAt: event.occurred_at,
            }
          : step,
      ),
    };
  }
  return next;
}

/** One stream event applied once: Items first, then the execution view. */
export interface RunFold {
  items: Map<string, PresentedItem>;
  execution: Execution;
}
export function applyRun(state: RunFold, entry: RunEvent): RunFold {
  const items = applyRunEvent(state.items, entry);
  return { items, execution: applyExecution(state.execution, entry, items) };
}
