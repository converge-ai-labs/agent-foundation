// Presentation-only migration of stored tool calls. Never used for tool dispatch.
// Keep old rows' structured details, grouping and links on the current rendering path.
const savedNames: Record<
  string,
  {
    name: string;
    arguments?: Record<string, string>;
    executionKind?: "agent" | "tool";
  }
> = {
  tool_execution_info: { name: "execution_info", executionKind: "tool" },
  tool_execution_wait: { name: "execution_wait" },
  tool_execution_cancel: { name: "execution_cancel" },
  tool_execution_result: { name: "execution_result" },
  subagent_info: { name: "execution_info", executionKind: "agent" },
  wait_subagent: { name: "execution_wait", executionKind: "agent" },
  cancel_subagent: { name: "execution_cancel", executionKind: "agent" },
  steer_subagent: { name: "send_message", arguments: { execution_id: "to" } },
  resume_subagent: {
    name: "send_message",
    arguments: { execution_id: "to", prompt: "message" },
  },
  send_thread_message: { name: "send_message", arguments: { thread_id: "to" } },
  steer_thread: { name: "send_message", arguments: { thread_id: "to" } },
  run_thread: {
    name: "send_message",
    arguments: { thread_id: "to", prompt: "message" },
  },
};

export function presentationTool(name: string, args: Record<string, unknown>) {
  // Inline delegation is a synchronous continuation, not an agent Execution.
  const saved =
    name === "resume_subagent" && typeof args.execution_id !== "string"
      ? undefined
      : savedNames[name];
  if (!saved) return { name, args, executionKind: undefined };
  const input = { ...args };
  for (const [from, to] of Object.entries(saved.arguments ?? {})) {
    if (from in args) input[to] = args[from];
  }
  return { name: saved.name, args: input, executionKind: saved.executionKind };
}
