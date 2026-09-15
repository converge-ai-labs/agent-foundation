import { expect, it, vi } from "vitest";
import type { Schema, Transport } from "../transport/client";
import {
  FocusDisplay,
  focusFrame,
  showFocusedOutput,
  watchThread,
} from "./stream";

const thread = {
  thread_id: "thread-one",
  created_at: "2026-09-12",
  updated_at: "2026-09-12",
  metadata_version: 1,
  archived: false,
  configuration: {
    version: 1,
    agent_source: { kind: "agent", id: "agent-one" },
    environment_profile_id: "local",
  },
  continuation_state: "initial",
  root_activity: { state: "inactive" },
} as Schema<"ThreadSummary">;
function snapshot(count?: number) {
  return focusFrame({
    kind: "snapshot",
    resume_cursor: count === undefined ? "cursor-one" : null,
    snapshot: {
      epoch: "epoch-one",
      cutover_sequence: 100,
      thread: { thread, continuation_id: null },
      children: { executions: [], total: 0 },
      recent_events: [
        {
          event_type: "TEXT_MESSAGE_CONTENT",
          payload: { delta: "diagnostic only" },
        },
      ],
      root_stream:
        count === undefined
          ? null
          : {
              thread_id: "thread-one",
              run_id: "run-one",
              base_continuation_id: null,
              event_count: count,
            },
    },
  });
}
function event(sequence: number, delta = "live", run_kind = "root") {
  return focusFrame({
    kind: "event",
    resume_cursor: `cursor-${sequence}`,
    event: {
      sequence,
      epoch: "epoch-one",
      run_kind,
      thread_id: "thread-one",
      root_thread_id: "thread-one",
      run_id: "run-one",
      event_type: "TEXT_MESSAGE_CONTENT",
      payload: { message_id: "message-one", delta },
      payload_omitted: false,
    },
  });
}
it("does not turn diagnostics into conversation output and only saves cursor after ready", () => {
  const display = new FocusDisplay();
  display.accept(snapshot(2));
  expect(display.cursor).toBeUndefined();
  expect(display.blocks.size).toBe(0);
  display.accept(
    focusFrame({
      kind: "root_stream",
      run_id: "run-one",
      events: [
        {
          index: 0,
          event_type: "TEXT_MESSAGE_START",
          payload: { message_id: "message-one" },
          payload_omitted: false,
        },
        {
          index: 1,
          event_type: "TEXT_MESSAGE_CONTENT",
          payload: { message_id: "message-one", delta: "replay" },
          payload_omitted: false,
        },
      ],
    }),
  );
  expect(display.cursor).toBeUndefined();
  display.accept(focusFrame({ kind: "ready", resume_cursor: "cursor-ready" }));
  display.accept(event(105));
  display.accept(event(105, "duplicate"));
  expect([...display.blocks.values()][0].text).toBe("replaylive");
  expect(display.cursor).toBe("cursor-105");
});
it("rejects incomplete replay rather than publishing its cutover cursor", () => {
  const display = new FocusDisplay();
  display.accept(snapshot(2));
  expect(() =>
    display.accept(focusFrame({ kind: "ready", resume_cursor: "bad" })),
  ).toThrow("Incomplete");
  expect(display.cursor).toBeUndefined();
});
it("reset discards provisional output without making a new saved transcript", () => {
  const display = new FocusDisplay();
  display.accept(snapshot(0));
  display.accept(focusFrame({ kind: "ready", resume_cursor: "ready" }));
  display.accept(event(110));
  display.accept(focusFrame({ kind: "reset", reason: "live_cursor_expired" }));
  expect(display.blocks.size).toBe(0);
  expect(display.ready).toBe(false);
  expect(display.cursor).toBeUndefined();
});
it("a child event advances the shared cursor but does not become root text", () => {
  const display = new FocusDisplay();
  display.accept(snapshot());
  display.accept(event(110, "child", "child"));
  expect(display.cursor).toBe("cursor-110");
  expect(display.blocks.size).toBe(0);
});
it("rejects incompatible frame envelopes at the boundary", () => {
  for (const value of [
    null,
    { kind: "snapshot" },
    { kind: "event", event: {} },
    { kind: "ready", resume_cursor: 1 },
  ])
    expect(() => focusFrame(value)).toThrow("Invalid");
});

