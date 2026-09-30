import { afterEach, expect, it, vi } from "vitest";
import type {
  DisplayBlock,
  DisplayDelta,
  DisplaySnapshot,
} from "a13n-ui/display";
import { createTransport, type Schema } from "../transport/client";
import { mockWebSocket, FakeWebSocket } from "../../tests/fake-websocket";
import {
  FocusDisplay,
  focusFrame,
  showFocusedOutput,
  watchThread,
} from "./stream";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
const producer = { run_id: "run-one", generation: "generation-one" };
function block(
  id = "answer",
  text = "replay",
  extra: Partial<DisplayBlock> = {},
): DisplayBlock {
  return {
    id,
    scope_id: "run-one",
    kind: "text",
    revision: 1,
    status: "running",
    content: { text },
    message_index: 0,
    part_index: 0,
    ...extra,
  };
}
function baseline(
  blocks: DisplayBlock[] = [],
  run = "run-one",
  sequence = 1,
): DisplaySnapshot {
  return {
    format: "display/1",
    position: { producer: { ...producer, run_id: run }, sequence },
    scopes: [
      {
        id: run,
        thread_id: run === "run-one" ? "thread-one" : "child-one",
        run_id: run,
        parent_scope_id: null,
        parent_tool_call_id: null,
        invocation_id: null,
        status: "running",
      },
    ],
    blocks,
    omitted: 0,
    continuity: {},
  };
}
function summary(snapshot: DisplaySnapshot, checkpoints = {}) {
  const child = snapshot.position.producer.run_id !== "run-one";
  return {
    thread_id: child ? "child-one" : "thread-one",
    run_id: snapshot.position.producer.run_id,
    parent_thread_id: child ? "thread-one" : null,
    execution_id: child ? "exec-one" : null,
    base_continuation_id: null,
    position: snapshot.position,
    checkpoints,
  };
}
function opening(
  root?: DisplaySnapshot,
  children: DisplaySnapshot[] = [],
  checkpoints = {},
) {
  return focusFrame({
    kind: "snapshot",
    resume_cursor: root || children.length ? null : "cursor-one",
    snapshot: {
      epoch: "epoch-one",
      cutover_sequence: 100,
      thread: { thread: { thread_id: "thread-one" }, continuation_id: null },
      recent_events: [
        {
          event_type: "TEXT_MESSAGE_CONTENT",
          payload: { delta: "diagnostic only" },
        },
      ],
      root_stream: root ? summary(root, checkpoints) : null,
      child_streams: children.map((s) => summary(s)),
    },
  });
}
function chunks(
  snapshot: DisplaySnapshot,
  sequences?: Record<string, number>,
  controls: unknown[] = [],
) {
  const data = JSON.stringify({
    display: snapshot,
    block_sequences:
      sequences ?? Object.fromEntries(snapshot.blocks.map((b) => [b.id, 1])),
    controls,
  });
  const middle = Math.floor(data.length / 2);
  return [
    focusFrame({
      kind: "display_chunk",
      run_id: snapshot.position.producer.run_id,
      index: 0,
      data: data.slice(0, middle),
    }),
    focusFrame({
      kind: "display_chunk",
      run_id: snapshot.position.producer.run_id,
      index: 1,
      data: data.slice(middle),
    }),
    focusFrame({
      kind: "display_commit",
      run_id: snapshot.position.producer.run_id,
      chunk_count: 2,
    }),
  ];
}
function ready(cursor = "ready") {
  return focusFrame({ kind: "ready", resume_cursor: cursor });
}
function boot(
  display: FocusDisplay,
  root = baseline(),
  children: DisplaySnapshot[] = [],
  checkpoints = {},
  sequences?: Record<string, number>,
) {
  display.accept(opening(root, children, checkpoints));
  for (const frame of chunks(root, sequences)) display.accept(frame);
  for (const child of children)
    for (const frame of chunks(child)) display.accept(frame);
  display.accept(ready());
}
function event(sequence: number, values: Record<string, unknown>) {
  return focusFrame({
    kind: "event",
    resume_cursor: `cursor-${sequence}`,
    event: {
      epoch: "epoch-one",
      sequence,
      run_kind: "root",
      root_thread_id: "thread-one",
      thread_id: "thread-one",
      run_id: "run-one",
      parent_thread_id: null,
      execution_id: null,
      payload: null,
      payload_omitted: false,
      ...values,
    },
  });
}
function delta(
  sequence: number,
  operations: DisplayDelta["operations"],
  from = 1,
  child = false,
) {
  return event(sequence, {
    event_type: "DISPLAY_DELTA",
    ...(child
      ? {
          run_kind: "child",
          thread_id: "child-one",
          run_id: "run-child",
          parent_thread_id: "thread-one",
          execution_id: "exec-one",
        }
      : {}),
    delta: {
      format: "display-delta/1",
      producer: { ...producer, run_id: child ? "run-child" : "run-one" },
      from_sequence: from,
      through_sequence: from + 1,
      operations,
    },
  });
}
function append(value = "live", revision = 1): DisplayDelta["operations"] {
  return [
    {
      op: "block.append",
      id: "answer",
      field: "text",
      value,
      expected_revision: revision,
      revision: revision + 1,
    },
  ];
}
function control(
  sequence: number,
  name: string,
  source: unknown,
  child = false,
) {
  return event(sequence, {
    event_type: "CUSTOM",
    payload: { name, value: { event: source } },
    ...(child
      ? {
          run_kind: "child",
          thread_id: "child-one",
          run_id: "run-child",
          parent_thread_id: "thread-one",
          execution_id: "exec-one",
        }
      : {}),
  });
}
function connectedTransport() {
  vi.stubGlobal("window", { location: { origin: "http://localhost" } });
  return { socket: mockWebSocket(), transport: createTransport("", vi.fn()) };
}

