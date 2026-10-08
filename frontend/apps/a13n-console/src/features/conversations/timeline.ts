import type { ContentPart } from "@ag-ui/core";
import { structuredPatch } from "diff";
import { sumCosts } from "../../shared/cost";
import { isRecord } from "../../service-client";
import type { Schema } from "../../shared/api";
import { comparePositions } from "./display";
import { parseItemValue, type PresentedItem } from "./projection";
import type {
  Execution,
  ExecutionCoverage,
  ExecutionObservation,
  ExecutionStep,
} from "./execution";
import { attemptEvent, retryEvent, type LifecycleNotice } from "./lifecycle";
import { reportsTokens, type StepUsage } from "./usage";

/**
 * One Run rendered as a single ordered timeline. This module is pure: it reads
 * the presented Items, the execution view and the Run's attempts, and derives
 * nothing the Run did not report. Values the Run never reported stay `null`,
 * never zero.
 */

export interface TimelineEdit {
  filePath: string;
  before: string;
  after: string;
  diff: { added: number; removed: number; hunks: string[] };
}

interface EntryBase {
  id: string;
  itemId?: string;
  subagentRunId?: string;
  startedAt: string | null;
  endedAt: string | null;
  durationMs: number | null;
  state: string;
}

type StopReason = "tool_use" | "end_turn" | "error" | null;

export interface ModelEntry extends EntryBase {
  kind: "model";
  /** 1-based position among the Run's model requests, in observation order. */
  index: number;
  /** Reported by a usage record; unknown model names are never guessed. */
  model: string | null;
  usage: StepUsage | null;
  /** That record carried token counters; without them tokens stay unknown. */
  reportedUsage: boolean;
  messageCount: number | null;
  errorCode: string | null;
  contextTokens: number | null;
  stopReason: StopReason;
  children: TimelineEntry[];
}

/** Every execution step except a model request, plus provider-native calls. */
type ActionKind = Exclude<ExecutionStep["kind"], "llm"> | "native";

export interface ActionEntry extends EntryBase {
  kind: ActionKind;
  name: string | null;
  arguments: unknown;
  result: unknown;
  resultParts?: ContentPart[];
  failure: unknown;
  edit: TimelineEdit | null;
  providerUsage: StepUsage | null;
  waitingReason: string | null;
  hitl: "question" | "approval" | null;
  /** Asynchronous delegation describes its dispatch, not child completion. */
  dispatchOnly: boolean;
  childExecutionId: string | null;
  callCount: number | null;
  outcome: string | null;
  children: TimelineEntry[];
}

export interface ContentEntry extends EntryBase {
  kind: "reasoning" | "reply" | "guidance";
  text: string;
  /** `a13n.steering-source` provenance for a Harness-enqueued notice. */
  steeringSource: string | null;
  protectedReasoning: boolean;
  failure: unknown;
}

export interface EventEntry extends EntryBase {
  kind: "event";
  notice: LifecycleNotice;
  occurredAt: string | null;
}

export interface OtherEntry extends EntryBase {
  kind: "other";
  count: number;
  observations: ExecutionObservation[];
}

export type TimelineEntry =
  ModelEntry | ActionEntry | ContentEntry | EventEntry | OtherEntry;

interface RunTotals {
  modelCalls: number;
  toolCalls: number;
  inputTokens: number;
  outputTokens: number;
  /** A model usage record was observed; without one, tokens are unknown. */
  reportedUsage: boolean;
  costUsd: string | null;
  /** Every observed record carried a price and the history is complete. */
  costComplete: boolean;
  durationMs: number | null;
}

export interface RunTimeline {
  entries: TimelineEntry[];
  totals: RunTotals;
  coverage: ExecutionCoverage;
}

/**
 * The Run's terminal fact is not an entry: `runOutcome` presents it once,
 * from the Run itself, at the end.
 */
