import type { Schema } from "../../shared/api";
import { resultExcerpt } from "./format";
import type { EventEntry } from "./timeline";

/**
 * Which lifecycle facts earn a row, and what each one says. Accepting a Run
 * and starting its first attempt happen to every Run: the Details card keeps
 * the complete lifecycle, and the timeline shows only what changes how the Run
 * reads. This decision is pure so both disclosure levels agree on it.
 */
export type LifecycleNotice =
  | { kind: "attempt"; tone: Tone; attempt: number; reason: string | null }
  | { kind: "recovery"; tone: Tone; reason: string | null }
  | {
      kind: "retry";
      tone: Tone;
      attempt: number | null;
      maxAttempts: number | null;
      delaySeconds: number | null;
    }
  | {
      kind: "outcome";
      tone: Tone;
      status: "failed" | "cancelled" | "waiting";
      /** Who owes the Run an answer; only a waiting outcome has one. */
      waitingOn: WaitingAudience | null;
      reason: string | null;
      message: string | null;
    };

type Tone = "neutral" | "warning" | "danger";

/**
 * A client tool result is owed by the application that holds the tool; every
 * other wait — approval, user input, or a mixed batch containing one — is
 * something the reader can answer here.
 */
export type WaitingAudience = "reader" | "application";

const APPLICATION_WAITS = ["client_tool"];

/** Attempt facts that start an attempt; the wording is never "leased". */
const ATTEMPT_STARTS = ["leased", "started"];

export function lifecycleNotice(entry: EventEntry): LifecycleNotice | null {
  if (entry.type === "model_retry_scheduled")
    return {
      kind: "retry",
      tone: "warning",
      attempt: entry.attempt,
      maxAttempts: entry.maxAttempts,
      delaySeconds: entry.delaySeconds,
    };
  if (entry.type === "run.recovery")
    return { kind: "recovery", tone: "warning", reason: entry.code };
  const [resource, action = ""] = entry.type.split(".");
  if (resource === "run_attempt") {
    const attempt = entry.attempt ?? 1;
    // The first attempt is how every Run starts; a later one explains itself.
    return ATTEMPT_STARTS.includes(action) && attempt > 1
      ? { kind: "attempt", tone: "neutral", attempt, reason: entry.code }
      : null;
  }
  if (resource !== "run") return null;
  if (action === "failed")
    return outcome("failed", "danger", entry.code, entry.message);
  if (action === "cancelled")
    return outcome("cancelled", "warning", entry.code, entry.message);
  if (action === "waiting")
    return outcome("waiting", "warning", entry.code, entry.message);
  return null;
}

function outcome(
  status: "failed" | "cancelled" | "waiting",
  tone: Tone,
  reason: string | null,
  message: string | null,
): LifecycleNotice {
  const waitingOn: WaitingAudience | null =
    status !== "waiting"
      ? null
      : reason && APPLICATION_WAITS.includes(reason)
        ? "application"
        : "reader";
  return { kind: "outcome", tone, status, waitingOn, reason, message };
}

const TERMINAL_TYPES: Record<string, string | undefined> = {
  failed: "run.failed",
  cancelled: "run.cancelled",
  waiting: "run.waiting",
};

/**
 * The Run's own terminal fact, in the timeline's row language. It is read from
 * the Run rather than from the stream, so a Run whose events were never
 * replayed still says how it ended, and the stream's copy never repeats it.
 */
export function runOutcome(run: Schema["RunResource"]): EventEntry | null {
  const type = TERMINAL_TYPES[run.status];
  if (!type) return null;
  const waiting = run.status === "waiting";
  const failure = run.failure as { code?: unknown; message?: unknown } | null;
  const code = typeof failure?.code === "string" ? failure.code : null;
  const message =
    typeof failure?.message === "string"
      ? failure.message
      : run.failure != null
        ? resultExcerpt(run.failure, 160)
        : null;
  const at = run.completed_at ?? run.waiting_at ?? run.updated_at;
  return {
    kind: "event",
    id: `${run.id}:outcome`,
    type,
    code: waiting ? run.wait_reason : code,
    message: waiting ? null : message,
    attempt: null,
    maxAttempts: null,
    delaySeconds: null,
    occurredAt: at,
    startedAt: at,
    endedAt: at,
    durationMs: null,
    state: "observed",
  };
}
