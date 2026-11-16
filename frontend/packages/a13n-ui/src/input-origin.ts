export type InputOrigin = {
  kind: "message" | "agent_message" | "result" | "event";
  sender: {
    kind: "user" | "agent" | "system" | "connector";
    thread_id?: string;
    name?: string;
  };
  execution?: {
    execution_id: string;
    kind: "agent" | "tool";
    name: string;
    state: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "lost";
  };
};

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function member<T extends string>(
  value: unknown,
  values: readonly T[],
): value is T {
  return typeof value === "string" && values.includes(value as T);
}
const unknownOrigin = (): InputOrigin => ({
  kind: "event",
  sender: { kind: "system", name: "unknown" },
});

/** Read content provenance, including stored history. Invalid markers are never user input. */
export function inputOrigin(metadata: unknown): InputOrigin | null {
  if (!record(metadata)) return null;
  if ("a13n.input" in metadata) {
    const marker = metadata["a13n.input"];
    if (
      !record(marker) ||
      !member(marker.kind, ["message", "agent_message", "result", "event"]) ||
      !record(marker.sender)
    )
      return unknownOrigin();
    const sender = marker.sender;
    if (
      !member(sender.kind, ["user", "agent", "system", "connector"]) ||
      (sender.thread_id != null && typeof sender.thread_id !== "string") ||
      (sender.name != null && typeof sender.name !== "string") ||
      (marker.id != null && typeof marker.id !== "string")
    )
      return unknownOrigin();
    const origin: InputOrigin = {
      kind: marker.kind,
      sender: {
        kind: sender.kind,
        ...(typeof sender.thread_id === "string"
          ? { thread_id: sender.thread_id }
          : {}),
        ...(typeof sender.name === "string" ? { name: sender.name } : {}),
      },
    };
    if (marker.execution != null) {
      const execution = marker.execution;
      if (
        !record(execution) ||
        typeof execution.execution_id !== "string" ||
        !member(execution.kind, ["agent", "tool"]) ||
        typeof execution.name !== "string" ||
        !member(execution.state, [
          "queued",
          "running",
          "succeeded",
          "failed",
          "cancelled",
          "lost",
        ])
      )
        return unknownOrigin();
      origin.execution = {
        execution_id: execution.execution_id,
        kind: execution.kind,
        name: execution.name,
        state: execution.state,
      };
    }
    return origin;
  }
  const source = metadata["a13n.steering-source"];
  if (source === "async_subagent" || source === "tool_execution")
    return {
      kind: "result",
      sender: {
        kind: source === "async_subagent" ? "agent" : "system",
        name: source,
      },
    };
  if (source === "parent_agent")
    return { kind: "agent_message", sender: { kind: "agent", name: "parent" } };
  if (source === "background_process" || source === "envd_session")
    return { kind: "event", sender: { kind: "system", name: source } };
  const message = record(metadata.harness_ui)
    ? metadata.harness_ui.thread_message
    : undefined;
  if (record(message))
    return {
      kind: "agent_message",
      sender: {
        kind: "agent",
        ...(typeof message.source_thread_id === "string"
          ? { thread_id: message.source_thread_id }
          : {}),
        ...(typeof message.source_thread_title === "string"
          ? { name: message.source_thread_title }
          : {}),
      },
    };
  if (metadata.source_id === "a13n-harness-ui.thread-lifecycle")
    return {
      kind: "event",
      sender: { kind: "system", name: "thread-lifecycle" },
    };
  return null;
}
