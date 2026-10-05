import { isRecord } from "../../service-client";
import type { Schema } from "../../shared/api";
import { comparePositions, type DisplayItem } from "./display";
import { applyObservation } from "./execution-observations";
import { parseItemValue, presentItem, type PresentedItem } from "./projection";
import type { StepUsage } from "./usage";

export type ExecutionKind =
  "llm" | "tool" | "hitl" | "subagent" | "compaction" | "handoff" | "codeact";

/** Actual text before and after one applied edit, as the Toolset reported it. */
export interface StepEdit {
  filePath: string;
  before: string;
  after: string;
}

export interface ExecutionStep {
  id: string;
  /** Attempt and inline Harness run; native IDs are local to this scope. */
  scope: string;
  subagentRunId?: string;
  kind: ExecutionKind;
  name?: string;
  state: string;
  items: string[];
  /** Position and times of the creating and terminal facts. */
  position: string;
  startedAt: string | null;
  endedAt: string | null;
  callId?: string;
  parentId?: string;
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
  occurredAt: string | null;
  detail: unknown;
}
/** A model request the Harness scheduled to send again. */
export interface ModelRetry {
  id: string;
  position: string;
  occurredAt: string | null;
  attempt: number | null;
  maxAttempts: number | null;
  delaySeconds: number | null;
}
export interface ExecutionUsage {
  /** Every model record in report order; Run totals sum these. */
  model: StepUsage[];
  /** Every provider receipt in report order, whether or not it named a call. */
  provider: StepUsage[];
  /** Applied record IDs, because reports repeat across attempts. */
  recordIds: string[];
}
/**
 * How much of a Run's execution history a consumer actually observed.
 * `complete` requires a display that kept every item and stream output
 * without a gap; retained Items alone never establish it.
 */
export type ExecutionCoverage = "complete" | "partial" | "unavailable";

export interface Execution {
  steps: ExecutionStep[];
  observations: ExecutionObservation[];
  retries: ModelRetry[];
  usage: ExecutionUsage;
  /** Context snapshots keyed by the model-request step they precede. */
  contextTokens: Record<string, number>;
}

export function emptyExecution(): Execution {
  return {
    steps: [],
    observations: [],
    retries: [],
    usage: { model: [], provider: [], recordIds: [] },
    contextTokens: {},
  };
}

/** Where and when one fact of the display occurred, and the attempt that reported it. */
export interface Occurrence {
  id: string;
  position: string;
  occurredAt: string | null;
  scope: string;
  subagentRunId?: string;
}

/** An action is settled once a terminal fact fixed its outcome. */
function settled(state: string) {
  return !["running", "waiting", "prepared", "in_progress"].includes(state);
}

/*
 * `runExecution` owns the execution it builds, so the helpers below update it
 * in place and return it; a display of thousands of Items is read again on
 * every published frame.
 */

export function updateStep(
  execution: Execution,
  at: Occurrence,
  id: string,
  update: Partial<ExecutionStep>,
): Execution {
  const index = execution.steps.findLastIndex((step) => step.id === id);
  const step = execution.steps[index];
  if (!step) return execution;
  execution.steps[index] = {
    ...step,
    ...update,
    endedAt:
      update.state !== undefined && settled(update.state)
        ? at.occurredAt
        : (update.endedAt ?? step.endedAt),
    detail:
      isRecord(step.detail) && isRecord(update.detail)
        ? { ...step.detail, ...update.detail }
        : (update.detail ?? step.detail),
  };
  return execution;
}

export function addStep(
  execution: Execution,
  at: Occurrence,
  step: Omit<ExecutionStep, "position" | "startedAt" | "endedAt">,
): Execution {
  if (execution.steps.findLastIndex((existing) => existing.id === step.id) < 0)
    execution.steps.push({
      ...step,
      subagentRunId: at.subagentRunId,
      position: at.position,
      startedAt: at.occurredAt,
      endedAt: null,
    });
  return execution;
}

export function observe(
  execution: Execution,
  at: Occurrence,
  name: string,
  detail: unknown,
): Execution {
  execution.observations.push({
    id: at.id,
    name,
    occurredAt: at.occurredAt,
    detail,
  });
  return execution;
}

/**
 * One fact of the display, at its position: an observation, a message or
 * tool call that opened, or a tool call that finished. A failed call finishes
 * on the observation that reported the failure, before that observation.
 */
type Fact =
  | { kind: "opened"; at: Occurrence; item: PresentedItem; callId?: string }
  | { kind: "finished"; at: Occurrence; item: PresentedItem }
  | { kind: "observation"; at: Occurrence; content: DisplayItem["content"] };

const FACT_ORDER: Record<Fact["kind"], number> = {
  opened: 0,
  finished: 1,
  observation: 2,
};

const attemptOf = (position: string) => position.split("-")[0]!;

function occurrence(
  id: string,
  position: string,
  occurredAt: string | null,
  subagentRunId?: string,
): Occurrence {
  const attempt = attemptOf(position);
  return {
    id,
    position,
    occurredAt,
    subagentRunId,
    scope: subagentRunId ? `${attempt}/child/${subagentRunId}` : attempt,
  };
}

