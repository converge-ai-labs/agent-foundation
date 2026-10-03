import { afterEach, expect, it, vi } from "vitest";
import type { ItemChange } from "a13n-ui/display";
import { createTransport, type Schema } from "../transport/client";
import { mockWebSocket, FakeWebSocket } from "../../tests/fake-websocket";
import {
  FocusDisplay,
  focusFrame,
  focusRefresh,
  showFocusedOutput,
  watchThread,
} from "./stream";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
function connectedTransport() {
  vi.stubGlobal("window", { location: { origin: "http://localhost" } });
  const socket = mockWebSocket();
  return { socket, transport: createTransport("", vi.fn()) };
}
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
function snapshot(count?: number, checkpoints: Record<string, number> = {}) {
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
              checkpoints,
            },
    },
  });
}
function item(
  id: string,
  position: number,
  content: Record<string, unknown>,
  kind: Schema<"Item">["kind"] = "text_message",
  state: Schema<"Item">["state"] = "completed",
): Schema<"Item"> {
  return {
    id,
    ordinal: position,
    kind,
    state,
    first_stream_id: `1-${position}`,
    last_stream_id: `1-${position}`,
    started_at: "2026-01-01T00:00:00Z",
    ended_at: null,
    content,
  };
}
function text(id: string, position: number, value: string, extra = {}) {
  return item(id, position, {
    messageId: id,
    role: "assistant",
    text: value,
    ...extra,
  });
}
function observation(
  id: string,
  position: number,
  name: string,
  source: Record<string, unknown>,
  extra = {},
) {
  return item(
    id,
    position,
    { name, value: { event: source }, ...extra },
    "observation",
  );
}
function set(value: Schema<"Item">): ItemChange<Schema<"Item">> {
  return { type: "set", item: value };
}
function live(
  sequence: number,
  changes: ItemChange<Schema<"Item">>[],
  overrides = {},
) {
  return focusFrame({
    kind: "event",
    resume_cursor: `cursor-${sequence}`,
    event: {
      sequence,
      epoch: "epoch-one",
      run_kind: "root",
      root_thread_id: "thread-one",
      thread_id: "thread-one",
      run_id: "run-one",
      event_type: "CUSTOM",
      payload: null,
      payload_omitted: false,
      changes,
      ...overrides,
    },
  });
}
function replay(items: Schema<"Item">[]) {
  return focusFrame({
    kind: "root_stream",
    run_id: "run-one",
    events: items.map((value, index) => ({ index, changes: [set(value)] })),
  });
}
function ready() {
  return focusFrame({ kind: "ready", resume_cursor: "ready" });
}
function boot(
  items: Schema<"Item">[] = [],
  checkpoints: Record<string, number> = {},
) {
  const display = new FocusDisplay();
  display.accept(snapshot(items.length, checkpoints));
  display.accept(replay(items));
  display.accept(ready());
  return display;
}
const child = {
  run_kind: "child",
  execution_id: "exec-one",
  parent_thread_id: "thread-one",
  thread_id: "child-one",
  run_id: "child-run",
};

