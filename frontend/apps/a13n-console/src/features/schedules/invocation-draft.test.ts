import { expect, it } from "vitest";
import { scheduleInvocation } from "../../shared/forms/validation";
import { invocationFields } from "./invocation-draft";

it.each([
  {},
  { payload: {} },
  { agent_id: "", payload: { content: [] } },
  {
    agent_id: "unfinished",
    payload: { content: [{ type: "text", text: "" }] },
  },
])(
  "converts incomplete drafts without requiring submission validity: %j",
  (value) => {
    const { draft, input } = invocationFields(JSON.stringify(value));
    expect(input).toBe("");
    expect(draft.agent_id).toBe("agent_id" in value ? value.agent_id : "");
    expect(draft.payload.content).toEqual([{ type: "text", text: "" }]);
    expect(() => scheduleInvocation(JSON.stringify(draft))).toThrow();
  },
);

it("preserves fields not represented by the form without accepting them for submission", () => {
  const value = {
    agent_id: "agt_0123456789abcdef0123",
    agent_revision_id: null,
    memories: [],
    options: { custom: false },
    custom: 0,
    payload: {
      custom: null,
      content: [{ type: "text", text: "Hello", custom: [] }],
    },
  };
  const { draft, input } = invocationFields(JSON.stringify(value));
  expect(draft).toEqual(value);
  expect(input).toBe("Hello");
  expect(() => scheduleInvocation(JSON.stringify(draft))).toThrow();
});

it.each([
  [null, "Invocation must be a JSON object."],
  [[], "Invocation must be a JSON object."],
  [{ agent_id: 1 }, "Agent ID must be text."],
  [{ agent_revision_id: false }, "Agent revision must be text or null."],
  [{ payload: null }, "Payload must be a JSON object."],
  [{ payload: { content: null } }, "Input content must be an array."],
  [
    { payload: { content: [{ type: "text", text: 1 }] } },
    "Input must be text.",
  ],
  [
    { payload: { content: [{ type: "json", value: {} }] } },
    "Fields require one text input. Use JSON to edit structured input.",
  ],
  [
    {
      payload: {
        content: [
          { type: "text", text: "a" },
          { type: "text", text: "b" },
        ],
      },
    },
    "Fields require one text input. Use JSON to edit structured input.",
  ],
])("rejects drafts that cannot be represented safely: %j", (value, message) => {
  expect(() => invocationFields(JSON.stringify(value))).toThrow(message);
});

it("reports malformed JSON and accepts complete invocations for submission", () => {
  expect(() => invocationFields("{")).toThrow(SyntaxError);
  const value = {
    agent_id: "agt_0123456789abcdef0123",
    agent_revision_id: null,
    memories: [],
    payload: { content: [{ type: "text", text: "Report" }] },
  };
  expect(
    scheduleInvocation(
      JSON.stringify(invocationFields(JSON.stringify(value)).draft),
    ),
  ).toEqual(value);
});
