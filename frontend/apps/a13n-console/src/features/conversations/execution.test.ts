import { expect, it } from "vitest";
import type { RunEvent } from "../../service-client";
import { applyRun, emptyExecution, type RunFold } from "./execution";
import type { PresentedItem } from "./projection";

let sequence = 0;
function event(
  type: string,
  payload: RunEvent["event"]["payload"],
  item?: string,
  scope = "root",
  occurredAt = "2026-09-12T00:00:00Z",
): RunEvent {
  return {
    cursor: `${++sequence}-0`,
    event: {
      event_type: type,
      event_id: `event-${sequence}`,
      run_id: "run",
      thread_id: "thread",
      occurred_at: occurredAt,
      payload,
      item_id: item,
      run_attempt_id: "attempt",
      harness_run_id: scope,
    },
  };
}
function lifecycle(
  type: string,
  request = "model-request-1",
  scope = "root",
  extra: Record<string, unknown> = {},
  occurredAt?: string,
) {
  return event(
    "agui.custom",
    {
      name: "a13n.harness.lifecycle",
      value: { event: { payload: { type, request_id: request, ...extra } } },
    },
    undefined,
    scope,
    occurredAt,
  );
}
function custom(
  name: string,
  payload: Record<string, unknown>,
  scope = "root",
) {
  return event(
    "agui.custom",
    { name, value: { event: { payload } } },
    undefined,
    scope,
  );
}
/** A native Capability event carries its fields on the envelope, not a payload. */
function capability(
  name: string,
  fields: Record<string, unknown>,
  scope = "root",
) {
  return event(
    "agui.custom",
    { name, value: { event: { kind: name, ...fields } } },
    undefined,
    scope,
  );
}
function tool(name: string, id: string, scope = "root", sourceId = id) {
  return event(
    "agui.tool_call_start",
    {
      item_kind: "tool_call",
      toolCallName: name,
      toolCallId: id,
      source_tool_call_id: sourceId,
    },
    id,
    scope,
  );
}
function args(id: string, text: string, scope = "root") {
  return event("agui.tool_call_args", { delta: text }, id, scope);
}
function empty(): RunFold {
  return {
    items: new Map<string, PresentedItem>(),
    execution: emptyExecution(),
  };
}
function run(...events: RunEvent[]): RunFold {
  return events.reduce(applyRun, empty());
}
function fold(...events: RunEvent[]) {
  return run(...events).execution;
}

it("counts model and tool calls independently and excludes input and context", () => {
  const state = fold(
    event(
      "agui.text_message_start",
      { role: "user", item_kind: "text_message" },
      "input",
    ),
    lifecycle("model_request_started"),
    event(
      "agui.text_message_start",
      { role: "user", item_kind: "text_message", metadata: { display: false } },
      "context",
    ),
    event(
      "agui.tool_call_start",
      { item_kind: "tool_call", toolCallName: "search" },
      "tool",
    ),
    lifecycle("model_request_completed"),
    lifecycle("model_request_started", "model-request-2"),
    event(
      "agui.tool_call_result",
      { content: "Found", item_state: "completed" },
      "tool",
    ),
    event(
      "agui.text_message_start",
      { role: "assistant", item_kind: "text_message" },
      "reply",
    ),
    lifecycle("model_request_completed", "model-request-2"),
  );
  expect(state.steps.map(({ state, items }) => ({ state, items }))).toEqual([
    { state: "completed", items: ["tool"] },
    { state: "completed", items: ["tool"] },
    { state: "completed", items: ["reply"] },
  ]);
});

it("deduplicates starts and separates child requests with the same request ID", () => {
  const state = fold(
    lifecycle("model_request_started"),
    lifecycle("model_request_started"),
    lifecycle("model_request_started", "model-request-1", "child"),
    lifecycle("model_request_failed", "model-request-1", "child", {
      error_code: "provider_timeout",
    }),
  );
  expect(state.steps).toHaveLength(2);
  expect(state.steps.map((step) => step.state)).toEqual(["running", "failed"]);
  expect(state.steps[1]?.errorCode).toBe("provider_timeout");
});

