import { expect, it } from "vitest";
import type { DisplayItem } from "./display";
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

it("leaves an unfinished Item without an end time", () => {
  expect(
    presentItem(item({ state: "interrupted", ended_at: null })),
  ).toMatchObject({ startedAt: "2026-09-08T00:00:01Z", endedAt: null });
  expect(presentItem(item({ ended_at: undefined })).endedAt).toBeNull();
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