it("honors display metadata, canonical reasoning events and tool result identity", () => {
  const display = new FocusDisplay();
  display.accept(snapshot(8));
  const events = [
    [
      "TEXT_MESSAGE_START",
      { message_id: "hidden", role: "user", metadata: { display: false } },
    ],
    [
      "TEXT_MESSAGE_CONTENT",
      {
        message_id: "hidden",
        delta: "hidden guidance",
        metadata: { display: false },
      },
    ],
    ["REASONING_MESSAGE_START", { message_id: "reason", role: "reasoning" }],
    [
      "REASONING_MESSAGE_CONTENT",
      { message_id: "reason", delta: "reasoning text" },
    ],
    ["REASONING_MESSAGE_END", { message_id: "reason" }],
    ["TOOL_CALL_START", { tool_call_id: "call-one", tool_call_name: "shell" }],
    ["TOOL_CALL_ARGS", { tool_call_id: "call-one", delta: "{}" }],
    [
      "TOOL_CALL_RESULT",
      {
        message_id: "call-one:result",
        tool_call_id: "call-one",
        content: "done",
      },
    ],
  ];
  display.accept(
    focusFrame({
      kind: "root_stream",
      run_id: "run-one",
      events: events.map(([event_type, payload], index) => ({
        event_type,
        payload,
        index,
        payload_omitted: false,
      })),
    }),
  );
  display.accept(focusFrame({ kind: "ready", resume_cursor: "ready" }));
  expect([...display.blocks.values()]).toEqual([
    {
      id: "run-one:reason",
      kind: "thinking",
      text: "reasoning text",
      done: true,
    },
    {
      id: "run-one:call-one",
      kind: "tool",
      name: "shell",
      text: "{}",
      result: "done",
      done: true,
    },
  ]);
});

it("reboots on the next root Run and retains its authoritative unsaved base", async () => {
  vi.useFakeTimers();
  const display = new FocusDisplay();
  const first = snapshot(0);
  if (first.kind !== "snapshot") throw new Error("fixture");
  first.snapshot.thread.continuation_id = "C0";
  first.snapshot.root_stream!.base_continuation_id = "C0";
  const next = event(120);
  if (next.kind !== "event") throw new Error("fixture");
  next.event.run_id = "run-two";
  const second = snapshot(1);
  if (second.kind !== "snapshot") throw new Error("fixture");
  second.snapshot.thread.continuation_id = "C1";
  second.snapshot.root_stream!.base_continuation_id = "C1";
  second.snapshot.root_stream!.run_id = "run-two";
  const response = (frames: unknown[]) =>
    new Response(
      frames.map((frame) => `data: ${JSON.stringify(frame)}\n\n`).join(""),
    );
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      response([first, { kind: "ready", resume_cursor: "C0-ready" }, next]),
    )
    .mockResolvedValueOnce(
      response([
        second,
        {
          kind: "root_stream",
          run_id: "run-two",
          events: [
            {
              index: 0,
              event_type: "TEXT_MESSAGE_CONTENT",
              payload: {
                message_id: "unsaved",
                delta: "Retained failed output",
              },
              payload_omitted: false,
            },
          ],
        },
        { kind: "ready", resume_cursor: "C1-ready" },
      ]),
    );
  const close = watchThread(
    { fetch } as unknown as Transport,
    "thread-one",
    display,
    vi.fn(),
    vi.fn(),
    vi.fn(),
  );
  try {
    await vi.advanceTimersByTimeAsync(1);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(fetch.mock.calls.map((call) => call[0])).toEqual([
      "/api/threads/thread-one/events",
      "/api/threads/thread-one/events",
    ]);
    expect(display.runId).toBe("run-two");
    expect(display.baseContinuation).toBe("C1");
    expect(display.snapshot!.thread.thread.root_activity.state).toBe(
      "inactive",
    );
    expect([...display.blocks.values()][0].text).toBe("Retained failed output");
  } finally {
    close();
    vi.useRealTimers();
  }
});

