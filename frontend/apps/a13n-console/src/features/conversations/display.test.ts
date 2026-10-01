import { expect, it, vi } from "vitest";
import { createClient, type ThreadDelta } from "../../service-client";
import { fixtureRun } from "./transcript/fixture";
import {
  applyDelta,
  comparePositions,
  isFragment,
  isOmitted,
  readDisplay,
  type DisplayItem,
} from "./display";

it("reads the committed display of one Run", async () => {
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    expect(new URL((input as Request).url).pathname).toBe(
      "/api/v1/runs/run_2/items",
    );
    return Response.json({
      run: fixtureRun(),
      items: [],
      position: "1-4",
      complete: true,
      dropped: 0,
    });
  });
  const client = createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
    fetch,
  });
  const display = await readDisplay(
    client,
    "ws_1",
    "run_2",
    new AbortController().signal,
  );
  expect(display).toMatchObject({ position: "1-4", complete: true });
});

it("orders display positions numerically, at any size", () => {
  expect(comparePositions("100-10", "100-2")).toBeGreaterThan(0);
  expect(comparePositions("2-1", "10-0")).toBeLessThan(0);
  expect(comparePositions("3-7", "3-7")).toBe(0);
  expect(
    comparePositions("9007199254740993-0", "9007199254740992-0"),
  ).toBeGreaterThan(0);
  expect(() => comparePositions("1-01", "1-2")).toThrow(
    "Invalid display position.",
  );
  expect(() => comparePositions("1", "1-2")).toThrow();
});

const time = (second: number) =>
  Date.parse("2026-09-20T10:00:00.000Z") + second * 1000;
const iso = (second: number) => new Date(time(second)).toISOString();

function delta(
  sequence: number,
  event: ThreadDelta["event"],
  item: ThreadDelta["item"],
): ThreadDelta {
  return {
    run_id: "run_2",
    attempt: 1,
    sequence,
    event: { timestamp: time(sequence), ...event },
    item,
  };
}

const message = (state: "in_progress" | "completed") =>
  ({ id: "itm_message", kind: "text_message", state }) as const;
const call = (state: "in_progress" | "completed" | "failed") =>
  ({ id: "itm_call", kind: "tool_call", state }) as const;

function fold(deltas: ThreadDelta[], items = new Map<string, DisplayItem>()) {
  for (const next of deltas) applyDelta(items, next);
  return items;
}

it("folds live deltas into the Items the committed display records", () => {
  const items = fold([
    delta(
      1,
      { type: "TEXT_MESSAGE_START", messageId: "msg_1", role: "assistant" },
      message("in_progress"),
    ),
    delta(
      2,
      { type: "TEXT_MESSAGE_CONTENT", messageId: "msg_1", delta: "Reading " },
      message("in_progress"),
    ),
    delta(
      3,
      { type: "TEXT_MESSAGE_CONTENT", messageId: "msg_1", delta: "it" },
      message("in_progress"),
    ),
    delta(
      4,
      { type: "TEXT_MESSAGE_END", messageId: "msg_1" },
      message("completed"),
    ),
    delta(
      5,
      {
        type: "TOOL_CALL_START",
        toolCallId: "call_1",
        toolCallName: "read_file",
        parentMessageId: "msg_1",
      },
      call("in_progress"),
    ),
    delta(
      6,
      { type: "TOOL_CALL_ARGS", toolCallId: "call_1", delta: '{"path":' },
      call("in_progress"),
    ),
    delta(
      7,
      { type: "TOOL_CALL_ARGS", toolCallId: "call_1", delta: '"a.md"}' },
      call("in_progress"),
    ),
    delta(
      8,
      { type: "TOOL_CALL_END", toolCallId: "call_1" },
      call("in_progress"),
    ),
    delta(
      9,
      { type: "TOOL_CALL_RESULT", toolCallId: "call_1", content: "# A" },
      call("completed"),
    ),
    delta(
      10,
      {
        type: "CUSTOM",
        name: "a13n.harness.usage",
        value: { type: "usage_report" },
      },
      { id: "itm_usage", kind: "observation", state: "completed" },
    ),
    // Only an Item's delta changes the display.
    delta(11, { type: "RUN_FINISHED" }, null),
  ]);
  expect([...items.values()]).toEqual([
    {
      id: "itm_message",
      kind: "text_message",
      state: "completed",
      first_stream_id: "1-1",
      last_stream_id: "1-4",
      started_at: iso(1),
      ended_at: iso(4),
      content: { messageId: "msg_1", role: "assistant", text: "Reading it" },
    },
    {
      id: "itm_call",
      kind: "tool_call",
      state: "completed",
      first_stream_id: "1-5",
      last_stream_id: "1-9",
      started_at: iso(5),
      ended_at: iso(9),
      content: {
        toolCallId: "call_1",
        toolCallName: "read_file",
        parentMessageId: "msg_1",
        arguments: '{"path":"a.md"}',
        result: "# A",
      },
    },
    {
      id: "itm_usage",
      kind: "observation",
      state: "completed",
      first_stream_id: "1-10",
      last_stream_id: "1-10",
      started_at: iso(10),
      ended_at: iso(10),
      content: { name: "a13n.harness.usage", value: { type: "usage_report" } },
    },
  ]);
});

