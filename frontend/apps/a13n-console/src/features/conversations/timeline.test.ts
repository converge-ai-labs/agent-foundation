import { expect, it } from "vitest";
import type { RunEvent } from "../../service-client";
import type { Schema } from "../../shared/api";
import { applyRun, emptyExecution, type RunFold } from "./execution";
import { compareCursors, type PresentedItem } from "./projection";
import { runTimeline, type ModelEntry, type TimelineEntry } from "./timeline";

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
function lifecycle(
  type: string,
  request = "model-request-1",
  scope = "root",
  extra: Record<string, unknown> = {},
) {
  return custom(
    "a13n.harness.lifecycle",
    { type, request_id: request, ...extra },
    scope,
  );
}
function text(
  id: string,
  role: string,
  delta: string,
  scope = "root",
  metadata?: Record<string, unknown>,
) {
  return [
    event(
      "agui.text_message_start",
      { item_kind: "text_message", role, ...(metadata ? { metadata } : {}) },
      id,
      scope,
    ),
    event("agui.text_message_content", { delta }, id, scope),
    event("item.completed", { item_state: "completed" }, id, scope),
  ];
}
function reasoning(id: string, delta: string, scope = "root") {
  return [
    event(
      "agui.reasoning_message_start",
      { item_kind: "reasoning_message" },
      id,
      scope,
    ),
    event("agui.reasoning_message_content", { delta }, id, scope),
  ];
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

const run = {
  id: "run",
  thread_id: "thread",
  session_id: "session",
  status: "completed",
  created_at: "2026-09-12T00:00:00Z",
  started_at: "2026-09-12T00:00:00Z",
  completed_at: "2026-09-12T00:00:09Z",
} as unknown as Schema["RunResource"];

function timeline(...events: RunEvent[]) {
  const state: RunFold = events.reduce(applyRun, {
    items: new Map<string, PresentedItem>(),
    execution: emptyExecution(),
  });
  return runTimeline({
    run,
    items: [...state.items.values()].sort((a, b) =>
      compareCursors(a.firstCursor, b.firstCursor),
    ),
    execution: state.execution,
    coverage: "complete",
  });
}
function kinds(entries: readonly TimelineEntry[]) {
  return entries.map((entry) => entry.kind);
}
function models(entries: readonly TimelineEntry[]) {
  return entries.filter((entry): entry is ModelEntry => entry.kind === "model");
}

it("orders the Run and nests reasoning, tool calls and the reply under their request", () => {
  const { entries } = timeline(
    ...text("input", "user", "Ship the release"),
    lifecycle("model_request_started", "model-request-1", "root", {
      message_count: 4,
    }),
    ...reasoning("thought", "Check the changelog"),
    tool("read_file", "call"),
    event(
      "agui.tool_call_result",
      { content: "ok", item_state: "completed" },
      "call",
    ),
    lifecycle("model_request_completed"),
    lifecycle("model_request_started", "model-request-2"),
    ...text("reply", "assistant", "Released"),
    lifecycle("model_request_completed", "model-request-2"),
  );
  expect(kinds(entries)).toEqual(["model", "model"]);
  const [first, second] = models(entries);
  expect(kinds(first!.children)).toEqual(["reasoning", "tool"]);
  expect(first).toMatchObject({
    index: 1,
    messageCount: 4,
    stopReason: "tool_use",
  });
  expect(kinds(second!.children)).toEqual(["reply"]);
  expect(second).toMatchObject({ index: 2, stopReason: "end_turn" });
});

it("reads a failed request as an error and keeps an unknown model unknown", () => {
  const { entries } = timeline(
    lifecycle("model_request_started"),
    lifecycle("model_request_failed", "model-request-1", "root", {
      error_code: "provider_timeout",
    }),
  );
  expect(models(entries)[0]).toMatchObject({
    stopReason: "error",
    errorCode: "provider_timeout",
    model: null,
    usage: null,
  });
});

it("treats later user text as guidance and never the Run input", () => {
  const { entries } = timeline(
    ...text("input", "user", "Ship the release"),
    lifecycle("model_request_started"),
    ...text("notice", "user", "A subagent finished", "root", {
      "a13n.steering-source": "async_subagent",
    }),
    ...text("steer", "user", "Skip the changelog"),
  );
  const guidance = entries.filter((entry) => entry.kind === "guidance");
  expect(guidance).toHaveLength(2);
  expect(guidance[0]).toMatchObject({
    text: "A subagent finished",
    steeringSource: "async_subagent",
  });
  expect(
    entries.some(
      (entry) => "text" in entry && entry.text === "Ship the release",
    ),
  ).toBe(false);
});

it("nests inline subagent work in its child scope and keeps a dispatch a dispatch", () => {
  const { entries, totals } = timeline(
    lifecycle("model_request_started"),
    tool("delegate", "inline", "root", "native-inline"),
    custom(
      "a13n.harness.delegation",
      {
        type: "inline_delegation",
        invocation_id: "delegation-1",
        action: "started",
        subagent: "Researcher",
        status: "running",
        parent_run_id: "root",
        parent_tool_call_id: "native-inline",
        child_run_id: "child",
      },
      "child",
    ),
    lifecycle("model_request_started", "model-request-1", "child"),
    tool("grep", "child-call", "child"),
    lifecycle("model_request_completed", "model-request-1", "child"),
    tool("delegate", "async", "root"),
    event(
      "agui.tool_call_result",
      {
        item_state: "completed",
        content: JSON.stringify({
          execution_id: "execution-9",
          subagent_name: "reviewer",
          status: "running",
        }),
      },
      "async",
    ),
  );
  const [request] = models(entries);
  const [inline, dispatch] = request!.children;
  expect(inline).toMatchObject({ kind: "subagent", name: "Researcher" });
  const child = (inline as { children: TimelineEntry[] }).children;
  expect(kinds(child)).toEqual(["model"]);
  expect(kinds(models(child)[0]!.children)).toEqual(["tool"]);
  expect(dispatch).toMatchObject({
    kind: "subagent",
    dispatchOnly: true,
    childExecutionId: "execution-9",
  });
  expect(totals).toMatchObject({ modelCalls: 2, toolCalls: 3 });
});

it("nests CodeAct inner calls without counting the outer call twice", () => {
  const { entries, totals } = timeline(
    lifecycle("model_request_started"),
    tool("run_code", "outer"),
    custom("a13n.harness.diagnostic", {
      type: "codeact_execution_started",
      execution_id: "codeact-1",
      outer_tool_call_id: "outer",
    }),
    custom("a13n.harness.diagnostic", {
      type: "codeact_tool_call_started",
      execution_id: "codeact-1",
      nested_tool_call_id: "nested",
      canonical_tool_name: "read_file",
    }),
    custom("a13n.harness.diagnostic", {
      type: "codeact_execution_completed",
      execution_id: "codeact-1",
      status: "completed",
      duration_ms: 1200,
      call_count: 1,
    }),
  );
  const outer = models(entries)[0]!.children[0]!;
  expect(outer).toMatchObject({
    kind: "codeact",
    durationMs: 1200,
    callCount: 1,
    outcome: "completed",
  });
  expect(kinds((outer as { children: TimelineEntry[] }).children)).toEqual([
    "tool",
  ]);
  expect(totals).toMatchObject({ modelCalls: 1, toolCalls: 2 });
});

it("treats a usage record without counters as unknown, never as zero", () => {
  const { entries, totals } = timeline(
    lifecycle("model_request_started"),
    lifecycle("model_request_failed", "model-request-1", "root", {
      error_code: "provider_error",
    }),
    custom("a13n.harness.usage", {
      type: "usage_report",
      reason: "model_request",
      records: [
        {
          kind: "model",
          record_id: "record-1",
          response_ordinal: 0,
          model_name: "claude-sonnet",
          request_usage: { input_tokens: 0, output_tokens: 0 },
        },
      ],
    }),
  );
  const [request] = models(entries);
  expect(request).toMatchObject({ model: "claude-sonnet" });
  expect(request!.reportedUsage).toBe(false);
  expect(totals.reportedUsage).toBe(false);
});

it("sums reported usage and refuses to complete a total that lost a price", () => {
  const { totals } = timeline(
    lifecycle("model_request_started"),
    lifecycle("model_request_completed"),
    custom("a13n.harness.usage", {
      type: "usage_report",
      reason: "model_request",
      records: [
        {
          kind: "model",
          record_id: "record-1",
          response_ordinal: 0,
          model_name: "claude-sonnet",
          request_usage: { input_tokens: 120, output_tokens: 30, cost: "0.01" },
        },
      ],
    }),
    lifecycle("model_request_started", "model-request-2"),
    lifecycle("model_request_completed", "model-request-2"),
    custom("a13n.harness.usage", {
      type: "usage_report",
      reason: "model_request",
      records: [
        {
          kind: "model",
          record_id: "record-2",
          response_ordinal: 1,
          model_name: "claude-sonnet",
          request_usage: { input_tokens: 80, output_tokens: 10 },
        },
      ],
    }),
  );
  expect(totals).toMatchObject({
    inputTokens: 200,
    outputTokens: 40,
    costUsd: "0.01",
    costComplete: false,
    durationMs: 9000,
  });
});

it("computes a patch from the reported edit content", () => {
  const { entries } = timeline(
    lifecycle("model_request_started"),
    tool("edit_file", "edit"),
    capability("a13n.filesystem.edit_applied", {
      tool_call_id: "edit",
      file_path: "/repo/app.ts",
      before: "one\ntwo\nthree\n",
      after: "one\nTWO\nthree\nfour\n",
    }),
  );
  const call = models(entries)[0]!.children[0]!;
  expect(call).toMatchObject({ kind: "tool", name: "edit_file" });
  const edit = (
    call as {
      edit: { diff: { added: number; removed: number; hunks: string[] } };
    }
  ).edit;
  expect(edit.diff).toMatchObject({ added: 2, removed: 1 });
  expect(edit.diff.hunks[0]).toContain("+TWO");
});

it("keeps only the lifecycle facts that carry information, observations last", () => {
  const { entries, totals } = timeline(
    event("run.accepted", {}),
    event("run_attempt.leased", { data: { attempt_number: 1 } }),
    lifecycle("model_request_started"),
    lifecycle("model_request_failed", "model-request-1", "root", {
      error_code: "provider_error",
    }),
    custom("a13n.harness.recovery", {
      type: "model_retry_scheduled",
      attempt: 2,
      max_attempts: 3,
      delay_seconds: 2,
    }),
    event("run.recovery", { reason: "worker_replaced" }),
    event("run_attempt.leased", {
      data: { attempt_number: 2, start_reason: "recovery" },
    }),
    lifecycle("model_request_started", "model-request-1", "second"),
    event("agui.custom", { name: "plugin.progress", value: "working" }),
    event("run.failed", {
      data: { failure: { code: "run_failed", message: "No" } },
    }),
  );
  // Accepting the Run, leasing its first attempt and its terminal fact never
  // become rows: the heading and the Run's own outcome already say them.
  expect(kinds(entries)).toEqual([
    "model",
    "event",
    "event",
    "event",
    "model",
    "other",
  ]);
  const events = entries.filter((entry) => entry.kind === "event");
  expect(events.map((entry) => entry.type)).toEqual([
    "model_retry_scheduled",
    "run.recovery",
    "run_attempt.leased",
  ]);
  expect(events[0]).toMatchObject({
    attempt: 2,
    maxAttempts: 3,
    delaySeconds: 2,
  });
  expect(events[1]).toMatchObject({ code: "worker_replaced" });
  expect(events[2]).toMatchObject({ attempt: 2, code: "recovery" });
  expect(entries.at(-1)).toMatchObject({ kind: "other", count: 1 });
  expect(totals.modelCalls).toBe(2);
});

it("reads a retained tool-call Item as the call it recorded", () => {
  const retained = runTimeline({
    run,
    items: [
      {
        id: "item_call",
        kind: "tool_call",
        state: "completed",
        parentId: null,
        firstCursor: "1-0",
        lastCursor: "1-1",
        startedAt: null,
        endedAt: null,
        text: "",
        role: "assistant",
        toolName: "read_file",
        arguments: '{"path":"src/stream.ts"}',
        result: "ok",
        protectedReasoning: false,
      },
    ],
    execution: emptyExecution(),
    coverage: "unavailable",
  });
  expect(retained.entries).toMatchObject([
    { kind: "tool", name: "read_file", result: "ok" },
  ]);
  // Nothing is inferred about how much history those Items represent.
  expect(retained.totals.toolCalls).toBe(0);
  expect(retained.totals.reportedUsage).toBe(false);
});

it("propagates the consumer's coverage instead of inferring it from content", () => {
  const state: RunFold = [lifecycle("model_request_started")].reduce(applyRun, {
    items: new Map<string, PresentedItem>(),
    execution: emptyExecution(),
  });
  const partial = runTimeline({
    run,
    items: [],
    execution: state.execution,
    coverage: "partial",
  });
  expect(partial.coverage).toBe("partial");
  expect(partial.totals.costComplete).toBe(false);
  expect(
    runTimeline({
      run,
      items: [],
      execution: state.execution,
      coverage: "unavailable",
    }).coverage,
  ).toBe("unavailable");
});
