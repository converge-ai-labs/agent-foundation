import { isRecord } from "../../service-client";
import type { DisplayItem } from "./display";
import type { PresentedItem } from "./projection";
import {
  addStep,
  observe,
  updateStep,
  type Execution,
  type ExecutionStep,
  type Occurrence,
} from "./execution";
import { parseUsageReport } from "./usage";

/**
 * Fold one observation into the execution view. `items` holds the messages and
 * tool calls that opened before it.
 */
export function applyObservation(
  current: Execution,
  at: Occurrence,
  content: DisplayItem["content"],
  items: ReadonlyMap<string, PresentedItem>,
): Execution {
  const { scope } = at;
  const { name, value } = content;
  if (typeof name !== "string") return current;
  if (
    [
      "a13n.context.model_input",
      "a13n.input.media",
      "a13n.pydantic_ai.enqueued_messages",
    ].includes(name)
  )
    return current;
  const source = isRecord(value) ? (value.event ?? value) : value;
  const payload = isRecord(source)
    ? isRecord(source.payload)
      ? source.payload
      : source
    : {};
  const type = typeof payload.type === "string" ? payload.type : "";
  let next = current;
  if (
    name === "a13n.display.model_request" ||
    name === "a13n.display.context_operation"
  ) {
    const model = name === "a13n.display.model_request";
    const status = String(payload.status ?? "unknown");
    return addStep(next, at, {
      id: at.id,
      scope,
      kind: model
        ? "llm"
        : payload.operation === "handoff"
          ? "handoff"
          : "compaction",
      state:
        status === "succeeded"
          ? "completed"
          : status === "pending"
            ? "running"
            : status,
      items: [],
      messageCount: numeric(payload.message_count) ?? undefined,
      contextTokens: numeric(payload.request_tokens) ?? undefined,
      errorCode:
        typeof payload.error_code === "string" ? payload.error_code : undefined,
      detail: payload,
    });
  }
  if (
    (name === "a13n.pydantic_ai.part_start" ||
      name === "a13n.pydantic_ai.part_end") &&
    isRecord(payload.part)
  ) {
    const part = payload.part;
    if (typeof part.tool_call_id === "string") {
      const id = `${scope}/native/${part.tool_call_id}`;
      if (part.part_kind === "builtin-tool-call") {
        next = addStep(next, at, {
          id,
          scope,
          kind: "tool",
          native: true,
          name: String(part.tool_name ?? ""),
          state: "running",
          items: [],
        });
        return updateStep(next, at, id, {
          detail: { arguments: part.args },
        });
      }
      if (
        part.part_kind === "builtin-tool-return" &&
        name.endsWith(".part_end")
      ) {
        return updateStep(next, at, id, {
          state: part.outcome === "success" ? "completed" : "failed",
          detail: { result: part.content },
        });
      }
    }
  }

  if (name === "a13n.harness.lifecycle") {
    if (
      type === "context_snapshot" &&
      typeof payload.request_index === "number"
    )
      return snapshotContext(next, at, scope, payload);
    if (typeof payload.request_id === "string") {
      const id = `${scope}/${payload.request_id}`;
      if (type === "model_request_started")
        return addStep(next, at, {
          id,
          scope,
          kind: "llm",
          state: "running",
          items: [],
          messageCount:
            typeof payload.message_count === "number"
              ? payload.message_count
              : undefined,
          contextTokens: next.contextTokens[id],
        });
      if (type === "model_request_completed")
        return updateStep(next, at, id, { state: "completed" });
      if (type === "model_request_failed")
        return updateStep(next, at, id, {
          state: "failed",
          errorCode:
            typeof payload.error_code === "string"
              ? payload.error_code
              : undefined,
        });
    }
  }
  if (name === "a13n.harness.recovery" && type === "model_retry_scheduled") {
    next.retries.push({
      id: at.id,
      position: at.position,
      occurredAt: at.occurredAt,
      attempt: numeric(payload.attempt) ?? null,
      maxAttempts: numeric(payload.max_attempts) ?? null,
      delaySeconds: numeric(payload.delay_seconds) ?? null,
    });
    return next;
  }
  if (name === "a13n.harness.usage" && type === "usage_report")
    return applyUsageReport(next, at, scope, payload);
  if (name === "a13n.filesystem.edit_applied")
    return applyEdit(next, at, scope, payload, items);
  if (name === "a13n.harness.run_result" && isRecord(payload.deferred)) {
    for (const category of ["calls", "approvals"] as const) {
      const calls = payload.deferred[category];
      if (!Array.isArray(calls)) continue;
      for (const call of calls) {
        if (!isRecord(call)) continue;
        const step = next.steps.find(
          (step) => step.scope === scope && step.callId === call.tool_call_id,
        );
        if (step)
          next = updateStep(next, at, step.id, {
            state: "waiting",
            waitingReason:
              category === "approvals" ? "approval" : "external_call",
            ...(category === "approvals"
              ? {
                  kind: "hitl" as const,
                  hitl: "approval" as const,
                  name: "Approval",
                }
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
    // The display observes one Harness run per attempt, so the delegating
    // run is always this scope's own.
    const tool = next.steps.find(
      (step) =>
        step.scope === scope && step.callId === payload.parent_tool_call_id,
    );
    const id = tool?.id ?? `${scope}/${payload.invocation_id}`;
    const fields = {
      kind: "subagent" as const,
      name: String(payload.subagent ?? ""),
      state: String(payload.status ?? "running"),
      dispatchOnly: false,
      detail: payload,
    };
    if (tool || next.steps.some((step) => step.id === id))
      return updateStep(next, at, id, fields);
    // A delegation without a parent tool is an independently observed invocation.
    if (payload.parent_tool_call_id == null)
      return addStep(next, at, { id, scope, items: [], ...fields });
  }
  if (
    name === "a13n.context.handoff_summary" &&
    typeof payload.tool_call_id === "string"
  ) {
    const tool = next.steps.find(
      (step) => step.scope === scope && step.callId === payload.tool_call_id,
    );
    if (tool)
      return updateStep(next, at, tool.id, {
        kind: "handoff",
        state: "prepared",
        detail: payload,
      });
  }
  if (
    name === "a13n.harness.context" &&
    typeof payload.operation_id === "string"
  ) {
    if (type.startsWith("compaction_")) {
      const id = `${scope}/${payload.operation_id}`;
      if (type.endsWith("_started"))
        return addStep(next, at, {
          id,
          scope,
          kind: "compaction",
          state: "running",
          items: [],
          detail: payload,
        });
      if (type.endsWith("_completed") || type.endsWith("_failed"))
        return updateStep(next, at, id, {
          state: type.endsWith("_failed") ? "failed" : "completed",
          detail: payload,
        });
    }
    if (type.startsWith("handoff_")) {
      const tool = next.steps.find(
        (step) =>
          step.scope === scope &&
          step.kind === "handoff" &&
          isRecord(step.detail) &&
          step.detail.operation_id === payload.operation_id,
      );
      if (tool)
        return updateStep(next, at, tool.id, {
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
        return updateStep(next, at, tool.id, {
          kind: "codeact",
          detail: payload,
        });
    }
    const outer = next.steps.find(
      (step) =>
        step.scope === scope &&
        isRecord(step.detail) &&
        step.detail.execution_id === payload.execution_id &&
        step.kind === "codeact",
    );
    if (type === "codeact_execution_completed" && outer)
      return updateStep(next, at, outer.id, {
        state: String(payload.status),
        outcome: String(payload.status),
        durationMs: numeric(payload.duration_ms),
        callCount: numeric(payload.call_count),
        detail: payload,
      });
    if (typeof payload.nested_tool_call_id === "string" && outer) {
      const nestedId = `${id}/${payload.nested_tool_call_id}`;
      if (type === "codeact_tool_call_started")
        return addStep(next, at, {
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
        return updateStep(next, at, nestedId, {
          state: String(payload.outcome),
          outcome: String(payload.outcome),
          durationMs: numeric(payload.duration_ms),
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
      return updateStep(next, at, id, { detail: payload });
  }
  // Preserve custom observations without inventing an execution action or count.
  return observe(next, at, type || name, source);
}

function numeric(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : undefined;
}

/**
 * A context snapshot describes the request that has not started yet. Its
 * `request_index` names that request's step identity directly, so the value
 * waits until the request is observed.
 */
function snapshotContext(
  current: Execution,
  at: Occurrence,
  scope: string,
  payload: Record<string, unknown>,
): Execution {
  const tokens = numeric(payload.request_tokens);
  const index = numeric(payload.request_index);
  if (tokens === undefined || index === undefined) return current;
  const id = `${scope}/model-request-${index + 1}`;
  const next: Execution = {
    ...current,
    contextTokens: { ...current.contextTokens, [id]: tokens },
  };
  return next.steps.some((step) => step.id === id)
    ? updateStep(next, at, id, { contextTokens: tokens })
    : next;
}

/**
 * The edit event carries its originating native tool-call ID in the envelope.
 * Without it, the only truthful fallback is the running tool call in the same
 * scope whose arguments name that path; otherwise the edit stays an observation.
 */
function applyEdit(
  current: Execution,
  at: Occurrence,
  scope: string,
  payload: Record<string, unknown>,
  items: ReadonlyMap<string, PresentedItem>,
): Execution {
  const filePath = payload.file_path;
  if (
    typeof filePath !== "string" ||
    typeof payload.before !== "string" ||
    typeof payload.after !== "string"
  )
    return observe(current, at, "edit_applied", payload);
  const edit = { filePath, before: payload.before, after: payload.after };
  const step =
    (typeof payload.tool_call_id === "string"
      ? current.steps.find(
          (step) =>
            step.scope === scope && step.callId === payload.tool_call_id,
        )
      : undefined) ??
    current.steps.findLast(
      (step) =>
        step.scope === scope &&
        step.state === "running" &&
        step.items.some((id) => items.get(id)?.arguments.includes(filePath)),
    );
  return step
    ? updateStep(current, at, step.id, { edit })
    : observe(current, at, "edit_applied", payload);
}

/**
 * Usage reporting is a boundary, not a correlation key: the `model_request`
 * report arrives immediately after its request completed. Attach a model record
 * to the newest completed request in the same scope that has no usage yet, and
 * otherwise fall back to `response_ordinal` as the request position. Records are
 * deduplicated by `record_id` because reports repeat across replay.
 */
function applyUsageReport(
  current: Execution,
  at: Occurrence,
  scope: string,
  payload: Record<string, unknown>,
): Execution {
  const report = parseUsageReport(payload);
  if (!report) return observe(current, at, "usage_report", payload);
  const applied = new Set(current.usage.recordIds);
  let next = current;
  const model = [...current.usage.model];
  const provider = [...current.usage.provider];
  for (const record of report.model) {
    if (applied.has(record.recordId)) continue;
    applied.add(record.recordId);
    const scoped = next.steps.filter(
      (step) => step.scope === scope && step.kind === "llm",
    );
    const step: ExecutionStep | undefined =
      scoped.findLast((step) => step.state === "completed" && !step.usage) ??
      scoped[record.responseOrdinal];
    if (step) next = updateStep(next, at, step.id, { usage: record.usage });
    model.push(record.usage);
  }
  for (const record of report.provider) {
    if (applied.has(record.recordId)) continue;
    applied.add(record.recordId);
    const step = next.steps.find(
      (step) =>
        step.scope === scope &&
        ((record.toolCallId !== null && step.callId === record.toolCallId) ||
          (record.toolId !== null && step.name === record.toolId)),
    );
    if (step)
      next = updateStep(next, at, step.id, { providerUsage: record.usage });
    provider.push(record.usage);
  }
  return {
    ...next,
    usage: { model, provider, recordIds: [...applied] },
  };
}