it("counts a replacement attempt independently and leaves prior snapshots unchanged", () => {
  const first = run(lifecycle("model_request_started"));
  const next = lifecycle("model_request_started");
  next.event.run_attempt_id = "replacement-attempt";
  const replacement = applyRun(first, next);
  const completed = applyRun(replacement, lifecycle("model_request_completed"));
  expect(first.execution.steps).toHaveLength(1);
  expect(first.execution.steps[0]?.state).toBe("running");
  expect(completed.execution.steps.map((step) => step.state)).toEqual([
    "completed",
    "running",
  ]);
});

it("records the observed start and terminal time of every step and Item", () => {
  const state = run(
    lifecycle(
      "model_request_started",
      "model-request-1",
      "root",
      { message_count: 7 },
      "2026-09-12T00:00:00Z",
    ),
    event(
      "agui.tool_call_start",
      { item_kind: "tool_call", toolCallName: "search" },
      "tool",
      "root",
      "2026-09-12T00:00:01Z",
    ),
    event(
      "agui.tool_call_result",
      { content: "Found", item_state: "completed" },
      "tool",
      "root",
      "2026-09-12T00:00:04Z",
    ),
    lifecycle(
      "model_request_completed",
      "model-request-1",
      "root",
      {},
      "2026-09-12T00:00:05Z",
    ),
  );
  expect(state.execution.steps[0]).toMatchObject({
    kind: "llm",
    messageCount: 7,
    startedAt: "2026-09-12T00:00:00Z",
    endedAt: "2026-09-12T00:00:05Z",
  });
  expect(state.execution.steps[1]).toMatchObject({
    startedAt: "2026-09-12T00:00:01Z",
    endedAt: "2026-09-12T00:00:04Z",
  });
  expect(state.items.get("tool")).toMatchObject({
    startedAt: "2026-09-12T00:00:01Z",
    endedAt: "2026-09-12T00:00:04Z",
  });
});

it("holds a context snapshot until its own request is observed", () => {
  const state = fold(
    custom("a13n.harness.lifecycle", {
      type: "context_snapshot",
      request_index: 1,
      request_tokens: 8200,
      trigger_tokens: 100000,
    }),
    lifecycle("model_request_started", "model-request-1"),
    lifecycle("model_request_started", "model-request-2"),
  );
  expect(state.steps[0]?.contextTokens).toBeUndefined();
  expect(state.steps[1]?.contextTokens).toBe(8200);
  expect(state.observations).toHaveLength(0);
});

function usageReport(
  records: Record<string, unknown>[],
  reason = "model_request",
  scope = "root",
) {
  return custom(
    "a13n.harness.usage",
    { type: "usage_report", reason, records },
    scope,
  );
}
function modelRecord(id: string, ordinal: number, cost: string | null = null) {
  return {
    kind: "model",
    record_id: id,
    response_ordinal: ordinal,
    model_name: "claude-sonnet",
    provider_name: "anthropic",
    pricing_status: cost ? "priced" : "unpriced",
    request_usage: {
      input_tokens: 100,
      output_tokens: 20,
      cache_read_tokens: 5,
      cache_write_tokens: 0,
      ...(cost === null ? {} : { cost }),
    },
  };
}

it("attaches a usage report to the request that just completed and skips a failed one", () => {
  const state = fold(
    lifecycle("model_request_started"),
    lifecycle("model_request_failed", "model-request-1", "root", {
      error_code: "provider_error",
    }),
    lifecycle("model_request_started", "model-request-2"),
    lifecycle("model_request_completed", "model-request-2"),
    usageReport([modelRecord("record-1", 0, "0.004")]),
  );
  expect(state.steps[0]?.usage).toBeUndefined();
  expect(state.steps[1]?.usage).toMatchObject({
    model: "claude-sonnet",
    inputTokens: 100,
    outputTokens: 20,
    cacheReadTokens: 5,
    costUsd: "0.004",
    pricingStatus: "priced",
  });
  expect(state.usage.model).toHaveLength(1);
});