it("publishes a compact baseline only after complete replay and never renders diagnostics", () => {
  const display = new FocusDisplay();
  display.accept(snapshot(1));
  expect(display.cursor).toBeUndefined();
  expect(display.blocks.size).toBe(0);
  display.accept(replay([text("m", 1, "before")]));
  expect(display.cursor).toBeUndefined();
  display.accept(ready());
  const frame = live(101, [
    {
      type: "append",
      id: "m",
      field: "text",
      text: " after",
      last_stream_id: "1-2",
      state: "completed",
      ended_at: null,
    },
  ]);
  display.accept(frame);
  display.accept(frame);
  expect([...display.blocks.values()].map((block) => block.text)).toEqual([
    "before after",
  ]);
  expect(display.cursor).toBe("cursor-101");
});
it("rejects incomplete replay without selecting its cutover cursor", () => {
  const display = new FocusDisplay();
  display.accept(snapshot(2));
  display.accept(replay([text("m", 1, "one")]));
  expect(() => display.accept(ready())).toThrow("Incomplete");
  expect(display.cursor).toBeUndefined();
});
it("rejects missing compact baseline atomically without advancing the cursor", () => {
  const display = boot();
  expect(() =>
    display.accept(
      live(101, [
        set(text("one", 1, "not yet")),
        {
          type: "append",
          id: "missing",
          field: "text",
          text: "suffix",
          last_stream_id: "1-2",
          state: "in_progress",
          ended_at: null,
        },
      ]),
    ),
  ).toThrow("Missing compact display baseline");
  expect(display.blocks.size).toBe(0);
  expect(display.cursor).toBe("ready");
});
it("does not fall back to raw AG-UI events after the compact protocol cutover", () => {
  const display = boot();
  expect(() =>
    display.accept(
      live(101, [], {
        changes: null,
        event_type: "TEXT_MESSAGE_CONTENT",
        payload: { messageId: "raw", delta: "wrong" },
      }),
    ),
  ).toThrow("Missing compact");
  expect(display.blocks.size).toBe(0);
  expect(display.cursor).toBe("ready");
});
it("reset discards provisional output without manufacturing saved history", () => {
  const display = boot([text("m", 1, "answer")]);
  display.accept(focusFrame({ kind: "reset", reason: "live_cursor_expired" }));
  expect(display.blocks.size).toBe(0);
  expect(display.ready).toBe(false);
  expect(display.cursor).toBeUndefined();
});
it("validates envelopes and rejects run or epoch changes before cursor advancement", () => {
  for (const value of [
    null,
    { kind: "snapshot" },
    { kind: "event", event: {} },
    { kind: "ready", resume_cursor: 1 },
  ])
    expect(() => focusFrame(value)).toThrow("Invalid");
  const display = boot();
  expect(() => display.accept(live(101, [], { epoch: "other" }))).toThrow(
    "epoch",
  );
  expect(() => display.accept(live(101, [], { run_id: "new" }))).toThrow(
    "Root Run changed",
  );
  expect(display.sequence).toBe(100);
});
it("uses Host input visibility and preserves reasoning and media without replaying native parts", () => {
  const display = boot([
    text("hidden", 1, "hidden", { metadata: { display: false }, role: "user" }),
    text("user", 2, "authored", {
      role: "user",
      metadata: { source_id: "input" },
    }),
    item(
      "reason",
      3,
      { messageId: "reason", text: "plan" },
      "reasoning_message",
    ),
    item("media", 4, {
      messageId: "media",
      role: "user",
      input_media: { url: "image" },
    }),
    observation("native", 5, "a13n.pydantic_ai.part_start", {
      part: { part_kind: "text", content: "duplicate" },
    }),
    observation("system", 6, "a13n.input.recovery", { content: "internal" }),
  ]);
  expect([...display.blocks.values()].map((block) => block.kind)).toEqual([
    "user",
    "thinking",
    "media",
  ]);
  expect(display.blocks.get("run-one:user")?.metadata).toEqual({
    source_id: "input",
  });
});
it.each(["success", "failed", "denied", "interrupted"] as const)(
  "renders canonical tool outcome %s and complete evidence with exact scope",
  (outcome) => {
    const before = "x".repeat(300_000);
    const display = boot([
      item(
        "root",
        1,
        { toolCallId: "call", toolCallName: "root", arguments: "{}" },
        "tool_call",
        "in_progress",
      ),
      item(
        "inline",
        2,
        {
          toolCallId: "call",
          toolCallName: "edit",
          arguments: "{}",
          subagentRunId: "inline",
          value: { ok: true },
          outcome,
          applied_edit: { file_path: "file.py", before, after: before + "!" },
          result_parts: [
            {
              type: "image",
              source: { type: "url", value: "https://example.com/a.png" },
            },
          ],
        },
        "tool_call",
        outcome === "success" ? "completed" : "failed",
      ),
    ]);
    expect(display.blocks.get("run-one:call")?.edit).toBeUndefined();
    expect(display.blocks.get("run-one:inline:call")).toMatchObject({
      outcome,
      edit: { before, after: before + "!" },
      resultParts: [{ type: "image" }],
    });
    expect(display.blocks.get("run-one:inline:call")?.result).toContain("ok");
  },
);
it("keeps native provider identities separate and renders retry, images and Apps from the item", () => {
  const image = {
    thread_id: "thread-one",
    attachment: {
      attachment_id: "screen",
      name: "desktop.png",
      media_type: "image/png",
      size: 128,
    },
  };
  const app = {
    app_id: "app",
    thread_id: "thread-one",
    run_id: "run-one",
    tool_call_id: "call",
    server_id: "mcp",
    tool_name: "counter",
  };
  const display = boot([
    item(
      "native",
      1,
      {
        toolCallId: "call",
        toolCallName: "search",
        provider: "provider",
        arguments: "query",
        value: "found",
      },
      "tool_call",
    ),
    item(
      "local",
      2,
      {
        toolCallId: "call",
        toolCallName: "view",
        arguments: "{}",
        value: "Retry this",
        retry: true,
        tool_images: [image],
        mcp_apps: [app],
        tool_image_unavailable: true,
      },
      "tool_call",
      "failed",
    ),
  ]);
  expect(display.blocks.size).toBe(2);
  expect(display.blocks.get("run-one:native:provider:call")).toMatchObject({
    provider: "provider",
    result: "found",
  });
  expect(display.blocks.get("run-one:call")).toMatchObject({
    retry: true,
    failure: "Retry this",
    images: [image],
    apps: [app],
    imageUnavailable: true,
  });
});
it("applies a child set followed by an append in the same batch", () => {
  const display = boot();
  display.accept(
    live(
      101,
      [
        set(text("m", 1, "first")),
        {
          type: "append",
          id: "m",
          field: "text",
          text: " second",
          last_stream_id: "1-2",
          state: "completed",
          ended_at: null,
        },
      ],
      child,
    ),
  );
  const output = display.children.get("exec-one")!.display;
  expect(output.blocks.get("child-run:m")?.text).toBe("first second");
  expect(output.gap).toBe(false);
});
it("isolates child output by execution, run and parent and does not label it root output", () => {
  const display = boot();
  display.accept(live(101, [set(text("m", 1, "child"))], child));
  expect(display.blocks.size).toBe(0);
  expect(display.cursor).toBe("cursor-101");
  expect(
    display.children.get("exec-one")?.display.blocks.get("child-run:m")?.text,
  ).toBe("child");
  display.accept(
    live(102, [set(text("wrong", 2, "wrong parent"))], {
      ...child,
      parent_thread_id: "other",
    }),
  );
  expect(display.children.get("exec-one")?.display.blocks.size).toBe(1);
  display.accept(
    live(103, [set(text("m", 1, "new segment"))], {
      ...child,
      run_id: "child-next",
    }),
  );
  expect(
    [...display.children.get("exec-one")!.display.blocks.values()].map(
      (block) => block.text,
    ),
  ).toEqual(["new segment"]);
  display.reset();
  expect(display.children.size).toBe(0);
});
it("bounds child presentation and marks omissions instead of claiming complete history", () => {
  const display = boot();
  display.accept(live(101, [set(text("long", 1, "x".repeat(300_000)))], child));
  const observed = display.children.get("exec-one")!.display;
  expect(observed.gap).toBe(true);
  expect([...observed.blocks.values()][0].text.length).toBeLessThanOrEqual(
    128 * 1024,
  );
  for (let i = 2; i < 150; i++)
    display.accept(live(101 + i, [set(text(`m-${i}`, i, "child"))], child));
  expect(observed.blocks.size).toBeLessThanOrEqual(128);
});
it("keeps task and context activity scoped while preserving the complete summary", () => {
  const summary = "# Summary\n\n" + "full ".repeat(20_000);
  const display = boot([
    observation("task", 1, "a13n.harness.state", {
      payload: {
        type: "task_changed",
        task: {
          id: "one",
          subject: "Root task",
          status: "in_progress",
          active_form: "Working",
        },
      },
    }),
    observation(
      "child-task",
      2,
      "a13n.harness.state",
      {
        payload: {
          type: "task_changed",
          task: { id: "one", subject: "Child task", status: "pending" },
        },
      },
      { subagentRunId: "inline" },
    ),
    observation("context", 3, "a13n.context.handoff_summary", {
      operation_id: "op",
      summary,
    }),
    observation("snapshot", 4, "a13n.harness.context", {
      payload: { type: "context_snapshot" },
    }),
  ]);
  expect(display.blocks.get("run-one:task:one")?.text).toBe("Working");
  expect(display.blocks.get("run-one:inline:task:one")?.subagentRunId).toBe(
    "inline",
  );
  expect(display.blocks.get("context:op")?.result).toBe(summary);
  expect(display.blocks.size).toBe(3);
});
it("cuts over only checkpoint-covered blocks on both reconnect and live delivery", () => {
  const display = boot(
    [text("saved", 2, "saved"), text("later", 5, "unsaved")],
    { checkpoint: 3 },
  );
  expect(display.blocksAfter("checkpoint").map((block) => block.text)).toEqual([
    "unsaved",
  ]);
  display.accept(
    live(101, [
      set(
        observation("checkpoint", 6, "a13n.harness_ui.checkpoint", {
          continuation_id: "new",
        }),
      ),
    ]),
  );
  display.accept(live(102, [set(text("last", 7, "last"))]));
  expect(display.blocksAfter("new").map((block) => block.text)).toEqual([
    "last",
  ]);
  expect(showFocusedOutput(display, "new", "run-one")).toBe(true);
  expect(showFocusedOutput(display, "new", "other-run")).toBe(false);
});
it("keeps unsaved initial output until replacement saved history is selected", () => {
  const display = boot([text("m", 1, "answer")]);
  expect(showFocusedOutput(display, "initial:state")).toBe(true);
  expect(showFocusedOutput(display, "saved")).toBe(false);
  expect(showFocusedOutput(display, "saved", undefined, true)).toBe(true);
});
it("updates context using latest root request attribution, including resumed generations", () => {
  const display = boot();
  const usage = (position: number, records: unknown[], resumed = false) => {
    const value = observation(
      `usage-${position}`,
      position,
      "a13n.harness.usage",
      {
        payload: {
          type: "usage_report",
          records,
          ...(resumed ? { usage_id: "generation" } : {}),
        },
      },
    );
    if (resumed)
      (value.content.value as Record<string, unknown>).run_id = "run-one";
    return value;
  };
  const record = (ordinal: number, run = "run-one", extra = {}) => ({
    kind: "model",
    run_id: run,
    source: "agent",
    response_ordinal: ordinal,
    request_usage: { input_tokens: ordinal * 10, output_tokens: 2 },
    ...extra,
  });
  display.accept(
    live(101, [
      set(
        usage(1, [
          record(2),
          record(20, "child"),
          record(21, "run-one", { delegation_id: "child" }),
        ]),
      ),
    ]),
  );
  expect(display.contextUsage).toEqual({ ordinal: 2, tokens: 22 });
  display.accept(live(102, [set(usage(2, [record(1)]))]));
  expect(display.contextUsage?.ordinal).toBe(2);
  display.accept(
    live(103, [
      set(
        usage(
          3,
          [
            record(3, "previous"),
            record(30, "previous", { parent_agent_instance_id: "parent" }),
          ],
          true,
        ),
      ),
    ]),
  );
  expect(display.contextUsage).toEqual({ ordinal: 3, tokens: 32 });
});
it("marks recovery resumed only for visible root progress and renders terminal interruption", () => {
  const display = boot([
    observation("retry", 1, "a13n.harness.recovery", {
      payload: { type: "model_retry_scheduled", attempt: 2 },
    }),
  ]);
  expect(display.recovery?.state).toBe("retrying");
  display.accept(
    live(101, [
      set(text("hidden", 2, "hidden", { metadata: { display: false } })),
      set(text("inline", 3, "child", { subagentRunId: "inline" })),
    ]),
  );
  expect(display.recovery?.state).toBe("retrying");
  display.accept(live(102, [set(text("root", 4, "resumed"))]));
  expect(display.recovery?.state).toBe("resumed");
  display.accept(
    live(103, [
      set(
        item(
          "end",
          5,
          { event: { type: "RUN_FINISHED", outcome: { type: "interrupt" } } },
          "observation",
        ),
      ),
    ]),
  );
  expect(display.blocks.get("run-one:execution")?.name).toBe(
    "Execution suspended",
  );
});
it("projects lifecycle after compact tools and keeps inline suspension separate", () => {
  const display = boot([
    item(
      "root",
      1,
      { toolCallId: "same", toolCallName: "root" },
      "tool_call",
      "in_progress",
    ),
    item(
      "child",
      2,
      { toolCallId: "same", toolCallName: "child", subagentRunId: "inline" },
      "tool_call",
      "in_progress",
    ),
    item(
      "end",
      3,
      {
        event: {
          type: "SUBAGENT_FINISHED",
          subagentRunId: "inline",
          name: "Explorer",
          outcome: { type: "suspended" },
        },
      },
      "observation",
    ),
  ]);
  expect(display.blocks.get("run-one:inline:inline")?.text).toBe("Suspended");
  expect(display.blocks.get("run-one:inline:same")?.stopped).toBe(true);
  expect(display.blocks.get("run-one:same")?.stopped).not.toBe(true);
});
it("shares process inspection across root and child item results and closes exact scopes", () => {
  const display = boot([
    item(
      "shell",
      1,
      {
        toolCallId: "shell",
        toolCallName: "shell_exec",
        arguments: JSON.stringify({ command: "pnpm dev" }),
        value: { process_id: "process-one", status: { phase: "running" } },
      },
      "tool_call",
    ),
  ]);
  display.accept(
    live(
      101,
      [
        set(
          item(
            "child-shell",
            1,
            {
              toolCallId: "shell",
              toolCallName: "shell_exec",
              arguments: "{}",
              value: {
                process_id: "process-one",
                status: { phase: "running" },
              },
            },
            "tool_call",
          ),
        ),
      ],
      child,
    ),
  );
  expect(display.processes.background).toHaveLength(2);
  expect(display.processes.background[0].command).toBe("pnpm dev");
  display.accept(
    live(
      102,
      [
        set(
          observation("exit", 2, "a13n.shell.status", {
            process_id: "process-one",
            phase: "exited",
            exit_code: 2,
          }),
        ),
      ],
      child,
    ),
  );
  display.accept(
    live(103, [
      set(
        item(
          "end",
          3,
          { event: { type: "RUN_FINISHED", outcome: { type: "success" } } },
          "observation",
        ),
      ),
    ]),
  );
  expect(display.processes.background.map((value) => value.phase)).toEqual([
    "unavailable",
    "exited",
  ]);
  display.reset();
  expect(display.processes.background).toEqual([]);
});
it("keeps refresh reasons independent of compact presentation", () => {
  expect(focusRefresh(live(101, [], { event_type: "RUN_FINISHED" }))).toBe(
    "lifecycle",
  );
  expect(
    focusRefresh(
      live(102, [], { payload: { name: "a13n.harness_ui.checkpoint" } }),
    ),
  ).toBe("checkpoint");
  expect(
    focusRefresh(
      live(103, [], {
        payload: { value: { event: { payload: { type: "usage_report" } } } },
      }),
    ),
  ).toBe("usage");
});
it("publishes replacement snapshots only after replay completes and retains last good presentation", () => {
  const display = boot([text("old", 1, "visible")]);
  const { socket, transport } = connectedTransport();
  const invalidate = vi.fn();
  const close = watchThread(
    transport,
    "thread-one",
    display,
    vi.fn(),
    vi.fn(),
    invalidate,
  );
  try {
    socket().open();
    socket().frame(snapshot(1));
    expect(display.blocks.get("run-one:old")?.text).toBe("visible");
    socket().frame(replay([text("new", 2, "replacement")]));
    expect(display.blocks.get("run-one:old")?.text).toBe("visible");
    socket().frame(ready());
    expect(display.blocks.get("run-one:new")?.text).toBe("replacement");
    expect(invalidate).toHaveBeenCalledWith("reconcile");
  } finally {
    close();
  }
});
it.each([false, true])(
  "retains output until replacement history arrives (next Run: %s)",
  (nextRun) => {
    const display = boot([text("old", 1, "visible")]);
    const replacement = snapshot(nextRun ? 0 : undefined);
    if (replacement.kind !== "snapshot") throw new Error("fixture");
    replacement.snapshot.thread.continuation_id = "saved-new";
    if (replacement.snapshot.root_stream) {
      replacement.snapshot.root_stream.run_id = "run-two";
      replacement.snapshot.root_stream.base_continuation_id = "saved-new";
    }
    const { socket, transport } = connectedTransport();
    const invalidate = vi.fn();
    const close = watchThread(
      transport,
      "thread-one",
      display,
      vi.fn(),
      vi.fn(),
      invalidate,
    );
    try {
      socket().open();
      socket().frame(replacement);
      if (nextRun) socket().frame(ready());
      expect(
        display
          .presentationFor("initial:state")
          .blocksAfter(null)
          .map((block) => block.text),
      ).toEqual(["visible"]);
      expect(display.presentationFor("saved-new")).toBe(display);
      expect(display.retainedPresentation).toBeUndefined();
    } finally {
      close();
    }
  },
);
it("reboots a root Run race without reconnecting the physical socket", async () => {
  vi.useFakeTimers();
  const display = boot();
  const { socket, transport } = connectedTransport();
  const invalidate = vi.fn();
  const close = watchThread(
    transport,
    "thread-one",
    display,
    vi.fn(),
    vi.fn(),
    invalidate,
  );
  try {
    socket().open();
    socket().frame(live(101, [], { run_id: "run-two" }));
    await vi.advanceTimersByTimeAsync(1);
    expect(socket().sent.at(-1)?.after).toBeNull();
    expect(FakeWebSocket.instances).toHaveLength(1);
    const replacement = snapshot(0);
    if (replacement.kind !== "snapshot") throw new Error("fixture");
    replacement.snapshot.root_stream!.run_id = "run-two";
    replacement.snapshot.root_stream!.base_continuation_id = "saved";
    socket().frame(replacement);
    socket().frame(ready());
    expect(display.runId).toBe("run-two");
    expect(display.baseContinuation).toBe("saved");
  } finally {
    close();
  }
});
it("backs off repeated snapshot races while retaining visible output", async () => {
  vi.useFakeTimers();
  const display = boot([text("kept", 1, "visible")]);
  const { socket, transport } = connectedTransport();
  const close = watchThread(
    transport,
    "thread-one",
    display,
    vi.fn(),
    vi.fn(),
    vi.fn(),
  );
  try {
    socket().open();
    const reset = () =>
      socket().frame({ kind: "reset", reason: "live_snapshot_changed" });
    const count = () =>
      socket().sent.filter((value) => value.kind === "subscribe").length;
    reset();
    await vi.advanceTimersByTimeAsync(1);
    expect(count()).toBe(2);
    reset();
    expect(display.blocks.get("run-one:kept")?.text).toBe("visible");
    await vi.advanceTimersByTimeAsync(1000);
    expect(count()).toBe(3);
    reset();
    await vi.advanceTimersByTimeAsync(1000);
    expect(count()).toBe(3);
    await vi.advanceTimersByTimeAsync(1000);
    expect(count()).toBe(4);
  } finally {
    close();
  }
});

it("refreshes durable checkpoint state during a quiet tail and stops when unsubscribed", async () => {
  vi.useFakeTimers();
  const display = boot([text("visible", 1, "answer")]);
  const { socket, transport } = connectedTransport();
  const invalidate = vi.fn();
  const close = watchThread(
    transport,
    "thread-one",
    display,
    vi.fn(),
    vi.fn(),
    invalidate,
  );
  socket().open();
  await vi.advanceTimersByTimeAsync(10_000);
  expect(invalidate).toHaveBeenCalledWith("checkpoint");
  invalidate.mockClear();
  close();
  await vi.advanceTimersByTimeAsync(30_000);
  expect(invalidate).not.toHaveBeenCalled();
});