export function runTimeline({
  run,
  attempts = [],
  items,
  execution,
  coverage,
}: {
  run: Schema["RunView"];
  attempts?: readonly Schema["AttemptView"][];
  items: readonly PresentedItem[];
  execution: Execution;
  coverage: ExecutionCoverage;
}): RunTimeline {
  const byId = new Map(items.map((item) => [item.id, item]));
  const ownerOfItem = new Map<string, ExecutionStep>();
  for (const step of execution.steps)
    for (const id of step.items)
      if (step.kind !== "llm") ownerOfItem.set(id, step);
  const requestOfItem = new Map<string, ExecutionStep>();
  for (const step of execution.steps)
    if (step.kind === "llm")
      for (const id of step.items) requestOfItem.set(id, step);
  const delegationOf = new Map<string, string>();
  for (const step of execution.steps)
    if (
      step.kind === "subagent" &&
      isRecord(step.detail) &&
      typeof step.detail.child_run_id === "string"
    )
      delegationOf.set(
        `${step.position.split("-")[0]}/${step.detail.child_run_id}`,
        step.id,
      );
  const childOwner = (position: string, child?: string) =>
    child
      ? (delegationOf.get(`${position.split("-")[0]}/${child}`) ?? null)
      : null;
  /** Nesting uses explicit correlation only; nothing is inferred from order. */
  function parentOf(step: ExecutionStep): string | null {
    if (step.parentId) return step.parentId;
    if (step.kind !== "llm")
      for (const id of step.items) {
        const request = requestOfItem.get(id);
        if (request) return request.id;
      }
    if (step.native) {
      const request = execution.steps.findLast(
        (candidate) =>
          candidate.kind === "llm" &&
          candidate.scope === step.scope &&
          comparePositions(candidate.position, step.position) < 0,
      );
      if (request) return request.id;
    }
    return childOwner(step.position, step.subagentRunId);
  }

  const children = new Map<string, Positioned[]>();
  const roots: Positioned[] = [];
  /** Every parent named here is a step the same fold already created. */
  function place(parent: string | null, positioned: Positioned) {
    if (parent === null) roots.push(positioned);
    else children.set(parent, [...(children.get(parent) ?? []), positioned]);
  }

  let requestIndex = 0;
  for (const step of execution.steps)
    place(parentOf(step), {
      position: step.position,
      entry:
        step.kind === "llm"
          ? modelEntry(step, ++requestIndex, execution)
          : actionEntry(step, step.native ? "native" : step.kind, byId),
    });

  // Content a model request emitted directly: reasoning and replies. Items a
  // tool-family step already represents are that step, not a second entry.
  let firstUserText = true;
  for (const item of [...items].sort((a, b) =>
    comparePositions(a.firstPosition, b.firstPosition),
  )) {
    if (item.display === false || ownerOfItem.has(item.id)) continue;
    const content = contentEntry(item, () => {
      const first = firstUserText;
      firstUserText = false;
      return first;
    });
    // A Run read from retained Items alone reports no execution steps; the
    // call the Item itself recorded is still what happened.
    const entry =
      content ?? (item.kind === "tool_call" ? retainedAction(item) : null);
    if (entry)
      place(
        requestOfItem.get(item.id)?.id ??
          childOwner(item.firstPosition, item.subagentRunId),
        {
          position: item.firstPosition,
          entry,
        },
      );
  }

  // A later attempt starts before anything it displayed.
  for (const attempt of attempts) {
    const entry = attemptEvent(attempt);
    if (entry) place(null, { position: `${attempt.number}-0`, entry });
  }
  for (const retry of execution.retries)
    place(null, { position: retry.position, entry: retryEvent(retry) });

  function assemble(positioned: Positioned[]): TimelineEntry[] {
    return [...positioned]
      .sort((a, b) => comparePositions(a.position, b.position))
      .map(({ entry }) => {
        const nested = children.get(entry.id);
        if (entry.kind === "model") {
          const model = { ...entry, children: assemble(nested ?? []) };
          return { ...model, stopReason: stopReason(model) };
        }
        return nested && isAction(entry)
          ? { ...entry, children: assemble(nested) }
          : entry;
      });
  }
  const entries = assemble(roots);
  if (execution.observations.length)
    entries.push(otherEntry(execution.observations));
  return { entries, totals: runTotals(run, execution, coverage), coverage };
}

interface Positioned {
  position: string;
  entry: TimelineEntry;
}

function isAction(entry: TimelineEntry): entry is ActionEntry {
  return "children" in entry && entry.kind !== "model";
}

function duration(startedAt: string | null, endedAt: string | null) {
  if (!startedAt || !endedAt) return null;
  const span = Date.parse(endedAt) - Date.parse(startedAt);
  return Number.isFinite(span) ? span : null;
}

function modelEntry(
  step: ExecutionStep,
  index: number,
  execution: Execution,
): ModelEntry {
  return {
    kind: "model",
    id: step.id,
    subagentRunId: step.subagentRunId,
    index,
    model: step.usage?.model ?? null,
    usage: step.usage ?? null,
    reportedUsage: reportsTokens(step.usage),
    messageCount: step.messageCount ?? null,
    errorCode: step.errorCode ?? null,
    contextTokens:
      step.contextTokens ?? execution.contextTokens[step.id] ?? null,
    stopReason: null,
    startedAt: step.startedAt,
    endedAt: step.endedAt,
    durationMs: duration(step.startedAt, step.endedAt),
    state: step.state,
    children: [],
  };
}

/** A request's outcome is read from what it actually emitted, never assumed. */
function stopReason(entry: ModelEntry): StopReason {
  if (entry.errorCode || entry.state === "failed") return "error";
  if (entry.children.some(isAction)) return "tool_use";
  if (entry.children.some((child) => child.kind === "reply")) return "end_turn";
  return null;
}