it("stages chunks, commits exact coverage and only publishes the cutover cursor after ready", () => {
  const display = new FocusDisplay(),
    root = baseline([block()]);
  display.accept(opening(root));
  expect(display.blocks.size).toBe(0);
  const frames = chunks(root);
  display.accept(frames[0]);
  display.accept(frames[1]);
  expect(display.blocks.size).toBe(0);
  expect(display.cursor).toBeUndefined();
  display.accept(frames[2]);
  display.accept(ready());
  display.accept(delta(105, append()));
  display.accept(delta(105, append("duplicate")));
  expect(display.blocks.get("answer")?.text).toBe("replaylive");
  expect(display.cursor).toBe("cursor-105");
});

it("rejects incomplete, out-of-order, oversized and coverage-mismatched baselines", () => {
  const root = baseline([block()]);
  for (const mode of ["missing", "order", "budget", "coverage"]) {
    const display = new FocusDisplay(mode === "budget" ? 1 : undefined);
    display.accept(opening(root));
    expect(() => {
      if (mode === "missing") display.accept(ready());
      else if (mode === "order") display.accept(chunks(root)[1]);
      else
        for (const frame of chunks(
          mode === "coverage" ? baseline([block()], "run-one", 2) : root,
        ))
          display.accept(frame);
    }).toThrow();
    expect(display.cursor).toBeUndefined();
  }
});

it("requires contiguous semantic deltas but permits sparse lineage transport sequences", () => {
  const display = new FocusDisplay();
  boot(display, baseline([block()]));
  display.accept(delta(900, append(" good")));
  expect(() => display.accept(delta(1000, append(" bad", 2), 3))).toThrow(
    "Noncontiguous",
  );
  expect(display.cursor).toBe("cursor-900");
  expect(display.blocks.get("answer")?.text).toBe("replay good");
});

it("does not partially publish a batch or acknowledge omitted output", () => {
  const display = new FocusDisplay();
  boot(display, baseline([block()]));
  expect(() =>
    display.accept(
      delta(101, [
        ...append("lost"),
        {
          op: "block.append",
          id: "absent",
          field: "text",
          expected_revision: 1,
          revision: 2,
          value: "x",
        },
      ]),
    ),
  ).toThrow();
  expect(display.blocks.get("answer")?.text).toBe("replay");
  expect(display.cursor).toBe("ready");
  expect(() =>
    display.accept(
      event(102, { event_type: "DISPLAY_DELTA", payload_omitted: true }),
    ),
  ).toThrow("omitted");
});

it("isolates child producers and resets when their generation changes", () => {
  const display = new FocusDisplay();
  boot(display, baseline([block()]), [
    baseline(
      [block("answer", "child", { scope_id: "run-child" })],
      "run-child",
    ),
  ]);
  display.accept(delta(101, append(" more"), 1, true));
  expect(display.blocks.get("answer")?.text).toBe("replay");
  expect(
    display.children.get("exec-one")?.display.blocks.get("answer")?.text,
  ).toBe("child more");
  const changed = delta(102, append("wrong", 2), 2, true);
  if (changed.kind !== "event" || !changed.event.delta) throw Error();
  changed.event.delta.producer.generation = "new";
  expect(() => display.accept(changed)).toThrow("producer changed");
  display.accept({ kind: "reset", reason: "expired" });
  expect(display.blocks.size).toBe(0);
  expect(display.children.size).toBe(0);
  expect(display.cursor).toBeUndefined();
});