it("keeps protected reasoning and leaves times unknown without a timestamp", () => {
  const reasoning = {
    id: "itm_reasoning",
    kind: "reasoning_message",
    state: "in_progress",
  } as const;
  const items = fold([
    {
      ...delta(1, { type: "REASONING_MESSAGE_START" }, reasoning),
      event: { type: "REASONING_MESSAGE_START", messageId: "rsn_1" },
    },
    {
      ...delta(2, { type: "REASONING_ENCRYPTED_VALUE" }, reasoning),
      event: {
        type: "REASONING_ENCRYPTED_VALUE",
        entityId: "rsn_1",
        encryptedValue: "sealed",
      },
    },
  ]);
  expect(items.get("itm_reasoning")).toMatchObject({
    started_at: null,
    ended_at: null,
    content: { messageId: "rsn_1", encrypted_value: "sealed" },
  });
});

it("continues the committed display and skips what it already folded", () => {
  const committed: DisplayItem = {
    id: "itm_message",
    kind: "text_message",
    state: "in_progress",
    first_stream_id: "1-1",
    last_stream_id: "1-2",
    started_at: iso(1),
    content: { messageId: "msg_1", role: "assistant", text: "Hel" },
  };
  const items = fold(
    [
      delta(
        2,
        { type: "TEXT_MESSAGE_CONTENT", messageId: "msg_1", delta: "Hel" },
        message("in_progress"),
      ),
      delta(
        3,
        { type: "TEXT_MESSAGE_CONTENT", messageId: "msg_1", delta: "lo" },
        message("in_progress"),
      ),
    ],
    new Map([[committed.id, committed]]),
  );
  expect(items.get("itm_message")).toMatchObject({
    first_stream_id: "1-1",
    last_stream_id: "1-3",
    started_at: iso(1),
    content: { text: "Hello" },
  });
});

it("fails a call on the observation that reported it and holds that observation", () => {
  const items = fold([
    delta(
      1,
      {
        type: "TOOL_CALL_START",
        toolCallId: "call_1",
        toolCallName: "read_file",
      },
      call("in_progress"),
    ),
    delta(
      2,
      {
        type: "CUSTOM",
        name: "a13n.pydantic_ai.function_tool_result",
        value: { event: { part: { tool_call_id: "call_1" } } },
      },
      call("failed"),
    ),
  ]);
  expect(items.get("itm_call")).toMatchObject({
    state: "failed",
    last_stream_id: "1-2",
    ended_at: iso(2),
    content: { toolCallId: "call_1", toolCallName: "read_file" },
  });
  expect(items.get("1-2")).toMatchObject({
    kind: "observation",
    first_stream_id: "1-2",
    content: { name: "a13n.pydantic_ai.function_tool_result" },
  });
});

