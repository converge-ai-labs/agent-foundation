import { expect, it } from "vitest";
import type { Schema } from "../../shared/api";
import {
  capability,
  custom,
  display,
  finish,
  lifecycle,
  message,
  observation,
  TIME,
  tool,
} from "./display-fixture";
import { runExecution } from "./execution";

const running = { status: "running", sealed_at: null } as const;
function fold(...entries: Parameters<typeof display>) {
  return runExecution(running, display(...entries));
}
function foldRun(
  run: Pick<Schema["RunView"], "status" | "sealed_at">,
  ...entries: Parameters<typeof display>
) {
  return runExecution(run, display(...entries));
}

it("counts model and tool calls independently and excludes input and context", () => {
  const search = tool("search", "tool");
  const state = fold(
    message("input", "user"),
    lifecycle("model_request_started"),
    message("context", "user", { metadata: { display: false } }),
    search,
    lifecycle("model_request_completed"),
    lifecycle("model_request_started", "model-request-2"),
    finish(search, { result: "Found" }),
    message("reply", "assistant"),
    lifecycle("model_request_completed", "model-request-2"),
  );
  expect(state.steps.map(({ state, items }) => ({ state, items }))).toEqual([
    { state: "completed", items: ["tool"] },
    { state: "completed", items: ["tool"] },
    { state: "completed", items: ["reply"] },
  ]);
});

it("deduplicates starts and separates each attempt's requests with the same request ID", () => {
  const state = fold(
    lifecycle("model_request_started"),
    lifecycle("model_request_started"),
    lifecycle("model_request_started", "model-request-1", {}, { attempt: 2 }),
    lifecycle(
      "model_request_failed",
      "model-request-1",
      { error_code: "provider_timeout" },
      { attempt: 2 },
    ),
  );
  expect(state.steps.map((step) => [step.scope, step.state])).toEqual([
    ["1", "running"],
    ["2", "failed"],
  ]);
  expect(state.steps[1]?.errorCode).toBe("provider_timeout");
});