it("keeps an unpriced record unavailable rather than zero and deduplicates replays", () => {
  const first = applyRun(
    run(
      lifecycle("model_request_started"),
      lifecycle("model_request_completed"),
    ),
    usageReport([modelRecord("record-1", 0)]),
  );
  const replayed = applyRun(first, {
    ...usageReport([modelRecord("record-1", 0)]),
    cursor: "900-0",
  });
  expect(first.execution.steps[0]?.usage?.costUsd).toBeNull();
  expect(replayed.execution.usage.model).toHaveLength(1);
  expect(replayed.execution.usage.recordIds).toEqual(["record-1"]);
});

it("attaches a provider receipt to its tool call and retains uncorrelated receipts", () => {
  const state = fold(
    tool("web_fetch", "call-one"),
    usageReport(
      [
        {
          kind: "provider",
          record_id: "provider-1",
          tool_call_id: "call-one",
          usage: {
            provider: "firecrawl",
            cost: "0.002",
            measures: [{ unit: "input_tokens", quantity: 30 }],
          },
        },
        {
          kind: "provider",
          record_id: "provider-2",
          tool_call_id: "unknown-call",
          usage: { provider: "firecrawl", cost: "0.001", measures: [] },
        },
      ],
      "terminal",
    ),
  );
  expect(state.steps[0]?.providerUsage).toMatchObject({
    provider: "firecrawl",
    inputTokens: 30,
    costUsd: "0.002",
  });
  expect(state.usage.provider.map((usage) => usage.costUsd)).toEqual([
    "0.002",
    "0.001",
  ]);
});

it("attaches an applied edit through native call correlation", () => {
  const state = fold(
    tool("edit_file", "edit-call"),
    capability("a13n.filesystem.edit_applied", {
      tool_call_id: "edit-call",
      file_path: "/repo/app.ts",
      before: "one\n",
      after: "two\n",
    }),
  );
  expect(state.steps[0]?.edit).toEqual({
    filePath: "/repo/app.ts",
    before: "one\n",
    after: "two\n",
  });
  expect(state.observations).toHaveLength(0);
});

it("falls back to the running call that named the path and otherwise observes the edit", () => {
  const correlated = fold(
    tool("edit_file", "edit-call"),
    args("edit-call", '{"path":"/repo/app.ts"}'),
    capability("a13n.filesystem.edit_applied", {
      file_path: "/repo/app.ts",
      before: "one\n",
      after: "two\n",
    }),
  );
  expect(correlated.steps[0]?.edit?.filePath).toBe("/repo/app.ts");
  const uncorrelated = fold(
    tool("edit_file", "edit-call"),
    args("edit-call", '{"path":"/repo/other.ts"}'),
    capability("a13n.filesystem.edit_applied", {
      file_path: "/repo/app.ts",
      before: "one\n",
      after: "two\n",
    }),
  );
  expect(uncorrelated.steps[0]?.edit).toBeUndefined();
  expect(uncorrelated.observations.map((entry) => entry.name)).toEqual([
    "edit_applied",
  ]);
});

it("keeps deferred external execution distinct from human questions and approvals", () => {
  const state = fold(
    tool("external", "external"),
    tool("ask_user_question", "question"),
    tool("shell", "approval"),
    custom("a13n.harness.run_result", {
      deferred: {
        calls: [{ tool_call_id: "external" }, { tool_call_id: "question" }],
        approvals: [{ tool_call_id: "approval" }],
      },
    }),
  );
  expect(state.steps.map((step) => [step.kind, step.state])).toEqual([
    ["tool", "waiting"],
    ["hitl", "waiting"],
    ["hitl", "waiting"],
  ]);
  expect(state.steps[2]?.waitingReason).toBe("approval");
  expect(state.steps).toHaveLength(3);
});

