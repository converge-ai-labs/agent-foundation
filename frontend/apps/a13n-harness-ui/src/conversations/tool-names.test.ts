import { expect, it } from "vitest";
import { describeTool, activityKind } from "./tool-presentation";
import { presentationTool } from "./tool-names";

it.each([
  ["tool_execution_info", "execution_info"],
  ["tool_execution_wait", "execution_wait"],
  ["tool_execution_cancel", "execution_cancel"],
  ["tool_execution_result", "execution_result"],
  ["subagent_info", "execution_info"],
  ["wait_subagent", "execution_wait"],
  ["cancel_subagent", "execution_cancel"],
])("renders stored %s through %s", (saved, current) => {
  const input = {
    execution_id: saved.startsWith("tool_") ? "exec-one" : "agent-one",
  };
  const tool = { id: "call", name: current, input };
  expect(describeTool({ ...tool, name: saved })).toEqual(describeTool(tool));
  expect(activityKind({ ...tool, name: saved })).toEqual(activityKind(tool));
});

it.each([
  [
    "steer_subagent",
    { execution_id: "agent-one", message: "Continue" },
    "agent-one",
  ],
  [
    "resume_subagent",
    { execution_id: "agent-one", prompt: "Continue" },
    "agent-one",
  ],
  [
    "send_thread_message",
    { thread_id: "thread-one", message: "Continue" },
    "thread-one",
  ],
  [
    "steer_thread",
    { thread_id: "thread-one", message: "Continue" },
    "thread-one",
  ],
  ["run_thread", { thread_id: "thread-one", prompt: "Continue" }, "thread-one"],
] as const)(
  "preserves the target and message of saved %s",
  (name, args, to) => {
    expect(presentationTool(name, args)).toMatchObject({
      name: "send_message",
      args: { to, message: "Continue" },
    });
    const info = describeTool({
      id: "call",
      name,
      input: args,
      result: { accepted: true, input_id: "input-one" },
    });
    expect(info.summary).toBe("Continue");
    expect(info.phase).toBe("Message accepted");
    expect(info.threadId).toBe(to.startsWith("thread") ? to : "");
  },
);

it("retains inline resume_subagent presentation and grouping", () => {
  const tool = {
    id: "inline",
    name: "resume_subagent",
    input: { subagent_id: "child-one", prompt: "Continue" },
  };
  expect(describeTool(tool)).toMatchObject({
    name: "resume_subagent",
    label: "Resume subagent",
    summary: "child-one",
  });
  expect(activityKind(tool)).toBe("subagents");
});

it.each(["parent", "bot", "live", "agent-one", "thread-one"])(
  "reports only acceptance for send_message to %s",
  (to) => {
    const info = describeTool({
      id: "send",
      name: "send_message",
      input: { to, message: "Hello" },
      result: { accepted: true, input_id: "input-one" },
    });
    expect(info.phase).toBe("Message accepted");
    expect(info.summary).toBe("Hello");
  },
);

it("reads the unified execution list and agent execution views", () => {
  expect(
    describeTool({
      id: "list",
      name: "execution_info",
      input: {},
      result: {
        agent_executions: {
          executions: [{}],
          execution_offset: 0,
          total: 1,
          next_offset: null,
        },
        tool_executions: [{}, {}],
      },
    }),
  ).toMatchObject({ label: "List executions", phase: "3 executions listed" });
  const result = {
    execution_id: "agent-one",
    subagent_name: "Reviewer",
    status: "succeeded",
    output: "Done",
  };
  expect(
    describeTool({
      id: "result",
      name: "execution_result",
      input: { execution_id: "agent-one" },
      result,
    }),
  ).toMatchObject({
    label: "Subagent result",
    phase: "Succeeded at last check",
    executionKind: "agent",
  });
  expect(
    describeTool({
      id: "wait",
      name: "execution_wait",
      input: { execution_id: "exec-one" },
      result: {
        execution_id: "exec-one",
        tool_name: "view",
        status: "running",
      },
    }),
  ).toMatchObject({
    label: "Wait for tool execution",
    phase: "Running at last check",
    executionKind: "tool",
  });
});

it.each([
  ["subagent_info", "Inspect subagent", "subagents"],
  ["wait_subagent", "Wait for subagents", "subagents"],
  ["tool_execution_info", "List tool executions", "executions"],
])(
  "preserves the known kind of saved %s without an individual ID",
  (name, label, kind) => {
    const tool = { id: "list", name, input: {} };
    expect(describeTool(tool).label).toBe(label);
    expect(activityKind(tool)).toBe(kind);
  },
);