it("dispatches native custom payloads and folds task/context operations without diagnostic spam", () => {
  const display = new FocusDisplay();
  const custom = (name: string, event: unknown, extra = {}) => ({
    event_type: "CUSTOM",
    payload: { name, value: { event }, ...extra },
    payload_omitted: false,
  });
  const events = [
    custom(
      "a13n.input.media",
      { content: { kind: "binary", size_bytes: 20 } },
      { message_id: "run-one:input:1:0", metadata: { media: true } },
    ),
    custom("a13n.harness.state", {
      payload: {
        type: "task_changed",
        task: { id: "task-one", status: "pending", subject: "Inspect code" },
      },
    }),
    custom("a13n.harness.state", {
      payload: {
        type: "task_changed",
        task: {
          id: "task-one",
          status: "in_progress",
          subject: "Inspect code",
          active_form: "Inspecting code",
        },
      },
    }),
    custom("a13n.harness.context", {
      payload: { type: "compaction_started", operation_id: "compact-one" },
    }),
    custom("a13n.context.compaction_summary", {
      operation_id: "compact-one",
      summary: "Keep this context",
    }),
    custom("a13n.harness.context", {
      payload: { type: "compaction_completed", operation_id: "compact-one" },
    }),
    custom("a13n.harness.lifecycle", { payload: { type: "request_started" } }),
    {
      event_type: "RUN_ERROR",
      payload: { code: "failed", message: "Provider disconnected" },
      payload_omitted: false,
    },
  ];
  display.accept(snapshot(events.length));
  display.accept(
    focusFrame({
      kind: "root_stream",
      run_id: "run-one",
      events: events.map((event, index) => ({ ...event, index })),
    }),
  );
  const blocks = [...display.blocks.values()];
  expect(blocks.filter((block) => block.kind === "media")[0].value).toEqual({
    kind: "binary",
    size_bytes: 20,
  });
  expect(blocks.filter((block) => block.kind === "task")).toHaveLength(1);
  expect(blocks.find((block) => block.kind === "task")!.text).toBe(
    "Inspecting code",
  );
  expect(
    blocks.find((block) => block.id.endsWith("context:compact-one")),
  ).toMatchObject({
    name: "compaction completed",
    result: "Keep this context",
  });
  expect(blocks.filter((block) => block.diagnostic)).toHaveLength(1);
  expect(blocks.find((block) => block.id.endsWith(":execution"))).toMatchObject(
    { name: "Execution failed", text: "Provider disconnected" },
  );
});
it("reassembles custom fragments before applying hidden metadata and rejects missing fragments", () => {
  const display = new FocusDisplay();
  const hidden = JSON.stringify({
    name: "a13n.input.media",
    metadata: { display: false },
    value: { event: { content: { kind: "binary" } } },
  });
  const visible = JSON.stringify({
    name: "a13n.context.compaction_summary",
    value: { event: { operation_id: "one", summary: "Exact summary" } },
  });
  const fragment = (
    id: string,
    data: string,
    index: number,
    count: number,
  ) => ({
    event_type: "CUSTOM",
    payload: {
      name: "a13n.stream.fragment",
      value: { id, data, index, count },
    },
    payload_omitted: false,
  });
  const events = [
    fragment("hidden", hidden.slice(0, 20), 0, 2),
    fragment("hidden", hidden.slice(20), 1, 2),
    fragment("visible", visible, 0, 1),
    fragment("gap", "{}", 1, 2),
  ];
  display.accept(snapshot(events.length));
  display.accept(
    focusFrame({
      kind: "root_stream",
      run_id: "run-one",
      events: events.map((event, index) => ({ ...event, index })),
    }),
  );
  expect(display.blocks.size).toBe(1);
  expect([...display.blocks.values()][0].result).toBe("Exact summary");
  expect(display.gap).toBe(true);
});

it("retains unsaved initial output and only cuts over after replacement history is rendered", () => {
  const display = new FocusDisplay();
  display.runId = "run-one";
  display.baseContinuation = null;
  expect(showFocusedOutput(display, "initial:immutable-state", null)).toBe(
    true,
  );
  expect(showFocusedOutput(display, undefined, null)).toBe(true);
  expect(showFocusedOutput(display, "C1", null)).toBe(false);
  display.runId = "run-two";
  display.baseContinuation = "C1";
  expect(
    showFocusedOutput(display, "initial:immutable-state", null, true),
  ).toBe(true);
  expect(showFocusedOutput(display, "C1", null)).toBe(true);
  expect(showFocusedOutput(display, "C2", null)).toBe(false);
});