it("joins child delegation across envelopes with native call correlation and preserves child failure", () => {
  const state = fold(
    tool("delegate", "presentation-id", "parent", "native-call"),
    custom(
      "a13n.harness.delegation",
      {
        type: "inline_delegation",
        invocation_id: "delegation-1",
        action: "started",
        subagent: "Researcher",
        status: "running",
        parent_run_id: "parent",
        parent_tool_call_id: "native-call",
        child_run_id: "child",
      },
      "child",
    ),
    lifecycle("model_request_started", "model-request-1", "child"),
    lifecycle("model_request_failed", "model-request-1", "child"),
    custom(
      "a13n.harness.delegation",
      {
        type: "inline_delegation",
        invocation_id: "delegation-1",
        action: "failed",
        subagent: "Researcher",
        status: "failed",
        parent_run_id: "parent",
        parent_tool_call_id: "native-call",
        child_run_id: "child",
      },
      "parent",
    ),
    event(
      "agui.tool_call_result",
      { item_state: "completed", content: "Child failed" },
      "presentation-id",
      "parent",
    ),
  );
  expect(state.steps).toHaveLength(2);
  expect(state.steps[0]).toMatchObject({
    kind: "subagent",
    name: "Researcher",
    childScope: "attempt/child",
    state: "failed",
  });
});

it("keeps uncorrelated delegation observable without guessing a tool or inflating the count", () => {
  const state = fold(
    tool("delegate", "one"),
    tool("delegate", "two"),
    custom("a13n.harness.delegation", {
      type: "inline_delegation",
      invocation_id: "delegation-1",
      parent_run_id: "root",
      parent_tool_call_id: "missing",
      child_run_id: "child",
    }),
  );
  expect(state.steps.map((step) => step.kind)).toEqual(["tool", "tool"]);
  expect(state.observations).toHaveLength(1);
});

it("counts automatic context operations once and preserves compaction summary after completion", () => {
  const state = fold(
    custom("a13n.harness.context", {
      type: "memory_recall_started",
      operation_id: "recall-1",
    }),
    custom("a13n.harness.context", {
      type: "memory_recall_completed",
      operation_id: "recall-1",
      result_count: 2,
    }),
    custom("a13n.harness.context", {
      type: "memory_recall_skipped",
      operation_id: "recall-2",
      reason: "empty_query",
    }),
    custom("a13n.harness.context", {
      type: "compaction_started",
      operation_id: "compaction-1",
    }),
    custom("a13n.context.compaction_summary", {
      operation_id: "compaction-1",
      summary: "Retained context",
    }),
    custom("a13n.harness.context", {
      type: "compaction_completed",
      operation_id: "compaction-1",
    }),
  );
  expect(state.steps.map((step) => [step.kind, step.state])).toEqual([
    ["memory", "completed"],
    ["compaction", "completed"],
  ]);
  expect(state.steps[1]?.detail).toMatchObject({ summary: "Retained context" });
  expect(state.observations).toHaveLength(1);
});

it("shows handoff preparation and application on its tool without a second step", () => {
  const state = run(
    tool("summarize", "summary"),
    custom("a13n.harness.context", {
      type: "handoff_started",
      operation_id: "handoff-1",
    }),
    custom("a13n.context.handoff_summary", {
      tool_call_id: "summary",
      operation_id: "handoff-1",
      summary: "Continue here",
    }),
    event("agui.tool_call_result", { item_state: "completed" }, "summary"),
  );
  expect(state.execution.steps).toHaveLength(1);
  expect(state.execution.steps[0]).toMatchObject({
    kind: "handoff",
    state: "prepared",
  });
  const completed = applyRun(
    state,
    custom("a13n.harness.context", {
      type: "handoff_completed",
      operation_id: "handoff-1",
    }),
  );
  expect(completed.execution.steps[0]?.state).toBe("completed");
});

