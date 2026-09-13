import type { RunEvent } from "@converge.ai/a13n";
import {
  applyRunEvent,
  compareCursors,
  isObject,
  parseItemValue,
} from "./projection";
import { applyExecutionObservation } from "./execution-observations";
import {
  addStep,
  executionScope,
  updateStep,
  type Execution,
} from "./execution-state";
export type {
  Execution,
  ExecutionStep,
  ExecutionKind,
} from "./execution-state";

export function applyExecution(current: Execution, entry: RunEvent): Execution {
  if (
    current.lastCursor &&
    compareCursors(entry.cursor, current.lastCursor) <= 0
  )
    return current;
  const { event } = entry;
  const scope = executionScope(entry);
  const items = event.item_id
    ? applyRunEvent(current.items, entry)
    : current.items;
  let next: Execution = { ...current, items, lastCursor: entry.cursor };
  const item = event.item_id ? items.get(event.item_id) : undefined;
  if (item?.kind === "tool_call" && item.display !== false) {
    const id = `${scope}/item/${item.id}`;
    if (event.event_type === "agui.tool_call_start") {
      next = addStep(next, {
        id,
        scope,
        kind:
          item.toolName === "ask_user_question"
            ? "hitl"
            : item.toolName === "summarize"
              ? "handoff"
              : "tool",
        name: item.toolName,
        state: "running",
        items: [item.id],
        callId:
          typeof event.payload.source_tool_call_id === "string"
            ? event.payload.source_tool_call_id
            : typeof event.payload.toolCallId === "string"
              ? event.payload.toolCallId
              : undefined,
      });
    }
    if (!current.items.has(item.id)) {
      const model = next.steps.findLast(
        (step) => step.scope === scope && step.kind === "llm",
      );
      if (model)
        next = updateStep(next, model.id, { items: [...model.items, item.id] });
    }
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
        next = updateStep(next, id, {
          kind: "subagent",
          name: result.subagent_name,
          state: item.state,
          dispatchOnly: true,
        });
      }
    }
    const step = next.steps.find((step) => step.id === id);
    if (
      step &&
      (["tool", "hitl"].includes(step.kind) ||
        (step.kind === "handoff" && !isObject(step.detail)))
    ) {
      const state = item.state === "streaming" ? "running" : item.state;
      if (step.state !== state) next = updateStep(next, id, { state });
    }
  } else if (
    item &&
    item.display !== false &&
    !current.items.has(item.id) &&
    (item.kind === "reasoning_message" ||
      (item.kind === "text_message" && item.role === "assistant"))
  ) {
    const model = next.steps.findLast(
      (step) => step.scope === scope && step.kind === "llm",
    );
    if (model)
      next = updateStep(next, model.id, { items: [...model.items, item.id] });
  }
  if (event.event_type === "agui.custom")
    next = applyExecutionObservation(next, entry);
  if (
    ["run.failed", "run.cancelled", "run.completed"].includes(event.event_type)
  ) {
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
            }
          : step,
      ),
    };
  }
  return next;
}