it("folds native applied edits and failed results by exact ID without guessing proxy identities", () => {
  const display = new FocusDisplay();
  const events = [
    ["TOOL_CALL_START", { tool_call_id: "outer", tool_call_name: "call" }],
    [
      "TOOL_CALL_ARGS",
      { tool_call_id: "outer", delta: '{"group":"filesystem","tool":"edit"}' },
    ],
    ["TOOL_CALL_END", { tool_call_id: "outer" }],
    [
      "CUSTOM",
      {
        name: "a13n.filesystem.edit_applied",
        value: {
          event: {
            tool_call_id: "inner",
            file_path: "/native/a",
            before: "old",
            after: "new",
          },
        },
      },
    ],
    [
      "CUSTOM",
      {
        name: "a13n.pydantic_ai.function_tool_result",
        value: {
          event: {
            part: {
              part_kind: "tool-return",
              tool_name: "edit",
              tool_call_id: "inner",
              outcome: "failed",
              content: "Failed after write",
            },
          },
        },
      },
    ],
    ["TOOL_CALL_START", { tool_call_id: "retry", tool_call_name: "view" }],
    [
      "CUSTOM",
      {
        name: "a13n.pydantic_ai.function_tool_result",
        value: {
          event: {
            part: {
              part_kind: "retry-prompt",
              tool_call_id: "retry",
              content: "Invalid input",
            },
          },
        },
      },
    ],
    ["RUN_ERROR", { code: "run_cancelled" }],
  ];
  display.accept(snapshot(events.length));
  display.accept(
    focusFrame({
      kind: "root_stream",
      run_id: "run-one",
      events: events.map(([event_type, payload], index) => ({
        index,
        event_type,
        payload,
        payload_omitted: false,
      })),
    }),
  );
  expect(display.blocks.get("run-one:outer")).toMatchObject({
    done: true,
    stopped: true,
  });
  expect(display.blocks.get("run-one:outer")?.result).toBeUndefined();
  expect(display.blocks.get("run-one:outer")?.edit).toBeUndefined();
  expect(display.blocks.get("run-one:inner")).toMatchObject({
    outcome: "failed",
    result: "Failed after write",
    edit: { before: "old", after: "new" },
    stopped: true,
  });
  expect(display.blocks.get("run-one:retry")?.failure).toBe("Invalid input");
  expect(display.blocks.get("run-one:retry")?.retry).toBe(true);
  expect(
    [...display.blocks.values()].filter((block) => block.diagnostic),
  ).toHaveLength(0);
});

it("folds provider-native search snapshots once, retaining final arguments and separate local call identity", () => {
  const display = new FocusDisplay();
  display.accept(snapshot(0));
  display.accept(focusFrame({ kind: "ready", resume_cursor: "cursor-ready" }));
  let sequence = 101;
  const emit = (event_type: string, payload: Record<string, unknown>) =>
    display.accept(
      focusFrame({
        kind: "event",
        resume_cursor: `cursor-${sequence}`,
        event: {
          sequence: sequence++,
          epoch: "epoch-one",
          run_kind: "root",
          thread_id: "thread-one",
          root_thread_id: "thread-one",
          run_id: "run-one",
          event_type,
          payload,
        },
      }),
    );
  const part = {
    part_kind: "builtin-tool-call",
    tool_name: "web_search",
    tool_call_id: "same",
    provider_name: "openai",
    args: null,
  };
  const native = (name: string, value: Record<string, unknown>) =>
    emit("CUSTOM", {
      name: `a13n.pydantic_ai.${name}`,
      value: { event: { index: 0, part: value } },
    });
  native("part_start", part);
  expect([...display.blocks.values()][0].result).toBeUndefined();
  native("part_end", {
    ...part,
    args: { type: "search", query: "final query" },
  });
  expect([...display.blocks.values()][0].text).toContain("final query");
  expect([...display.blocks.values()][0].result).toBeUndefined();
  const returned = {
    ...part,
    part_kind: "builtin-tool-return",
    content: { status: "completed", sources: [] },
    outcome: "success",
  };
  native("part_start", returned);
  native("part_end", returned);
  emit("TOOL_CALL_START", {
    tool_call_id: "same",
    tool_call_name: "web_search",
  });
  expect(display.blocks.size).toBe(2);
  const provider = [...display.blocks.values()].find((block) => block.provider);
  expect(provider?.outcome).toBe("success");
  expect(provider?.text).toContain("final query");
  expect(provider?.result).toContain("completed");
  expect([...display.blocks.values()].every((block) => !block.diagnostic)).toBe(
    true,
  );
});

