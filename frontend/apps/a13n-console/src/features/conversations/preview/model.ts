import type { Schema } from "../../../shared/api";

/** One Run stream frame: its SSE cursor, its delay, and the event itself. */
export interface LogEntry {
  cursor: string;
  /** Milliseconds after the Run started, for scheduled live emission. */
  offsetMs: number;
  event: Schema["RunStreamEvent"];
}

/** One committed lifecycle fact, shared by the Run stream and the events page. */
export interface LifecycleFact {
  eventType: string;
  entityType: Schema["LifecycleEntityType"];
  occurredAt: string;
  attemptId: string | null;
  harnessRunId: string | null;
  data: Record<string, unknown>;
}

export interface PreviewRun {
  run: Schema["RunResource"];
  /** Committed lifecycle facts; Attempts and the events page derive from them. */
  facts: LifecycleFact[];
  attempts: Schema["RunAttemptResource"][];
  pending: Schema["PendingActionResource"][];
  lifecycle: Schema["LifecycleEvent"][];
  mounts: Schema["RunEnvironmentMount"][];
  log: LogEntry[];
  /** Frames still to be appended while a Run is simulated live. */
  script?: LogEntry[];
}

export interface PreviewThread {
  thread: Schema["ThreadResource"];
  runs: PreviewRun[];
  queue: Schema["QueuedSubmission"][];
}

export interface PreviewSession {
  session: Schema["SessionResource"];
  threads: PreviewThread[];
}

export interface PreviewTrace {
  trace: Schema["Trace"];
  observations: Schema["Observation"][];
}

export interface PreviewScenario {
  user: Schema["User"];
  organization: Schema["Organization"];
  workspace: Schema["Workspace"];
  /** Workspace actions; `notification.subscribe` is deliberately absent. */
  permissions: string[];
  agents: Schema["Agent"][];
  environments: Schema["Environment"][];
  assets: Schema["Asset"][];
  sessions: PreviewSession[];
  traces: PreviewTrace[];
  /** Where `/preview` lands. */
  entry: { sessionId: string; threadId: string; runId: string };
}

/**
 * Run stream cursors are `<milliseconds>-<ordinal>` and strictly increasing.
 * One function serves scripted logs and live appends so both agree.
 */
export function nextCursor(
  previous: string | undefined,
  occurredAt: string,
): string {
  const time = Date.parse(occurredAt);
  if (!previous) return `${time}-0`;
  const [milliseconds = "0", ordinal = "0"] = previous.split("-");
  return time > Number(milliseconds)
    ? `${time}-0`
    : `${milliseconds}-${Number(ordinal) + 1}`;
}