it("records the observed start and terminal time of every step", () => {
  const search = tool("search", "tool", {}, "2026-09-12T00:00:01Z");
  const state = fold(
    lifecycle(
      "model_request_started",
      "model-request-1",
      { message_count: 7 },
      { occurredAt: "2026-09-12T00:00:00Z" },
    ),
    search,
    finish(search, { result: "Found", occurredAt: "2026-09-12T00:00:04Z" }),
    lifecycle(
      "model_request_completed",
      "model-request-1",
      {},
      { occurredAt: "2026-09-12T00:00:05Z" },
    ),
  );
  expect(state.steps[0]).toMatchObject({
    kind: "llm",
    messageCount: 7,
    startedAt: "2026-09-12T00:00:00Z",
    endedAt: "2026-09-12T00:00:05Z",
  });
  expect(state.steps[1]).toMatchObject({
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
) {
  return custom("a13n.harness.usage", {
    type: "usage_report",
    reason,
    records,
  });
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
    lifecycle("model_request_failed", "model-request-1", {
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

it("keeps an unpriced record unavailable rather than zero and deduplicates repeats", () => {
  const state = fold(
    lifecycle("model_request_started"),
    lifecycle("model_request_completed"),
    usageReport([modelRecord("record-1", 0)]),
    usageReport([modelRecord("record-1", 0)]),
  );
  expect(state.steps[0]?.usage?.costUsd).toBeNull();
  expect(state.usage.model).toHaveLength(1);
  expect(state.usage.recordIds).toEqual(["record-1"]);
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

it("attaches an applied edit through its call ID", () => {
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
  const edit = {
    file_path: "/repo/app.ts",
    before: "one\n",
    after: "two\n",
  };
  const correlated = fold(
    tool("edit_file", "edit-call", { arguments: '{"path":"/repo/app.ts"}' }),
    capability("a13n.filesystem.edit_applied", edit),
  );
  expect(correlated.steps[0]?.edit?.filePath).toBe("/repo/app.ts");
  const uncorrelated = fold(
    tool("edit_file", "edit-call", { arguments: '{"path":"/repo/other.ts"}' }),
    capability("a13n.filesystem.edit_applied", edit),
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
});

it("specializes the delegating call into its inline delegation and keeps the child's failure", () => {
  const delegate = tool("delegate", "itm_delegate", { toolCallId: "call-1" });
  const delegation = (action: string, status: string) =>
    custom("a13n.harness.delegation", {
      type: "inline_delegation",
      invocation_id: "delegation-1",
      action,
      subagent: "Researcher",
      status,
      parent_run_id: "harness-root",
      parent_tool_call_id: "call-1",
      child_run_id: "harness-child",
    });
  const state = fold(
    delegate,
    delegation("started", "running"),
    delegation("failed", "failed"),
    finish(delegate, { result: "Child failed" }),
  );
  expect(state.steps).toEqual([
    expect.objectContaining({
      id: "itm_delegate",
      kind: "subagent",
      name: "Researcher",
      state: "failed",
      dispatchOnly: false,
    }),
  ]);
});

it("keeps uncorrelated delegation observable without guessing a tool or inflating the count", () => {
  const state = fold(
    tool("delegate", "one"),
    tool("delegate", "two"),
    custom("a13n.harness.delegation", {
      type: "inline_delegation",
      invocation_id: "delegation-1",
      parent_tool_call_id: "missing",
      child_run_id: "child",
    }),
  );
  expect(state.steps.map((step) => step.kind)).toEqual(["tool", "tool"]);
  expect(state.observations).toHaveLength(1);
});

it("counts a compaction once and keeps its summary after completion", () => {
  const state = fold(
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
    ["compaction", "completed"],
  ]);
  expect(state.steps[0]?.detail).toMatchObject({ summary: "Retained context" });
  expect(state.observations).toHaveLength(0);
});

it("shows handoff preparation and application on its tool without a second step", () => {
  const summarize = tool("summarize", "summary");
  const prepared = [
    summarize,
    custom("a13n.harness.context", {
      type: "handoff_started",
      operation_id: "handoff-1",
    }),
    custom("a13n.context.handoff_summary", {
      tool_call_id: "summary",
      operation_id: "handoff-1",
      summary: "Continue here",
    }),
    finish(summarize),
  ];
  const state = fold(...prepared);
  expect(state.steps).toHaveLength(1);
  expect(state.steps[0]).toMatchObject({ kind: "handoff", state: "prepared" });
  const completed = fold(
    ...prepared,
    custom("a13n.harness.context", {
      type: "handoff_completed",
      operation_id: "handoff-1",
    }),
  );
  expect(completed.steps[0]?.state).toBe("completed");
});

it("nests CodeAct tool calls under their actual execution and retains execution failure", () => {
  const outer = tool("run_code", "outer");
  const state = fold(
    outer,
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
    finish(outer),
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

it("records a scheduled model retry and keeps other observations outside the count", () => {
  const retry = custom("a13n.harness.recovery", {
    type: "model_retry_scheduled",
    error_code: "provider_overloaded",
    attempt: 2,
    max_attempts: 3,
    delay_seconds: 4,
  });
  const state = fold(
    retry,
    observation("plugin.progress", "working"),
    custom("a13n.context.model_input", { content: ["internal context"] }),
  );
  expect(state.steps).toEqual([]);
  expect(state.retries).toEqual([
    {
      id: retry.id,
      position: retry.first_stream_id,
      occurredAt: TIME,
      attempt: 2,
      maxAttempts: 3,
      delaySeconds: 4,
    },
  ]);
  expect(state.observations.map((entry) => entry.name)).toEqual([
    "plugin.progress",
  ]);
});

it("fails a call on the observation that reported it, before that observation", () => {
  const read = tool("read_file", "call");
  const failure = custom("a13n.pydantic_ai.function_tool_result", {});
  const state = fold(
    read,
    failure,
    finish(read, { state: "failed", on: failure }),
  );
  expect(state.steps[0]).toMatchObject({ state: "failed", endedAt: TIME });
  expect(state.observations).toHaveLength(1);
});

it("closes the actions a sealed Run left unresolved without claiming tool failure", () => {
  const sealed = "2026-09-12T00:01:00Z";
  const items = () => [
    tool("shell", "call"),
    lifecycle("model_request_started"),
  ];
  expect(
    foldRun({ status: "failed", sealed_at: sealed }, ...items()).steps.map(
      (step) => [step.state, step.endedAt],
    ),
  ).toEqual([
    ["interrupted", sealed],
    ["interrupted", sealed],
  ]);
  expect(
    foldRun({ status: "completed", sealed_at: sealed }, ...items()).steps[0]
      ?.state,
  ).toBe("unknown");
  // A waiting Run still owes its open calls.
  expect(
    foldRun({ status: "waiting", sealed_at: sealed }, ...items()).steps[0]
      ?.state,
  ).toBe("running");
  expect(fold(...items()).steps[0]?.state).toBe("running");
});

it("identifies an asynchronous delegation from its returned execution without inventing child completion", () => {
  const dispatch = tool("delegate", "dispatch");
  const state = fold(
    dispatch,
    finish(dispatch, {
      result: JSON.stringify({
        execution_id: "execution",
        subagent_name: "reviewer",
        status: "running",
      }),
    }),
  );
  expect(state.steps).toEqual([
    expect.objectContaining({
      kind: "subagent",
      name: "reviewer",
      state: "completed",
      dispatchOnly: true,
      childExecutionId: "execution",
    }),
  ]);
});