it("nests CodeAct tool calls under their actual execution and retains execution failure", () => {
  const state = fold(
    tool("run_code", "outer"),
    custom("a13n.harness.diagnostic", {
      type: "codeact_execution_started",
      execution_id: "codeact-1",
      outer_tool_call_id: "outer",
      kind: "inline",
    }),
    custom("a13n.harness.diagnostic", {
      type: "codeact_tool_call_started",
      execution_id: "codeact-1",
      nested_tool_call_id: "nested",
      canonical_tool_name: "read_file",
    }),
    custom("a13n.harness.diagnostic", {
      type: "codeact_tool_call_completed",
      execution_id: "codeact-1",
      nested_tool_call_id: "nested",
      outcome: "completed",
      duration_ms: 12,
    }),
    custom("a13n.harness.diagnostic", {
      type: "codeact_execution_completed",
      execution_id: "codeact-1",
      status: "failed",
      duration_ms: 940,
      call_count: 1,
    }),
    event("agui.tool_call_result", { item_state: "completed" }, "outer"),
  );
  expect(state.steps).toHaveLength(2);
  expect(state.steps[0]).toMatchObject({
    kind: "codeact",
    state: "failed",
    outcome: "failed",
    durationMs: 940,
    callCount: 1,
  });
  expect(state.steps[1]).toMatchObject({
    kind: "tool",
    name: "read_file",
    state: "completed",
    durationMs: 12,
    parentId: state.steps[0]!.id,
  });
});

it("counts provider-native tool calls from explicit part boundaries without counting returns twice", () => {
  const part = {
    tool_call_id: "native",
    tool_name: "web_search",
    part_kind: "builtin-tool-call",
    args: { q: "question" },
  };
  const state = fold(
    custom("a13n.pydantic_ai.part_start", { part }),
    custom("a13n.pydantic_ai.part_end", { part }),
    custom("a13n.pydantic_ai.part_end", {
      part: {
        ...part,
        part_kind: "builtin-tool-return",
        content: "Found",
        outcome: "success",
      },
    }),
  );
  expect(state.steps).toHaveLength(1);
  expect(state.steps[0]).toMatchObject({
    kind: "tool",
    native: true,
    name: "web_search",
    state: "completed",
    detail: { result: "Found" },
  });
});

it("retains lifecycle facts and arbitrary custom observations outside the count", () => {
  const state = fold(
    event("run.accepted", {}),
    event("run_attempt.leased", { data: { attempt_number: 2 } }),
    custom("a13n.harness.recovery", {
      type: "model_retry_scheduled",
      attempt: 2,
    }),
    event("agui.custom", { name: "plugin.progress", value: "working" }),
    custom("a13n.context.model_input", { content: ["internal context"] }),
  );
  expect(state.steps).toEqual([]);
  expect(state.events.map((entry) => entry.type)).toEqual([
    "run.accepted",
    "run_attempt.leased",
    "model_retry_scheduled",
  ]);
  expect(state.events[1]?.attempt).toBe(2);
  expect(state.observations.map((entry) => entry.name)).toEqual([
    "plugin.progress",
  ]);
});

it("ignores stale replay and marks unresolved actions interrupted instead of claiming tool failure", () => {
  const start = tool("shell", "call");
  const first = run(start);
  expect(applyRun(first, start).execution).toBe(first.execution);
  const failed = applyRun(first, event("run.failed", {}));
  expect(failed.execution.steps[0]?.state).toBe("interrupted");
  expect(first.execution.steps[0]?.state).toBe("running");
});

it("identifies an asynchronous delegation from its returned execution without inventing child completion", () => {
  const state = fold(
    tool("delegate", "dispatch"),
    event(
      "agui.tool_call_result",
      {
        item_state: "completed",
        content: JSON.stringify({
          execution_id: "execution",
          subagent_name: "reviewer",
          status: "running",
        }),
      },
      "dispatch",
    ),
  );
  expect(state.steps).toHaveLength(1);
  expect(state.steps[0]).toMatchObject({
    kind: "subagent",
    name: "reviewer",
    state: "completed",
    dispatchOnly: true,
    childExecutionId: "execution",
  });
  expect(state.steps[0]?.childScope).toBeUndefined();
});