function facts(items: readonly DisplayItem[]): Fact[] {
  const list: Fact[] = [];
  const children = new Set<string>();
  const payloadOf = (content: DisplayItem["content"]) => {
    const value = isRecord(content.value) ? content.value : {};
    const event = isRecord(value.event) ? value.event : {};
    return isRecord(event.payload) ? event.payload : {};
  };
  for (const item of items) {
    const child = item.content.subagentRunId;
    const delegated = payloadOf(item.content).child_run_id;
    const attempt = attemptOf(item.first_stream_id);
    if (typeof child === "string") children.add(`${attempt}/${child}`);
    if (
      item.content.name === "a13n.harness.delegation" &&
      typeof delegated === "string"
    )
      children.add(`${attempt}/${delegated}`);
  }
  for (const item of items) {
    const { content } = item;
    const payload = payloadOf(content);
    const value = isRecord(content.value) ? content.value : {};
    const delegation =
      content.name === "a13n.harness.delegation" &&
      payload.type === "inline_delegation";
    const source = delegation
      ? payload.parent_run_id
      : (content.subagentRunId ?? value.run_id);
    const subagentRunId =
      typeof source === "string" &&
      children.has(`${attemptOf(item.first_stream_id)}/${source}`)
        ? source
        : undefined;
    const opened = occurrence(
      item.id,
      item.first_stream_id,
      item.started_at,
      subagentRunId,
    );
    if (item.kind === "observation") {
      list.push({ kind: "observation", at: opened, content: item.content });
      continue;
    }
    const presented = presentItem(item);
    const callId = item.content.toolCallId;
    list.push({
      kind: "opened",
      at: opened,
      item: presented,
      ...(typeof callId === "string" ? { callId } : {}),
    });
    if (
      item.kind === "tool_call" &&
      (item.state === "completed" || item.state === "failed")
    )
      list.push({
        kind: "finished",
        at: occurrence(
          item.id,
          item.last_stream_id,
          item.ended_at ?? null,
          subagentRunId,
        ),
        item: presented,
      });
  }
  return list.sort(
    (a, b) =>
      comparePositions(a.at.position, b.at.position) ||
      FACT_ORDER[a.kind] - FACT_ORDER[b.kind],
  );
}

/**
 * Content attaches to the model request that was open when the Item began, so
 * a later request never adopts content it did not emit.
 */
function attachToRequest(
  execution: Execution,
  at: Occurrence,
  itemId: string,
): Execution {
  const model = execution.steps.findLast(
    (step) => step.scope === at.scope && step.kind === "llm",
  );
  model?.items.push(itemId);
  return execution;
}

function opened(
  execution: Execution,
  at: Occurrence,
  item: PresentedItem,
  callId: string | undefined,
): Execution {
  if (item.display === false) return execution;
  if (item.kind === "tool_call") {
    const question = item.toolName === "ask_user_question";
    const next = addStep(execution, at, {
      id: item.id,
      scope: at.scope,
      kind: question
        ? "hitl"
        : item.toolName === "summarize"
          ? "handoff"
          : "tool",
      name: item.toolName,
      state: "running",
      items: [item.id],
      ...(question ? { hitl: "question" as const } : {}),
      ...(callId ? { callId } : {}),
    });
    return attachToRequest(next, at, item.id);
  }
  return item.kind === "reasoning_message" ||
    (item.kind === "text_message" && item.role === "assistant")
    ? attachToRequest(execution, at, item.id)
    : execution;
}

/** A finished call is the outcome of its step, unless a later fact specialized it. */
function finished(
  execution: Execution,
  at: Occurrence,
  item: PresentedItem,
): Execution {
  let next = execution;
  if (item.display === false) return next;
  if (item.toolName === "delegate") {
    const result = parseItemValue(item.result, item.incomplete);
    if (
      isRecord(result) &&
      typeof result.execution_id === "string" &&
      typeof result.subagent_name === "string"
    )
      next = updateStep(next, at, item.id, {
        kind: "subagent",
        name: result.subagent_name,
        state: item.state,
        dispatchOnly: true,
        childExecutionId: result.execution_id,
      });
  }
  const step = next.steps.findLast((step) => step.id === item.id);
  if (
    step &&
    step.state !== item.state &&
    (["tool", "hitl"].includes(step.kind) ||
      (step.kind === "handoff" && !isRecord(step.detail)))
  )
    next = updateStep(next, at, step.id, { state: item.state });
  return next;
}

/** Actions a sealed Run left unresolved did not finish on their own. */
function closeAtSeal(
  execution: Execution,
  run: Pick<Schema["RunView"], "status" | "sealed_at">,
): Execution {
  if (
    !run.sealed_at ||
    !["completed", "failed", "cancelled"].includes(run.status)
  )
    return execution;
  return {
    ...execution,
    steps: execution.steps.map((step) =>
      step.state === "running" || step.state === "waiting"
        ? {
            ...step,
            state: run.status === "completed" ? "unknown" : "interrupted",
            endedAt: run.sealed_at,
          }
        : step,
    ),
  };
}

/**
 * The execution view of one Run, read from its display in stream order: model
 * requests, the calls and content they emitted, and what the observations
 * reported about them. The Items themselves stay in the display; steps only
 * name the Items they present.
 */
export function runExecution(
  run: Pick<Schema["RunView"], "status" | "sealed_at">,
  items: readonly DisplayItem[],
): Execution {
  const presented = new Map<string, PresentedItem>();
  let execution = emptyExecution();
  for (const fact of facts(items)) {
    if (fact.kind === "observation")
      execution = applyObservation(execution, fact.at, fact.content, presented);
    else if (fact.kind === "finished")
      execution = finished(execution, fact.at, fact.item);
    else {
      presented.set(fact.item.id, fact.item);
      execution = opened(execution, fact.at, fact.item, fact.callId);
    }
  }
  return closeAtSeal(execution, run);
}
