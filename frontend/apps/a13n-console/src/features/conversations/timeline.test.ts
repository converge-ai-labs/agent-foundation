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
  reasoning,
  tool,
} from "./display-fixture";
import { emptyExecution, runExecution } from "./execution";
import { presentItems } from "./projection";
import { runTimeline, type ModelEntry, type TimelineEntry } from "./timeline";
import { fixtureAttempt } from "./transcript/fixture";

const run = {
  id: "run",
  thread_id: "thread",
  session_id: "session",
  status: "completed",
  created_at: "2026-09-12T00:00:00Z",
  started_at: "2026-09-12T00:00:00Z",
  sealed_at: "2026-09-12T00:00:09Z",
} as unknown as Schema["RunView"];

function timeline(...entries: Parameters<typeof display>) {
  return timelineOf([], ...entries);
}
function timelineOf(
  attempts: Schema["AttemptView"][],
  ...entries: Parameters<typeof display>
) {
  const items = display(...entries);
  return runTimeline({
    run,
    attempts,
    items: presentItems(items),
    execution: runExecution(run, items),
    coverage: "complete",
  });
}
function kinds(entries: readonly TimelineEntry[]) {
  return entries.map((entry) => entry.kind);
}
function models(entries: readonly TimelineEntry[]) {
  return entries.filter((entry): entry is ModelEntry => entry.kind === "model");
}
const text = (
  id: string,
  role: string,
  value: string,
  metadata?: Record<string, unknown>,
) => message(id, role, { text: value, ...(metadata ? { metadata } : {}) });

it("orders the Run and nests reasoning, tool calls and the reply under their request", () => {
  const call = tool("read_file", "call");
  const { entries } = timeline(
    text("input", "user", "Ship the release"),
    lifecycle("model_request_started", "model-request-1", {
      message_count: 4,
    }),
    reasoning("thought", "Check the changelog"),
    call,
    finish(call, { result: "ok" }),
    lifecycle("model_request_completed"),
    lifecycle("model_request_started", "model-request-2"),
    text("reply", "assistant", "Released"),
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
    lifecycle("model_request_failed", "model-request-1", {
      error_code: "model_request_failed",
      status_code: 400,
      detail: "prompt is too long: 1006745 tokens > 1000000 maximum",
    }),
  );
  expect(models(entries)[0]).toMatchObject({
    stopReason: "error",
    errorCode: "model_request_failed",
    errorStatus: 400,
    errorDetail: "prompt is too long: 1006745 tokens > 1000000 maximum",
    model: null,
    usage: null,
  });
});

it("treats later user text as guidance and never the Run input", () => {
  const { entries } = timeline(
    text("input", "user", "Ship the release"),
    lifecycle("model_request_started"),
    text("notice", "user", "A subagent finished", {
      "a13n.steering-source": "async_subagent",
    }),
    text("steer", "user", "Skip the changelog"),
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

it("reads guidance before the request that read it", () => {
  const { entries } = timeline(
    text("input", "user", "Which colour is the banner?"),
    lifecycle("model_request_started", "model-request-1"),
    text("first", "assistant", "Cobalt"),
    lifecycle("model_request_completed", "model-request-1"),
    lifecycle("model_request_started", "model-request-2"),
    text("steer", "user", "Use the new palette"),
    text("second", "assistant", "Amber"),
    lifecycle("model_request_completed", "model-request-2"),
  );
  expect(kinds(entries)).toEqual(["model", "guidance", "model"]);
  expect(kinds(models(entries)[1]!.children)).toEqual(["reply"]);
});

it("keeps input hidden from Chat as an entry read before its request", () => {
  const reminder =
    "<system-reminder>Environment mounts changed</system-reminder>";
  const { entries } = timeline(
    lifecycle("model_request_started", "model-request-1"),
    text("reminder", "user", reminder, { display: false }),
    text("input", "user", "Ship the release"),
    text("reply", "assistant", "Released"),
    lifecycle("model_request_completed", "model-request-1"),
  );
  expect(kinds(entries)).toEqual(["hidden", "model"]);
  expect(entries[0]).toMatchObject({ text: reminder });
  expect(kinds(models(entries)[0]!.children)).toEqual(["reply"]);
});

it("presents an inline delegation on its call and keeps a dispatch a dispatch", () => {
  const inline = tool("delegate", "inline");
  const dispatch = tool("delegate", "async");
  const { entries, totals } = timeline(
    lifecycle("model_request_started"),
    inline,
    custom("a13n.harness.delegation", {
      type: "inline_delegation",
      invocation_id: "delegation-1",
      action: "started",
      subagent: "Researcher",
      status: "running",
      parent_tool_call_id: "inline",
    }),
    dispatch,
    finish(dispatch, {
      result: JSON.stringify({
        execution_id: "execution-9",
        subagent_name: "reviewer",
        status: "running",
      }),
    }),
  );
  const [request] = models(entries);
  expect(request!.children).toMatchObject([
    { kind: "subagent", name: "Researcher", dispatchOnly: false },
    { kind: "subagent", dispatchOnly: true, childExecutionId: "execution-9" },
  ]);
  expect(totals).toMatchObject({ modelCalls: 1, toolCalls: 2 });
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
    lifecycle("model_request_failed", "model-request-1", {
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
          request_usage: {
            input_tokens: 80,
            output_tokens: 10,
            cache_read_tokens: 64,
          },
        },
      ],
    }),
  );
  expect(totals).toMatchObject({
    inputTokens: 200,
    outputTokens: 40,
    // Cached input is the part of input the provider served from its cache.
    cacheReadTokens: 64,
    costUsd: "0.01",
    costComplete: false,
    durationMs: 9000,
  });
});

