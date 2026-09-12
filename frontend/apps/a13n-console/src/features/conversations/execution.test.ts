import { expect, it } from "vitest";
import type { RunEvent } from "@converge.ai/a13n";
import { applyExecution, type Execution } from "./execution";

function event(
  type: string,
  payload: RunEvent["event"]["payload"],
  item?: string,
  scope = "root",
): RunEvent {
  return {
    cursor: `${++sequence}-0`,
    event: {
      event_type: type,
      event_id: `event-${sequence}`,
      run_id: "run",
      thread_id: "thread",
      occurred_at: "2026-09-12T00:00:00Z",
      payload,
      item_id: item,
      run_attempt_id: "attempt",
      harness_run_id: scope,
    },
  };
}
let sequence = 0;
function lifecycle(type: string, request = "request-1", scope = "root") {
  return event(
    "agui.custom",
    {
      name: "a13n.harness.lifecycle",
      value: { event: { payload: { type, request_id: request } } },
    },
    undefined,
    scope,
  );
}
it("counts model and tool calls independently and excludes input and context", () => {
  let state: Execution = { steps: [], items: new Map() };
  for (const entry of [
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
    lifecycle("model_request_started", "request-2"),
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
    lifecycle("model_request_completed", "request-2"),
  ])
    state = applyExecution(state, entry);
  expect(state.steps.map(({ state, items }) => ({ state, items }))).toEqual([
    { state: "completed", items: ["tool"] },
    { state: "completed", items: ["tool"] },
    { state: "completed", items: ["reply"] },
  ]);
});
it("deduplicates starts and separates child requests with the same request ID", () => {
  let state: Execution = { steps: [], items: new Map() };
  for (const entry of [
    lifecycle("model_request_started"),
    lifecycle("model_request_started"),
    lifecycle("model_request_started", "request-1", "child"),
    lifecycle("model_request_failed", "request-1", "child"),
  ])
    state = applyExecution(state, entry);
  expect(state.steps).toHaveLength(2);
  expect(state.steps.map((step) => step.state)).toEqual(["running", "failed"]);
});

it("counts a replacement attempt independently and leaves prior snapshots unchanged", () => {
  const initial: Execution = { steps: [], items: new Map() };
  const first = applyExecution(initial, lifecycle("model_request_started"));
  const next = lifecycle("model_request_started");
  next.event.run_attempt_id = "replacement-attempt";
  const replacement = applyExecution(first, next);
  const completed = applyExecution(
    replacement,
    lifecycle("model_request_completed"),
  );
  expect(first.steps).toHaveLength(1);
  expect(first.steps[0]?.state).toBe("running");
  expect(completed.steps.map((step) => step.state)).toEqual([
    "completed",
    "running",
  ]);
});

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
function fold(...events: RunEvent[]) {
  return events.reduce(applyExecution, {
    steps: [],
    items: new Map(),
  } as Execution);
}

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
    lifecycle("model_request_started", "request-1", "child"),
    lifecycle("model_request_failed", "request-1", "child"),
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
  const state = fold(
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
  expect(state.steps).toHaveLength(1);
  expect(state.steps[0]).toMatchObject({ kind: "handoff", state: "prepared" });
  const completed = applyExecution(
    state,
    custom("a13n.harness.context", {
      type: "handoff_completed",
      operation_id: "handoff-1",
    }),
  );
  expect(completed.steps[0]?.state).toBe("completed");
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
    }),
    custom("a13n.harness.diagnostic", {
      type: "codeact_execution_completed",
      execution_id: "codeact-1",
      status: "failed",
    }),
    event("agui.tool_call_result", { item_state: "completed" }, "outer"),
  );
  expect(state.steps).toHaveLength(2);
  expect(state.steps[0]).toMatchObject({ kind: "codeact", state: "failed" });
  expect(state.steps[1]).toMatchObject({
    kind: "tool",
    name: "read_file",
    state: "completed",
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
    name: "web_search",
    state: "completed",
    detail: { result: "Found" },
  });
});

it("retains retries and arbitrary custom observations outside the count and omits injected input", () => {
  const state = fold(
    custom("a13n.harness.recovery", {
      type: "model_retry_scheduled",
      attempt: 2,
    }),
    event("agui.custom", { name: "plugin.progress", value: "working" }),
    custom("a13n.context.model_input", { content: ["internal context"] }),
  );
  expect(state.steps).toEqual([]);
  expect(state.observations?.map((event) => event.name)).toEqual([
    "model_retry_scheduled",
    "plugin.progress",
  ]);
});

it("ignores stale replay and marks unresolved actions interrupted instead of claiming tool failure", () => {
  const start = tool("shell", "call");
  const first = fold(start);
  expect(applyExecution(first, start)).toBe(first);
  const failed = applyExecution(first, event("run.failed", {}));
  expect(failed.steps[0]?.state).toBe("interrupted");
  expect(first.steps[0]?.state).toBe("running");
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
  });
  expect(state.steps[0]?.childScope).toBeUndefined();
});
