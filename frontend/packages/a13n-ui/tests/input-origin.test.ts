import { expect, it } from "vitest";
import { inputOrigin, type InputOrigin } from "../src/input-origin";

const unknownOrigin = {
  kind: "event",
  sender: { kind: "system", name: "unknown" },
};
const execution = {
  execution_id: "exec-one",
  kind: "tool",
  name: "shell_exec",
  state: "succeeded",
};

it.each(["message", "agent_message", "result", "event"] as const)(
  "reads current %s provenance and ignores unknown nested fields",
  (kind) => {
    expect(
      inputOrigin({
        "a13n.input": {
          id: "input-one",
          kind,
          sender: {
            kind: "connector",
            thread_id: "thread-one",
            name: "Connector",
            future: true,
          },
          execution: { ...execution, future: true },
          future: true,
        },
      }),
    ).toEqual({
      kind,
      sender: { kind: "connector", thread_id: "thread-one", name: "Connector" },
      execution,
    });
  },
);

it.each(["user", "agent", "system", "connector"] as const)(
  "reads a %s sender with absent or null optional fields",
  (kind) => {
    expect(
      inputOrigin({
        "a13n.input": {
          kind: "message",
          sender: { kind, name: null, thread_id: null },
          execution: null,
        },
      }),
    ).toEqual({ kind: "message", sender: { kind } });
  },
);

const legacy: [Record<string, unknown>, InputOrigin][] = [
  [
    { "a13n.steering-source": "async_subagent" },
    { kind: "result", sender: { kind: "agent", name: "async_subagent" } },
  ],
  [
    { "a13n.steering-source": "tool_execution" },
    { kind: "result", sender: { kind: "system", name: "tool_execution" } },
  ],
  [
    { "a13n.steering-source": "parent_agent" },
    { kind: "agent_message", sender: { kind: "agent", name: "parent" } },
  ],
  [
    { "a13n.steering-source": "background_process" },
    { kind: "event", sender: { kind: "system", name: "background_process" } },
  ],
  [
    { "a13n.steering-source": "envd_session" },
    { kind: "event", sender: { kind: "system", name: "envd_session" } },
  ],
  [
    {
      harness_ui: {
        thread_message: {
          source_thread_id: "thread-one",
          source_thread_title: "Other chat",
          ignored: true,
        },
      },
    },
    {
      kind: "agent_message",
      sender: { kind: "agent", thread_id: "thread-one", name: "Other chat" },
    },
  ],
  [
    {
      harness_ui: {
        thread_message: { source_thread_id: 2, source_thread_title: false },
      },
    },
    { kind: "agent_message", sender: { kind: "agent" } },
  ],
  [
    { source_id: "a13n-harness-ui.thread-lifecycle" },
    { kind: "event", sender: { kind: "system", name: "thread-lifecycle" } },
  ],
];
it.each(legacy)("maps stored legacy metadata %j", (metadata, expected) => {
  expect(inputOrigin(metadata)).toEqual(expected);
  expect(inputOrigin({ "a13n.input": expected })).toEqual(expected);
});

it.each([
  null,
  false,
  [],
  "message",
  {},
  { kind: "other", sender: { kind: "user" } },
  { kind: "message", sender: null },
  { kind: "message", sender: { kind: "other" } },
  { kind: "message", sender: { kind: "user", thread_id: 1 } },
  { kind: "message", sender: { kind: "user", name: false } },
  { id: 3, kind: "message", sender: { kind: "user" } },
  ...[
    {},
    { ...execution, state: "completed" },
    { ...execution, kind: "other" },
    { ...execution, execution_id: 1 },
    { ...execution, name: null },
  ].map((value) => ({
    kind: "result",
    sender: { kind: "system" },
    execution: value,
  })),
])("never treats a malformed current marker as user input: %j", (marker) => {
  expect(
    inputOrigin({
      "a13n.input": marker,
      "a13n.steering-source": "parent_agent",
    }),
  ).toEqual(unknownOrigin);
});

it.each([
  undefined,
  null,
  [],
  {},
  { display: true },
  { "a13n.steering-source": "future" },
  { harness_ui: { thread_message: false } },
])("leaves plain or unrelated input unmarked: %j", (metadata) => {
  expect(inputOrigin(metadata)).toBeNull();
});

it("prefers the current marker over legacy provenance", () => {
  expect(
    inputOrigin({
      "a13n.input": { kind: "event", sender: { kind: "system", name: "new" } },
      "a13n.steering-source": "parent_agent",
    }),
  ).toEqual({ kind: "event", sender: { kind: "system", name: "new" } });
});