it("counts model requests from usage when no lifecycle steps were retained", () => {
  const { totals } = timeline(
    custom("a13n.harness.usage", {
      type: "usage_report",
      reason: "model_request",
      records: [0, 1].map((ordinal) => ({
        kind: "model",
        record_id: `record-${ordinal}`,
        response_ordinal: ordinal,
        model_name: "claude-sonnet",
        request_usage: { input_tokens: 100, output_tokens: 5 },
      })),
    }),
  );
  expect(totals).toMatchObject({ modelCalls: 2, inputTokens: 200 });
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
  const { entries, totals } = timelineOf(
    [fixtureAttempt(1), fixtureAttempt(2)],
    lifecycle("model_request_started"),
    lifecycle("model_request_failed", "model-request-1", {
      error_code: "provider_error",
    }),
    custom("a13n.harness.recovery", {
      type: "model_retry_scheduled",
      attempt: 2,
      max_attempts: 3,
      delay_seconds: 2,
    }),
    lifecycle("model_request_started", "model-request-1", {}, { attempt: 2 }),
    observation("plugin.progress", "working", { attempt: 2 }),
  );
  // The first attempt and the Run's own outcome never become rows: the
  // heading and `runOutcome` already say them.
  expect(kinds(entries)).toEqual(["model", "event", "event", "model", "other"]);
  const notices = entries.flatMap((entry) =>
    entry.kind === "event" ? [entry.notice] : [],
  );
  expect(notices).toEqual([
    {
      kind: "retry",
      tone: "warning",
      attempt: 2,
      maxAttempts: 3,
      delaySeconds: 2,
    },
    { kind: "attempt", tone: "neutral", attempt: 2, reason: "recovery" },
  ]);
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
        firstPosition: "1-0",
        lastPosition: "1-1",
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
  const execution = runExecution(
    run,
    display(lifecycle("model_request_started")),
  );
  const partial = runTimeline({
    run,
    items: [],
    execution,
    coverage: "partial",
  });
  expect(partial.coverage).toBe("partial");
  expect(partial.totals.costComplete).toBe(false);
  expect(
    runTimeline({ run, items: [], execution, coverage: "unavailable" })
      .coverage,
  ).toBe("unavailable");
});

it("nests inline children by their delegation parent, including nested reused IDs", () => {
  const child = (entry: ReturnType<typeof lifecycle>, id: string) => ({
    ...entry,
    content: { ...entry.content, subagentRunId: id },
  });
  const delegation = (parent: string, id: string) =>
    custom("a13n.harness.delegation", {
      type: "inline_delegation",
      invocation_id: `inv-${id}`,
      parent_run_id: parent,
      child_run_id: id,
      parent_tool_call_id: "same",
      subagent: id,
      status: "completed",
    });
  const { entries } = timeline(
    lifecycle("model_request_started"),
    tool("delegate", "root-call", { toolCallId: "same" }),
    delegation("root", "child-a"),
    child(lifecycle("model_request_started"), "child-a"),
    tool("delegate", "child-call", {
      toolCallId: "same",
      subagentRunId: "child-a",
    }),
    delegation("child-a", "child-b"),
    child(lifecycle("model_request_started"), "child-b"),
    message("child-reply", "assistant", {
      text: "Child output",
      subagentRunId: "child-b",
    }),
    message("root-reply", "assistant", { text: "Root output" }),
  );
  const root = models(entries)[0]!;
  expect(root.children.map((entry) => entry.id)).toEqual([
    "root-call",
    "root-reply",
  ]);
  const outer = root.children[0]!;
  expect(outer).toMatchObject({
    kind: "subagent",
    children: [
      {
        subagentRunId: "child-a",
        children: [
          {
            id: "child-call",
            kind: "subagent",
            children: [
              {
                subagentRunId: "child-b",
                children: [{ id: "child-reply", subagentRunId: "child-b" }],
              },
            ],
          },
        ],
      },
    ],
  });
});

it("does not discard orphaned inline content when no parent was retained", () => {
  const { entries } = timeline(
    message("child-reply", "assistant", {
      text: "Partial child output",
      subagentRunId: "missing-child",
    }),
  );
  expect(entries).toEqual([
    expect.objectContaining({
      id: "child-reply",
      subagentRunId: "missing-child",
      kind: "reply",
    }),
  ]);
});

it("retains the source entry of steering even when historical paging omits the initial input", () => {
  const { entries } = timeline(
    message("steer", "user", {
      text: "Use revised numbers",
      input_group: "01a12147-bd16-73f2-b290-80198647ebc8",
      metadata: { source_id: "inb_abcdef1234567890abcdef123456" },
      input_source: "steering",
    }),
  );
  expect(entries).toMatchObject([
    {
      kind: "guidance",
      sourceId: "inb_abcdef1234567890abcdef123456",
      text: "Use revised numbers",
    },
  ]);
});

it("shows context recovery warnings in a completed run and retains their raw diagnostics", () => {
  const { entries } = timeline(
    custom("a13n.harness.recovery", {
      type: "context_recovery",
      outcome: "resumed",
      warning: "Immediate root-cause investigation is required.",
      before_chars: 100000,
    }),
    text("answer", "assistant", "Completed"),
  );
  expect(entries.find((entry) => entry.kind === "event")).toMatchObject({
    notice: {
      kind: "context-recovery",
      tone: "warning",
      outcome: "resumed",
      message: "Immediate root-cause investigation is required.",
    },
  });
  expect(entries.find((entry) => entry.kind === "other")).toMatchObject({
    observations: [{ detail: { payload: { before_chars: 100000 } } }],
  });
});
