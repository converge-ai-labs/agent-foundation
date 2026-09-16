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
  result(
    runId: string,
    name: string | undefined,
    args: unknown,
    content: unknown,
  ) {
    if (
      !name ||
      !["shell_exec", "shell_start", "shell_wait", "shell_info"].includes(name)
    )
      return;
    const value = parsed(content);
    if (!object(value) || typeof value.process_id !== "string") return;
    const item = this.observe(runId, value.process_id);
    const input = parsed(args);
    if (
      (name === "shell_exec" || name === "shell_start") &&
      object(input) &&
      typeof input.command === "string"
    )
      item.command = input.command.replace(/\s+/g, " ").trim().slice(0, 500);
    if (!object(value.status) || typeof value.status.phase !== "string") return;
    if (value.status.phase === "running") item.background = true;
    this.status(
      runId,
      value.process_id,
      value.status.phase,
      value.status.exit_code,
    );
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
