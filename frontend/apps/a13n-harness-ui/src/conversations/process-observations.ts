export type ProcessObservation = {
  runId: string;
  processId: string;
  command: string;
  phase: string;
  exitCode?: number;
  background: boolean;
};
const terminal = new Set([
  "exited",
  "signaled",
  "timed_out",
  "cancelled",
  "failed",
]);
function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function parsed(value: unknown): unknown {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return undefined;
  }
}

// Presentation only, keyed by Run-local handles, never an OS process inventory.
export class ProcessObservations {
  readonly entries = new Map<string, ProcessObservation>();
  omitted = false;
  private observe(runId: string, processId: string) {
    const key = `${runId}:${processId}`;
    let item = this.entries.get(key);
    if (!item) {
      item = {
        runId,
        processId,
        command: "",
        phase: "unavailable",
        background: false,
      };
      this.entries.set(key, item);
      if (this.entries.size > 128) {
        this.entries.delete(this.entries.keys().next().value!);
        this.omitted = true;
      }
    }
    return item;
  }
  command(runId: string, name: string, args: unknown): string | undefined {
    if (
      !name.startsWith("shell_") &&
      !["tool_execution_result", "tool_execution_wait"].includes(name)
    )
      return;
    const input = parsed(args);
    if (!object(input)) return;
    if (["shell_exec", "shell_start"].includes(name))
      return typeof input.command === "string" ? input.command : undefined;
    const reference = name.startsWith("shell_")
      ? input.process_id
      : ["tool_execution_result", "tool_execution_wait"].includes(name)
        ? input.execution_id
        : undefined;
    if (typeof reference !== "string") return;
    return this.entries.get(`${runId}:${reference}`)?.command || undefined;
  }
  result(
    runId: string,
    name: string | undefined,
    args: unknown,
    content: unknown,
  ) {
    if (
      !name ||
      ![
        "shell_exec",
        "shell_start",
        "shell_wait",
        "shell_info",
        "tool_execution_result",
        "tool_execution_wait",
      ].includes(name)
    )
      return;
    const input = parsed(args);
    const retrievesShell =
      (name === "tool_execution_result" || name === "tool_execution_wait") &&
      object(input) &&
      typeof input.execution_id === "string" &&
      this.entries.has(`${runId}:${input.execution_id}`);
    if (
      !retrievesShell &&
      ["tool_execution_result", "tool_execution_wait"].includes(name)
    )
      return;
    const value = parsed(content);
    if (!object(value)) return;
    const startsProcess = name === "shell_exec" || name === "shell_start";
    // Host-owned starts return an execution receipt; later Shell observations
    // use that same execution ID as their process handle.
    const processId = retrievesShell
      ? input.execution_id
      : startsProcess &&
          value.type === "tool_execution_receipt" &&
          value.tool_name === "shell_exec"
        ? value.execution_id
        : value.process_id;
    if (typeof processId !== "string") return;
    const item = this.observe(runId, processId);
    if (startsProcess && object(input) && typeof input.command === "string")
      item.command = input.command.replace(/\s+/g, " ").trim().slice(0, 500);
    if (!object(value.status) || typeof value.status.phase !== "string") return;
    if (value.status.phase === "running") item.background = true;
    this.status(runId, processId, value.status.phase, value.status.exit_code);
  }
  status(runId: string, processId: string, phase: string, exitCode: unknown) {
    const item = this.observe(runId, processId);
    // Completion callbacks can overtake the earlier tool-return snapshot.
    if (terminal.has(item.phase) && !terminal.has(phase)) return;
    item.phase = phase;
    if (typeof exitCode === "number" && Number.isInteger(exitCode))
      item.exitCode = exitCode;
  }
  end(runId?: string) {
    for (const item of this.entries.values())
      if ((!runId || item.runId === runId) && item.phase === "running")
        item.phase = "unavailable";
  }
  clear() {
    this.entries.clear();
    this.omitted = false;
  }
  get background() {
    return [...this.entries.values()].filter((item) => item.background);
  }
}