function actionEntry(
  step: ExecutionStep,
  kind: ActionKind,
  byId: ReadonlyMap<string, PresentedItem>,
): ActionEntry {
  const item = step.items.map((id) => byId.get(id)).find(Boolean);
  const detail = isRecord(step.detail) ? step.detail : {};
  return {
    kind,
    id: step.id,
    itemId: item?.id,
    subagentRunId: step.subagentRunId,
    name: step.name ?? null,
    arguments: item
      ? parseItemValue(item.arguments, item.incomplete)
      : parseItemValue(detail.arguments),
    result: item
      ? parseItemValue(item.result, item.incomplete)
      : parseItemValue(detail.result),
    resultParts: item?.resultParts,
    failure: item?.failure ?? null,
    edit: step.edit
      ? {
          ...step.edit,
          diff: editDiff(step.edit.filePath, step.edit.before, step.edit.after),
        }
      : null,
    providerUsage: step.providerUsage ?? null,
    waitingReason: step.waitingReason ?? null,
    hitl: step.hitl ?? null,
    dispatchOnly: step.dispatchOnly ?? false,
    childExecutionId: step.childExecutionId ?? null,
    callCount: step.callCount ?? null,
    outcome: step.outcome ?? null,
    startedAt: step.startedAt,
    endedAt: step.endedAt,
    // A CodeAct execution reports its own measured duration; prefer it over
    // the span between the observations that carried it.
    durationMs: step.durationMs ?? duration(step.startedAt, step.endedAt),
    state: step.state,
    children: [],
  };
}

/** A retained tool-call Item, read as the call it recorded. */
function retainedAction(item: PresentedItem): ActionEntry {
  return {
    kind: "tool",
    id: item.id,
    subagentRunId: item.subagentRunId,
    name: item.toolName || null,
    arguments: parseItemValue(item.arguments, item.incomplete),
    result: parseItemValue(item.result, item.incomplete),
    resultParts: item.resultParts,
    failure: item.failure ?? null,
    edit: null,
    providerUsage: null,
    waitingReason: null,
    hitl: null,
    dispatchOnly: false,
    childExecutionId: null,
    callCount: null,
    outcome: null,
    startedAt: item.startedAt,
    endedAt: item.endedAt,
    durationMs: duration(item.startedAt, item.endedAt),
    state: item.state,
    children: [],
  };
}

/**
 * A content Item becomes an entry only when it is the Agent's own output or
 * guidance sent into a working Run. Run input and injected context are not
 * timeline actions. `first` reports, once, whether this is the Run's leading
 * user text, which is the request itself rather than steering.
 */
function contentEntry(
  item: PresentedItem,
  first: () => boolean,
): ContentEntry | null {
  const base = {
    id: item.id,
    subagentRunId: item.subagentRunId,
    text: item.text,
    steeringSource: item.steeringSource ?? null,
    protectedReasoning: item.protectedReasoning,
    failure: item.failure ?? null,
    startedAt: item.startedAt,
    endedAt: item.endedAt,
    durationMs: duration(item.startedAt, item.endedAt),
    state: item.state,
  };
  if (item.kind === "reasoning_message") return { kind: "reasoning", ...base };
  if (item.kind !== "text_message") return null;
  if (item.role === "assistant") return { kind: "reply", ...base };
  if (item.role !== "user") return null;
  const leading = first();
  return item.steeringSource || !leading ? { kind: "guidance", ...base } : null;
}

function otherEntry(observations: readonly ExecutionObservation[]): OtherEntry {
  return {
    kind: "other",
    id: "observations",
    count: observations.length,
    observations: [...observations],
    startedAt: observations[0]?.occurredAt ?? null,
    endedAt: observations.at(-1)?.occurredAt ?? null,
    durationMs: null,
    state: "observed",
  };
}

/** Patches are computed from the reported before/after text, not from arguments. */
function editDiff(filePath: string, before: string, after: string) {
  const patch = structuredPatch(filePath, filePath, before, after, "", "", {
    context: 3,
  });
  let added = 0;
  let removed = 0;
  const hunks = patch.hunks.map((hunk) => {
    for (const line of hunk.lines) {
      if (line.startsWith("+")) added++;
      else if (line.startsWith("-")) removed++;
    }
    return [
      `@@ -${hunk.oldStart},${hunk.oldLines} +${hunk.newStart},${hunk.newLines} @@`,
      ...hunk.lines,
    ].join("\n");
  });
  return { added, removed, hunks };
}

/**
 * Model and tool calls count separately. Nested CodeAct calls and inline
 * subagent work are their own steps, and the outer call is never counted twice
 * because it is the same step it specializes.
 */
function runTotals(
  run: Schema["RunView"],
  execution: Execution,
  coverage: ExecutionCoverage,
): RunTotals {
  const records = [...execution.usage.model, ...execution.usage.provider];
  const { total, reported } = sumCosts(records.map((usage) => usage.costUsd));
  return {
    modelCalls: execution.steps.filter((step) => step.kind === "llm").length,
    toolCalls: execution.steps.filter((step) => step.kind !== "llm").length,
    inputTokens: execution.usage.model.reduce(
      (sum, usage) => sum + usage.inputTokens,
      0,
    ),
    outputTokens: execution.usage.model.reduce(
      (sum, usage) => sum + usage.outputTokens,
      0,
    ),
    reportedUsage: execution.usage.model.some(reportsTokens),
    costUsd: total,
    costComplete:
      coverage === "complete" &&
      records.length > 0 &&
      reported === records.length,
    durationMs: duration(run.started_at ?? run.created_at, run.sealed_at),
  };
}