it("applies producer omission rather than reconstructing an evicted child transcript", () => {
  const display = new FocusDisplay();
  boot(display, baseline([block()]));
  display.accept(
    delta(101, [{ op: "blocks.remove", ids: ["answer"], omitted: 1 }]),
  );
  expect(display.blocks.size).toBe(0);
  expect(display.gap).toBe(true);
  expect(display.cursor).toBe("cursor-101");
});

it("maps native reasoning, tool outcomes, provider identity, edits and retained media without event folding", () => {
  const edit = { file_path: "x.txt", before: "old", after: "new" };
  const image = {
    thread_id: "thread-one",
    attachment: {
      attachment_id: "screen",
      name: "screen.png",
      media_type: "image/png",
      size: 128,
    },
  };
  const app = {
    app_id: "app-one",
    thread_id: "thread-one",
    run_id: "run-one",
    tool_call_id: "call",
    server_id: "server",
    tool_name: "edit",
  };
  const display = new FocusDisplay();
  boot(
    display,
    baseline([
      block("hidden", "", {
        kind: "input",
        content: { text: "hidden", metadata: { display: false } },
      }),
      block("thinking", "reasoning", {
        kind: "reasoning",
        status: "succeeded",
      }),
      block("tool", "", {
        kind: "tool_chunk",
        status: "failed",
        content: {
          name: "edit",
          tool_call_id: "call",
          arguments: "{}",
          arguments_complete: true,
          result: "denied",
          outcome: "denied",
          metadata: {
            "a13n.harness-ui.applied_edit": edit,
            "a13n.harness-ui.tool_images": [image],
            "a13n.harness-ui.mcp_apps": [app],
          },
        },
      }),
      block("native", "", {
        kind: "tool_chunk",
        content: {
          name: "web_search",
          tool_call_id: "call",
          native: true,
          provider: "openai",
          arguments: "{}",
          result: "found",
          outcome: "success",
        },
      }),
    ]),
  );
  expect(display.blocks.has("hidden")).toBe(false);
  expect(display.blocks.get("thinking")).toMatchObject({
    kind: "thinking",
    text: "reasoning",
    done: true,
  });
  expect(display.blocks.get("tool")).toMatchObject({
    outcome: "denied",
    edit,
    images: [image],
    apps: [app],
    done: true,
  });
  expect(display.blocks.get("native")).toMatchObject({
    provider: "openai",
    result: "found",
  });
});

it("renders compact context and task summaries but not model instrumentation", () => {
  const display = new FocusDisplay();
  boot(
    display,
    baseline([
      block("summary", "summary text", {
        kind: "context_summary",
        status: "succeeded",
        content: { text: "summary text", kind: "handoff" },
      }),
      block("task", "", {
        kind: "extension",
        content: {
          name: "a13n.display.task",
          value: {
            id: "task",
            subject: "Do work",
            status: "in_progress",
            active_form: "Working",
          },
        },
      }),
      block("request", "", {
        kind: "extension",
        content: { name: "a13n.display.model_request", value: {} },
      }),
    ]),
  );
  expect(display.blocks.get("summary")).toMatchObject({
    context: "handoff",
    result: undefined,
    done: false,
  });
  expect(display.blocks.get("task")).toMatchObject({
    kind: "task",
    text: "Working",
  });
  expect(display.blocks.has("request")).toBe(false);
});

it("uses exact checkpoint coverage for delayed saved-history selection and cold replay", () => {
  const display = new FocusDisplay();
  boot(
    display,
    baseline(
      [
        block("input", "First input"),
        block("answer", "First answer"),
        block("steer", "Instruction"),
        block("suffix", "Still streaming"),
      ],
      "run-one",
      4,
    ),
    [],
    { "checkpoint-a": 1, "checkpoint-b": 3 },
    { input: 1, answer: 2, steer: 3, suffix: 4 },
  );
  expect(display.blocksAfter("checkpoint-a").map((b) => b.text)).toEqual([
    "First answer",
    "Instruction",
    "Still streaming",
  ]);
  expect(display.blocksAfter("checkpoint-b").map((b) => b.text)).toEqual([
    "Still streaming",
  ]);
  expect(showFocusedOutput(display, "checkpoint-b", null)).toBe(true);
  expect(showFocusedOutput(display, "terminal-head")).toBe(false);
  expect(showFocusedOutput(display, "initial:thread-one")).toBe(true);
  display.accept(
    control(101, "a13n.harness_ui.checkpoint", {
      continuation_id: "checkpoint-c",
      display_sequence: 4,
    }),
  );
  expect(display.blocksAfter("checkpoint-c")).toEqual([]);
  expect(display.blocks.size).toBe(4);
});