function childEvent(
  sequence: number,
  execution = "execution-one",
  payload = { message_id: "text", delta: "child text" },
) {
  return focusFrame({
    kind: "event",
    resume_cursor: `cursor-${sequence}`,
    event: {
      sequence,
      epoch: "epoch-one",
      run_kind: "child",
      root_thread_id: "thread-one",
      parent_thread_id: "thread-one",
      thread_id: `thread-${execution}`,
      run_id: `run-${execution}`,
      execution_id: execution,
      event_type: "TEXT_MESSAGE_CONTENT",
      payload,
      payload_omitted: false,
    },
  });
}
it("isolates interleaved child output by execution, run and parent, and clears it on reset", () => {
  const display = new FocusDisplay();
  display.accept(snapshot(0));
  display.accept(focusFrame({ kind: "ready", resume_cursor: "ready" }));
  display.accept(childEvent(101));
  display.accept(childEvent(102, "execution-two"));
  display.accept(childEvent(102, "execution-two"));
  const bad = childEvent(103);
  if (bad.kind !== "event") throw new Error("Expected event");
  bad.event.parent_thread_id = "wrong-parent";
  display.accept(bad);
  const nextRun = childEvent(104);
  if (nextRun.kind !== "event") throw new Error("Expected event");
  nextRun.event.run_id = "different-run";
  display.accept(nextRun);
  const unrelated = childEvent(105, "unrelated");
  if (unrelated.kind !== "event") throw new Error("Expected event");
  unrelated.event.root_thread_id = "another-root";
  display.accept(unrelated);
  display.accept(event(106, "root text"));
  expect([...display.blocks.values()].map((block) => block.text)).toEqual([
    "root text",
  ]);
  expect(display.children.size).toBe(2);
  for (const child of display.children.values())
    expect(
      [...child.display.blocks.values()].map((block) => block.text),
    ).toEqual(["child text"]);
  const child = {
    execution_id: "execution-one",
    parent_thread_id: "thread-one",
    child_thread_id: "thread-execution-one",
    child_run_id: "run-execution-one",
  } as Schema<"ChildExecutionView">;
  expect(display.childOutput(child)).toBeDefined();
  // The saved head can still identify the preceding deferred checkpoint.
  expect(display.childOutput(child)?.runId).toBe("different-run");
  expect(display.childOutput(child)?.gap).toBe(true);
  expect(
    display.childOutput({ ...child, parent_thread_id: "wrong-parent" }),
  ).toBeUndefined();
  display.accept(focusFrame({ kind: "reset", reason: "epoch_changed" }));
  expect(display.children.size).toBe(0);
  expect(display.tasks).toBeUndefined();
});
it("bounds observed child text and events without pretending it is complete saved history", () => {
  const display = new FocusDisplay();
  display.accept(snapshot());
  display.accept(
    childEvent(101, "execution-one", {
      message_id: "text",
      delta: "x".repeat(200_000),
    }),
  );
  const child = display.children.get("execution-one")!.display;
  expect(child.gap).toBe(true);
  expect([...child.blocks.values()][0].text.length).toBeLessThanOrEqual(
    128 * 1024,
  );
  for (let i = 0; i < 200; i++)
    display.accept(
      childEvent(102 + i, "execution-one", {
        message_id: `text-${i}`,
        delta: "next",
      }),
    );
  expect(child.blocks.size).toBeLessThanOrEqual(128);
  expect([...child.blocks.values()].at(-1)?.id).toContain("text-199");
});
it("merges same-version task batches, rejects stale projections and never mixes child tasks into root", () => {
  const display = new FocusDisplay();
  const prefix = snapshot(0);
  if (prefix.kind !== "snapshot") throw new Error("Expected snapshot");
  prefix.snapshot.tasks = {
    version: 3,
    available: true,
    tasks: [
      {
        task_id: "task-one",
        version: 2,
        subject: "Current",
        status: "in_progress",
      },
    ],
  };
  display.accept(prefix);
  display.accept(focusFrame({ kind: "ready", resume_cursor: "ready" }));
  let sequence = 101;
  function emit(
    id: string,
    state: number,
    version: number,
    subject: string,
    child = false,
  ) {
    const frame = child ? childEvent(sequence++) : event(sequence++);
    if (frame.kind !== "event") throw new Error("Expected event");
    frame.event.event_type = "CUSTOM";
    frame.event.payload = {
      name: "a13n.harness.state",
      value: {
        event: {
          payload: {
            type: "task_changed",
            task_state_version: state,
            task: {
              id,
              version,
              subject,
              status: "completed",
              blocks: ["task-next"],
            },
          },
        },
      },
    };
    display.accept(frame);
  }
  emit("task-one", 2, 9, "Stale state");
  emit("task-one", 3, 1, "Stale task");
  expect(display.tasks?.tasks?.[0].subject).toBe("Current");
  emit("task-one", 4, 3, "Updated");
  emit("task-two", 4, 1, "Reciprocal update");
  emit("child-task", 5, 1, "Child task", true);
  expect(display.tasks?.version).toBe(4);
  expect(display.tasks?.tasks?.map((task) => task.subject)).toEqual([
    "Updated",
    "Reciprocal update",
  ]);
  expect(display.tasks?.tasks?.[0].blocks).toEqual(["task-next"]);
  display.accept(snapshot());
  expect(display.tasks).toBeUndefined();
});
