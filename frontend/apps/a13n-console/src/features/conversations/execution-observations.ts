import type { RunEvent } from "@converge.ai/a13n";
import { isObject } from "./projection";
import {
  addStep,
  executionScope,
  updateStep,
  type Execution,
  type ExecutionKind,
} from "./execution-state";

export function applyExecutionObservation(
  current: Execution,
  entry: RunEvent,
): Execution {
  const { event } = entry;
  const scope = executionScope(entry);
  const { name, value } = event.payload;
  if (typeof name !== "string") return current;
  if (
    [
      "a13n.context.model_input",
      "a13n.input.media",
      "a13n.pydantic_ai.enqueued_messages",
    ].includes(name)
  )
    return current;
  const source = isObject(value) ? (value.event ?? value) : value;
  const payload = isObject(source)
    ? isObject(source.payload)
      ? source.payload
      : source
    : {};
  const type = typeof payload.type === "string" ? payload.type : "";
  let next = current;
  if (
    (name === "a13n.pydantic_ai.part_start" ||
      name === "a13n.pydantic_ai.part_end") &&
    isObject(payload.part)
  ) {
    const part = payload.part;
    if (typeof part.tool_call_id === "string") {
      const id = `${scope}/native/${part.tool_call_id}`;
      if (part.part_kind === "builtin-tool-call") {
        next = addStep(next, {
          id,
          scope,
          kind: "tool",
          name: String(part.tool_name ?? ""),
          state: "running",
          items: [],
        });
        return updateStep(next, id, { detail: { arguments: part.args } });
      }
      if (
        part.part_kind === "builtin-tool-return" &&
        name.endsWith(".part_end")
      ) {
        return updateStep(next, id, {
          state: part.outcome === "success" ? "completed" : "failed",
          detail: { result: part.content },
        });
      }
    }
  }

  if (
    name === "a13n.harness.lifecycle" &&
    typeof payload.request_id === "string"
  ) {
    const id = `${scope}/${payload.request_id}`;
    if (type === "model_request_started")
      return addStep(next, {
        id,
        scope,
        kind: "llm",
        state: "running",
        items: [],
      });
    if (type === "model_request_completed" || type === "model_request_failed")
      return updateStep(next, id, {
        state: type === "model_request_completed" ? "completed" : "failed",
      });
  }
  if (name === "a13n.harness.usage" && type === "usage_report") {
    return observe(next, entry, "Usage", payload);
  }
  if (name === "a13n.harness.run_result" && isObject(payload.deferred)) {
    for (const category of ["calls", "approvals"] as const) {
      const calls = payload.deferred[category];
      if (!Array.isArray(calls)) continue;
      for (const call of calls) {
        if (!isObject(call)) continue;
        const step = next.steps.find(
          (step) => step.scope === scope && step.callId === call.tool_call_id,
        );
        if (step)
          next = updateStep(next, step.id, {
            state: "waiting",
            ...(category === "approvals"
              ? { kind: "hitl", name: "Approval" }
              : {}),
            detail: call,
          });
      }
    }
    return next;
  }
  if (
    name === "a13n.harness.delegation" &&
    type === "inline_delegation" &&
    typeof payload.invocation_id === "string"
  ) {
    const parentScope =
      typeof payload.parent_run_id === "string"
        ? executionScope(entry, payload.parent_run_id)
        : scope;
    const tool = next.steps.find(
      (step) =>
        step.scope === parentScope &&
        step.callId === payload.parent_tool_call_id,
    );
    const id = tool?.id ?? `${parentScope}/${payload.invocation_id}`;
    const fields = {
      kind: "subagent" as const,
      name: String(payload.subagent ?? ""),
      state: String(payload.status ?? "running"),
      childScope:
        typeof payload.child_run_id === "string"
          ? executionScope(entry, payload.child_run_id)
          : undefined,
      dispatchOnly: false,
      detail: payload,
    };
    if (tool || next.steps.some((step) => step.id === id))
      return updateStep(next, id, fields);
    // A delegation without a parent tool is an independently observed invocation.
    if (payload.parent_tool_call_id == null)
      return addStep(next, { id, scope: parentScope, items: [], ...fields });
  }
  if (
    name === "a13n.context.handoff_summary" &&
    typeof payload.tool_call_id === "string"
  ) {
    const tool = next.steps.find(
      (step) => step.scope === scope && step.callId === payload.tool_call_id,
    );
    if (tool)
      return updateStep(next, tool.id, {
        kind: "handoff",
        state: "prepared",
        detail: payload,
      });
  }
  if (
    name === "a13n.harness.context" &&
    typeof payload.operation_id === "string"
  ) {
    const kind: ExecutionKind | undefined = type.startsWith("compaction_")
      ? "compaction"
      : type.startsWith("memory_recall_")
        ? "memory"
        : undefined;
    if (kind && !type.endsWith("_skipped")) {
      const id = `${scope}/${payload.operation_id}`;
      if (type.endsWith("_started"))
        return addStep(next, {
          id,
          scope,
          kind,
          state: "running",
          items: [],
          detail: payload,
        });
      if (type.endsWith("_completed") || type.endsWith("_failed"))
        return updateStep(next, id, {
          state: type.endsWith("_failed") ? "failed" : "completed",
          detail: payload,
        });
    }
    if (type.startsWith("handoff_")) {
      const tool = next.steps.find(
        (step) =>
          step.scope === scope &&
          step.kind === "handoff" &&
          isObject(step.detail) &&
          step.detail.operation_id === payload.operation_id,
      );
      if (tool)
        return updateStep(next, tool.id, {
          state: type.endsWith("_completed")
            ? "completed"
            : type.endsWith("_failed")
              ? "failed"
              : "prepared",
          detail: payload,
        });
    }
    // Uncorrelated handoff observations are retained without counting another call.
  }
  if (
    name === "a13n.harness.diagnostic" &&
    typeof payload.execution_id === "string"
  ) {
    const id = `${scope}/${payload.execution_id}`;
    if (type === "codeact_execution_started") {
      const tool = next.steps.find(
        (step) =>
          step.scope === scope && step.callId === payload.outer_tool_call_id,
      );
      if (tool)
        return updateStep(next, tool.id, { kind: "codeact", detail: payload });
    }
    const outer = next.steps.find(
      (step) =>
        step.scope === scope &&
        isObject(step.detail) &&
        step.detail.execution_id === payload.execution_id &&
        step.kind === "codeact",
    );
    if (type === "codeact_execution_completed" && outer)
      return updateStep(next, outer.id, {
        state: String(payload.status),
        detail: payload,
      });
    if (typeof payload.nested_tool_call_id === "string" && outer) {
      const nestedId = `${id}/${payload.nested_tool_call_id}`;
      if (type === "codeact_tool_call_started")
        return addStep(next, {
          id: nestedId,
          scope,
          parentId: outer.id,
          kind: "tool",
          name: String(payload.canonical_tool_name),
          state: "running",
          items: [],
          detail: payload,
        });
      if (type === "codeact_tool_call_completed")
        return updateStep(next, nestedId, {
          state: String(payload.outcome),
          detail: payload,
        });
    }
  }
  if (
    name === "a13n.context.compaction_summary" &&
    typeof payload.operation_id === "string"
  ) {
    const id = `${scope}/${payload.operation_id}`;
    if (next.steps.some((step) => step.id === id))
      return updateStep(next, id, { detail: payload });
  }
  // Preserve custom observations without inventing an execution action or count.
  return observe(next, entry, type || name, source);
}
function observe(
  current: Execution,
  entry: RunEvent,
  name: string,
  detail: unknown,
): Execution {
  return {
    ...current,
    observations: [
      ...(current.observations ?? []),
      { id: entry.event.event_id, name, detail },
    ],
  };
}