const PART_DELTA = "a13n.pydantic_ai.part_delta";

/** A tool call's streamed argument delta, as the stream protocol reports it. */
function argumentDelta(sequence: number, args: string, id = "itm_arguments") {
  return delta(
    sequence,
    {
      type: "CUSTOM",
      name: PART_DELTA,
      value: {
        thread_id: "thr_1",
        run_id: "harness_1",
        sequence,
        event: {
          index: 1,
          delta: { args_delta: args, part_delta_kind: "tool_call" },
          event_kind: "part_delta",
        },
      },
    },
    { id, kind: "observation", state: "completed" },
  );
}

it("extends one observation with the argument deltas the display merged", () => {
  const pieces = Array.from({ length: 300 }, (_, index) => `${index},`);
  const items = fold(
    pieces.map((piece, index) => argumentDelta(index + 1, piece)),
  );
  // The display commits the first delta's observation with every piece.
  expect([...items.values()]).toEqual([
    {
      id: "itm_arguments",
      kind: "observation",
      state: "completed",
      first_stream_id: "1-1",
      last_stream_id: "1-300",
      started_at: iso(1),
      ended_at: iso(1),
      content: {
        name: PART_DELTA,
        value: {
          thread_id: "thr_1",
          run_id: "harness_1",
          sequence: 1,
          event: {
            index: 1,
            delta: {
              args_delta: pieces.join(""),
              part_delta_kind: "tool_call",
            },
            event_kind: "part_delta",
          },
        },
      },
    },
  ]);
  // A delta the display kept apart is its own observation.
  fold([argumentDelta(302, "{}", "itm_other")], items);
  expect(items.get("itm_other")?.content.value).toMatchObject({
    event: { delta: { args_delta: "{}" } },
  });
});

it("recognizes the transport fragments of a large event", () => {
  expect(
    isFragment(
      delta(
        1,
        { type: "CUSTOM", name: "a13n.stream.fragment", value: {} },
        null,
      ),
    ),
  ).toBe(true);
  expect(isFragment(argumentDelta(1, "{"))).toBe(false);
});

it("recognizes content the display omitted over its limit", () => {
  expect(isOmitted({ omitted: true })).toBe(true);
  expect(isOmitted({ omitted: true, text: "kept" })).toBe(false);
  expect(isOmitted({})).toBe(false);
});

it.each(["user", "steering"])(
  "folds %s custom input as one completed user message without duplicate observation",
  (source) => {
    const event = {
      type: "CUSTOM",
      name: `a13n.input.${source}`,
      metadata: { source_id: "inbox_one", display: true },
      value: {
        event: {
          message_id: "run:input:1",
          role: "user",
          input_id: "input_one",
          source,
          content: "Question",
        },
      },
    };
    const items = fold([delta(1, event, message("completed"))]);
    expect(items.size).toBe(1);
    expect(items.get("itm_message")?.content).toEqual({
      messageId: "run:input:1",
      role: "user",
      text: "Question",
      metadata: event.metadata,
    });
    applyDelta(items, delta(1, event, message("completed")));
    expect(items.size).toBe(1);
  },
);

it("retains standard interrupt observations identically in live folding and replay", () => {
  const event = {
    type: "RUN_FINISHED",
    runId: "harness-root",
    threadId: "thread",
    outcome: {
      type: "interrupt",
      interrupts: [
        {
          id: "call",
          toolCallId: "call",
          reason: "approval",
          metadata: { tool_name: "shell" },
        },
      ],
    },
  };
  const next = delta(1, event, {
    id: "interrupt",
    kind: "observation",
    state: "completed",
  });
  const items = fold([next]);
  expect(items.get("interrupt")?.content).toEqual(next.event);
  const saved = JSON.stringify([...items.values()]);
  fold([next], items);
  expect(JSON.stringify([...items.values()])).toBe(saved);
});
