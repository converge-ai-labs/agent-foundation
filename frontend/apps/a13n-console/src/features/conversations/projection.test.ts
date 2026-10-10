import { expect, it } from "vitest";
import { applyDelta, type DisplayItem } from "./display";
import { parseItemValue, presentItem, presentItems } from "./projection";

const item = (fields: Partial<DisplayItem>): DisplayItem => ({
  id: "itm_message",
  ordinal: 1,
  kind: "text_message",
  state: "completed",
  first_stream_id: "1-1",
  last_stream_id: "1-2",
  started_at: "2026-09-08T00:00:01Z",
  ended_at: "2026-09-08T00:00:04Z",
  content: {},
  ...fields,
});

it("presents a message, a tool call and protected reasoning from their content", () => {
  expect(
    presentItem(
      item({ content: { messageId: "msg_1", role: "user", text: "Hello" } }),
    ),
  ).toEqual({
    id: "itm_message",
    kind: "text_message",
    state: "completed",
    firstPosition: "1-1",
    lastPosition: "1-2",
    startedAt: "2026-09-08T00:00:01Z",
    endedAt: "2026-09-08T00:00:04Z",
    text: "Hello",
    role: "user",
    toolName: "",
    arguments: "",
    result: undefined,
    failure: undefined,
    protectedReasoning: false,
  });
  expect(
    presentItem(
      item({
        kind: "tool_call",
        state: "failed",
        content: {
          toolCallName: "read_file",
          arguments: '{"path":',
          failure: { code: "tool_failed", message: "Retry" },
        },
      }),
    ),
  ).toMatchObject({
    role: "assistant",
    toolName: "read_file",
    arguments: '{"path":',
    failure: { code: "tool_failed", message: "Retry" },
  });
  expect(
    presentItem(
      item({ kind: "reasoning_message", content: { encrypted_value: null } }),
    ).protectedReasoning,
  ).toBe(true);
});

it("leaves an unfinished or untimed Item without the times it never reported", () => {
  expect(
    presentItem(item({ state: "interrupted", ended_at: null })),
  ).toMatchObject({ startedAt: "2026-09-08T00:00:01Z", endedAt: null });
  expect(presentItem(item({ ended_at: undefined }))).toMatchObject({
    startedAt: "2026-09-08T00:00:01Z",
    endedAt: null,
  });
});

it("keeps steering provenance so an enqueued notice is not read as authored input", () => {
  expect(
    presentItem(
      item({
        content: {
          role: "user",
          metadata: {
            "a13n.steering-source": "async_subagent",
            display: false,
          },
        },
      }),
    ),
  ).toMatchObject({ steeringSource: "async_subagent", display: false });
});

it("presents the messages and tool calls in the order they first appeared", () => {
  expect(
    presentItems([
      item({ id: "second", first_stream_id: "2-1" }),
      item({ id: "observation", kind: "observation", first_stream_id: "1-5" }),
      item({ id: "first", first_stream_id: "1-10" }),
    ]).map((presented) => presented.id),
  ).toEqual(["first", "second"]);
});

it("parses a JSON value and keeps any other text as it is", () => {
  expect(parseItemValue('{"path":"a.md"}')).toEqual({ path: "a.md" });
  expect(parseItemValue('{"path":')).toBe('{"path":');
  expect(parseItemValue({ already: true })).toEqual({ already: true });
});

it("preserves source entry references from persisted user and steering content", () => {
  for (const input_source of ["user", "steering"]) {
    expect(
      presentItem(
        item({
          content: {
            role: "user",
            input_source,
            input_group: "model-attempt-group",
            metadata: { source_id: "inb_1234567890abcdef1234567890ab" },
          },
        }),
      ),
    ).toMatchObject({
      sourceId: "inb_1234567890abcdef1234567890ab",
      inputSource: input_source,
    });
  }
});

it("resolves live steering through Service metadata rather than the Harness input group", () => {
  const items = new Map<string, DisplayItem>();
  applyDelta(items, {
    run_id: "run_a",
    attempt: 1,
    sequence: 18,
    item: {
      id: "itm_steer",
      kind: "text_message",
      state: "completed",
      ordinal: 2,
    },
    event: {
      type: "CUSTOM",
      name: "a13n.input.steering",
      metadata: {
        display: true,
        source_id: "inb_abcdef1234567890abcdef123456",
      },
      value: {
        event: {
          input_id: "01a12147-bd16-73f2-b290-80198647ebc8",
          source: "steering",
          content: "Use revised numbers",
          message_id: "run-a:input:18",
          role: "user",
        },
      },
    },
  });
  expect(presentItems(items.values())).toMatchObject([
    {
      sourceId: "inb_abcdef1234567890abcdef123456",
      inputSource: "steering",
      text: "Use revised numbers",
    },
  ]);
});

it("preserves opaque source references for session-scoped author lookup", () => {
  for (const source_id of [
    "legacy-source",
    "model-attempt-a",
    "run_abcdef1234567890abcdef123456",
  ]) {
    expect(
      presentItem(
        item({
          content: {
            role: "user",
            input_group: "group",
            metadata: { source_id },
          },
        }),
      ).sourceId,
    ).toBe(source_id);
  }
});

it("keeps missing or oversized source references out of the author batch", () => {
  for (const source_id of [undefined, "", "a".repeat(73)]) {
    expect(
      presentItem(item({ content: { role: "user", metadata: { source_id } } }))
        .sourceId,
    ).toBeUndefined();
  }
});