it("filters canonical root context samples, including accounting resumes and forwarded child usage", () => {
  const display = new FocusDisplay();
  boot(display);
  const record = (ordinal: number, tokens: number, extra = {}) => ({
    kind: "model",
    run_id: "run-one",
    response_ordinal: ordinal,
    request_usage: { input_tokens: tokens, output_tokens: 20 },
    ...extra,
  });
  display.accept(
    control(101, "a13n.harness.usage", {
      payload: {
        type: "usage_report",
        records: [
          record(1, 200),
          record(9, 999, { parent_agent_instance_id: "parent" }),
          record(10, 999, { source: "helper" }),
        ],
      },
    }),
  );
  expect(display.contextUsage).toEqual({ tokens: 220, ordinal: 1 });
  for (const [index, sourceRun] of ["run-one", "child-run"].entries()) {
    display.accept(
      event(102 + index, {
        event_type: "CUSTOM",
        payload: {
          name: "a13n.harness.usage",
          value: {
            run_id: sourceRun,
            event: {
              payload: {
                type: "usage_report",
                usage_id: "original",
                records: [
                  record(2, sourceRun === "run-one" ? 300 : 999, {
                    run_id: "original",
                  }),
                ],
              },
            },
          },
        },
      }),
    );
  }
  expect(display.contextUsage).toEqual({ tokens: 320, ordinal: 2 });
  expect(display.blocks.size).toBe(0);
});

it("keeps process observations separate and does not resurrect a completed process from a retained tool result", () => {
  const tool = block("shell", "", {
    kind: "tool_chunk",
    content: {
      name: "shell_exec",
      arguments: '{"command":"pnpm dev"}',
      result: '{"process_id":"process-one","status":{"phase":"running"}}',
    },
  });
  const display = new FocusDisplay();
  boot(display, baseline([tool]));
  expect(display.processes.background[0].command).toBe("pnpm dev");
  display.accept(
    control(101, "a13n.shell.status", {
      process_id: "process-one",
      phase: "exited",
      exit_code: 2,
    }),
  );
  display.accept(
    delta(102, [
      {
        op: "block.put",
        expected_revision: 1,
        block: { ...tool, revision: 2 },
      },
    ]),
  );
  expect(display.processes.background[0]).toMatchObject({
    phase: "exited",
    exitCode: 2,
  });
});

it("marks recovery resumed only on visible progress, not hidden/instrumentation updates", () => {
  const display = new FocusDisplay();
  boot(display);
  display.accept(
    control(101, "a13n.harness.recovery", {
      payload: { type: "model_retry_scheduled", attempt: 2 },
    }),
  );
  display.accept(
    delta(102, [
      {
        op: "block.put",
        expected_revision: 0,
        block: block("hidden", "", {
          content: { text: "private", metadata: { display: false } },
        }),
      },
    ]),
  );
  expect(display.recovery?.state).toBe("retrying");
  display.accept(
    delta(103, [{ op: "block.put", expected_revision: 0, block: block() }], 2),
  );
  expect(display.recovery?.state).toBe("resumed");
});

it("publishes replacement presentation only after every baseline commits and ready arrives", async () => {
  const display = new FocusDisplay();
  boot(display, baseline([block("answer", "Retained")]));
  const { socket, transport } = connectedTransport(),
    changed = vi.fn(),
    invalidate = vi.fn();
  const close = watchThread(
    transport,
    "thread-one",
    display,
    changed,
    vi.fn(),
    invalidate,
  );
  try {
    socket().open();
    const replacement = baseline([block("answer", "Replacement")]);
    socket().frame(opening(replacement));
    for (const frame of chunks(replacement)) socket().frame(frame);
    expect(display.blocks.get("answer")?.text).toBe("Retained");
    expect(changed).not.toHaveBeenCalled();
    socket().frame(ready("new"));
    expect(display.blocks.get("answer")?.text).toBe("Replacement");
    expect(changed).toHaveBeenCalledTimes(1);
    expect(display.cursor).toBe("new");
    expect(invalidate).toHaveBeenCalledWith("reconcile");
  } finally {
    close();
    transport.close();
  }
});

