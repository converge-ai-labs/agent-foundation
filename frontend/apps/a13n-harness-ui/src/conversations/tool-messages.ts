import type { TFunction } from "i18next";

// Tool labels, phases and generated targets are application-owned display
// identifiers produced by describeTool; they carry no message codes. Translate
// only these known values. Tool names, paths, commands, queries, identifiers,
// process phases and server diagnostics stay verbatim, including a label that
// falls back to the recorded tool name.
export const toolMessages = new Set([
  "Tool",
  "Observe desktop",
  "Inspect desktop",
  "Inspect desktop region",
  "Desktop · click",
  "Desktop · move",
  "Desktop · drag",
  "Desktop · scroll",
  "Desktop · type text",
  "Desktop · press keys",
  "Read",
  "Write",
  "Append",
  "Edit",
  "List",
  "Find files",
  "Search",
  "Inspect tool execution",
  "List tool executions",
  "Retrieve tool result",
  "Wait for tool execution",
  "Cancel tool execution",
  "Run",
  "Read process output",
  "List processes",
  "Inspect process",
  "Send process input",
  "Signal process",
  "Search the web",
  "Read webpage",
  "Find on webpage",
  "Download",
  "Find chats",
  "Inspect chat",
  "Find projects",
  "Find agents",
  "Find models",
  "Inspect project",
  "Start chat",
  "Continue chat",
  "Send instruction",
  "Message chat",
  "Run program",
  "Run code",
  "Create task",
  "Update task",
  "Inspect task",
  "List tasks",
  "Update note",
  "Delete note",
  "Read notes",
  "Delegate",
  "Inspect subagent",
  "Wait for subagents",
  "Guide subagent",
  "Stop subagent",
  "Resume subagent",
  "Receiving input",
  "Awaiting result",
  "Waiting",
  "Result received",
  "No result recorded",
  "Retry requested",
  "Interrupted",
  "Denied",
  "Failed",
  "Unavailable",
  "Cancellation requested",
  "Execution handed off",
  "Not accepted",
  "Run accepted",
  "Instruction accepted",
  "Message accepted · new turn",
  "Message accepted · instruction",
  "Queued at last check",
  "Running at last check",
  "Succeeded at last check",
  "Failed at last check",
  "Cancelled at last check",
  "Lost at last check",
  "Exited at last check",
  "Signaled at last check",
  "Timed out at last check",
  "All projects",
  "Current chat",
  "Configured resources",
]);

/** Translate a known tool descriptor; unknown text is returned unchanged. */
export function toolMessage(message: string, t: TFunction): string {
  if (toolMessages.has(message)) return t(message);
  // The only generated descriptor carrying a count; other observation phases
  // are exact values listed above.
  const listed = /^(\d+) executions? listed$/.exec(message);
  if (listed) {
    const count = Number(listed[1]);
    if (Number.isSafeInteger(count))
      return t("{{count, number}} executions listed", { count });
  }
  return message;
}

/**
 * Translate a tool label. A label that repeats the recorded tool name is that
 * identifier rather than a display value, so it stays verbatim; the
 * application's own placeholder for an unnamed call remains translatable.
 */
export function toolLabel(label: string, name: string, t: TFunction): string {
  if (label === name && label !== "Tool") return label;
  return toolMessage(label, t);
}