it("does not resume interrupted staging with the last presentation cursor", async () => {
  vi.useFakeTimers();
  const display = new FocusDisplay();
  boot(display, baseline([block("answer", "Retained")]));
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
    socket().frame(opening(baseline()));
    expect(display.cursor).toBeUndefined();
    expect(display.blocks.get("answer")?.text).toBe("Retained");
    socket().close();
    await vi.advanceTimersByTimeAsync(1000);
    socket().open();
    expect(socket().sent.at(-1)?.after).toBeNull();
  } finally {
    close();
    transport.close();
  }
});

it("retains provisional text until replacement saved history is actually rendered", () => {
  const display = new FocusDisplay();
  boot(display, baseline([block("answer", "Retained")]));
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
    const frame = opening();
    if (frame.kind !== "snapshot") throw Error();
    frame.snapshot.thread.continuation_id = "saved-new";
    socket().frame(frame);
    expect(
      display.presentationFor("initial:thread-one").blocks.get("answer")?.text,
    ).toBe("Retained");
    expect(display.presentationFor("saved-new")).toBe(display);
    expect(display.retainedPresentation).toBeUndefined();
  } finally {
    close();
    transport.close();
  }
});

it("backs off repeated snapshot races without reconnecting unrelated physical channels", async () => {
  vi.useFakeTimers();
  const display = new FocusDisplay();
  boot(display, baseline([block()]));
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
      socket().sent.filter((item) => item.kind === "subscribe").length;
    reset();
    await vi.advanceTimersByTimeAsync(1);
    expect(count()).toBe(2);
    reset();
    await vi.advanceTimersByTimeAsync(1000);
    expect(count()).toBe(3);
    reset();
    await vi.advanceTimersByTimeAsync(1000);
    expect(count()).toBe(3);
    await vi.advanceTimersByTimeAsync(1000);
    expect(count()).toBe(4);
    expect(display.blocks.get("answer")?.text).toBe("replay");
    expect(FakeWebSocket.instances).toHaveLength(1);
  } finally {
    close();
    transport.close();
  }
});

it("rejects incompatible envelope shapes instead of accepting legacy AG-UI replay", () => {
  for (const value of [
    null,
    { kind: "snapshot" },
    { kind: "event", event: {} },
    { kind: "root_stream", run_id: "old", events: [] },
    { kind: "ready", resume_cursor: 1 },
  ])
    expect(() => focusFrame(value)).toThrow("Invalid");
});

it.each([false, true])(
  "joins context summary and lifecycle regardless of baseline order (reverse=%s)",
  (reverse) => {
    const summary = block("run-one:context:compact", "", {
      kind: "context_summary",
      status: "succeeded",
      content: {
        operation_id: "compact",
        kind: "handoff",
        text: "Retained summary",
        files: ["src/main.py"],
      },
    });
    const lifecycle = block("run-one:execution:compact", "", {
      kind: "extension",
      status: "running",
      content: {
        name: "a13n.display.context_operation",
        value: { operation_id: "compact", operation: "handoff" },
      },
    });
    const display = new FocusDisplay();
    boot(
      display,
      baseline(reverse ? [lifecycle, summary] : [summary, lifecycle]),
    );
    expect(display.blocks.size).toBe(1);
    expect(display.blocks.get(lifecycle.id)?.result).toBeUndefined();
    display.accept(
      delta(101, [
        {
          op: "block.put",
          expected_revision: 1,
          block: { ...lifecycle, revision: 2, status: "succeeded" },
        },
      ]),
    );
    expect(display.blocks.size).toBe(1);
    expect(display.blocks.get(lifecycle.id)?.result).toBe(
      "Retained summary\n\nFiles to inspect:\nsrc/main.py",
    );
    display.accept(
      delta(102, [{ op: "blocks.remove", ids: [summary.id], omitted: 1 }], 2),
    );
    expect(display.blocks.size).toBe(1);
    expect(display.blocks.get(lifecycle.id)?.result).toBe(
      "Summary content unavailable.",
    );
    display.accept(
      delta(103, [{ op: "blocks.remove", ids: [lifecycle.id], omitted: 2 }], 3),
    );
    expect(display.blocks.size).toBe(0);
  },
);
