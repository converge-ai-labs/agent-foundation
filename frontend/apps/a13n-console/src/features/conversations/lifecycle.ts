import type { Schema } from "../../shared/api";
import type { ExecutionObservation, ModelRetry } from "./execution";
import { isRecord } from "../../service-client";
import { resultExcerpt } from "./format";
import type { EventEntry } from "./timeline";

/**
 * Which lifecycle facts earn a row, and what each one says. Accepting a Run
 * and starting its first attempt happen to every Run: the Details card keeps
 * the complete lifecycle, and the timeline shows only what changes how the Run
 * reads. This decision is pure so both disclosure levels agree on it.
 */
export type LifecycleNotice =
  | {
      kind: "context-recovery";
      tone: "warning";
      message: string;
      outcome: string;
    }
  | {
      kind: "attempt";
      tone: Tone;
      attempt: number;
      reason: Schema["AttemptView"]["start_reason"];
    }
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
      reason: string | null;
      message: string | null;
      /** What a waiting Run asks of the reader; null when its calls differ. */
      wait: WaitKind | null;
    };

type Tone = "neutral" | "warning" | "danger";

/**
 * What a wait asks of the reader: to approve a call, to answer a question, or
 * to return a tool's result. Every surface names a wait by its kind, so one
 * pending call never reads three different ways.
 */
export type WaitKind = "approval" | "question" | "result";

/** The one tool whose call asks the reader a question. */
export const QUESTION_TOOL = "ask_user_question";

/** A pending call returns an answer to a question or a tool's result. */
export function callWait(toolName: string | null | undefined): WaitKind {
  return toolName === QUESTION_TOOL ? "question" : "result";
}

/** One kind when every call the Run waits on asks the same thing. */
function runWait(run: Schema["RunView"]): WaitKind | null {
  const kinds = new Set<WaitKind>([
    ...(run.pending?.approvals ?? []).map(() => "approval" as const),
    ...(run.pending?.calls ?? []).map((call) => callWait(call.tool_name)),
  ]);
  if (kinds.size === 1) return [...kinds][0]!;
  return !kinds.size && run.wait_reason === "approval" ? "approval" : null;
}

function eventEntry(
  id: string,
  notice: LifecycleNotice,
  occurredAt: string | null,
): EventEntry {
  return {
    kind: "event",
    id,
    notice,
    occurredAt,
    startedAt: occurredAt,
    endedAt: occurredAt,
    durationMs: null,
    state: "observed",
  };
}

/** The first attempt is how every Run starts; a later one explains itself. */
export function attemptEvent(
  attempt: Schema["AttemptView"],
): EventEntry | null {
  if (attempt.number < 2) return null;
  return eventEntry(
    attempt.id,
    {
      kind: "attempt",
      tone: "neutral",
      attempt: attempt.number,
      reason: attempt.start_reason,
    },
    attempt.started_at ?? attempt.created_at,
  );
}

export function contextRecoveryEvent(
  observation: ExecutionObservation,
): EventEntry | null {
  if (observation.name !== "context_recovery" || !isRecord(observation.detail))
    return null;
  const payload = observation.detail.payload;
  if (!isRecord(payload) || typeof payload.warning !== "string") return null;
  return eventEntry(
    observation.id,
    {
      kind: "context-recovery",
      tone: "warning",
      message: payload.warning,
      outcome:
        typeof payload.outcome === "string" ? payload.outcome : "unknown",
    },
    observation.occurredAt,
  );
}

export function retryEvent(retry: ModelRetry): EventEntry {
  return eventEntry(
    retry.id,
    {
      kind: "retry",
      tone: "warning",
      attempt: retry.attempt,
      maxAttempts: retry.maxAttempts,
      delaySeconds: retry.delaySeconds,
    },
    retry.occurredAt,
  );
}

const OUTCOME_TONES: Record<string, Tone | undefined> = {
  failed: "danger",
  cancelled: "warning",
  waiting: "warning",
};

/**
 * The Run's own terminal fact, in the timeline's row language. It is read from
 * the Run rather than from its display, so a Run whose display was never read
 * still says how it ended.
 */
export function runOutcome(run: Schema["RunView"]): EventEntry | null {
  const tone = OUTCOME_TONES[run.status];
  if (!tone) return null;
  const status = run.status as "failed" | "cancelled" | "waiting";
  const failure = run.failure as { code?: unknown; message?: unknown } | null;
  const reason =
    status === "waiting"
      ? run.wait_reason
      : typeof failure?.code === "string"
        ? failure.code
        : null;
  const message =
    status === "waiting"
      ? null
      : typeof failure?.message === "string"
        ? failure.message
        : run.failure != null
          ? resultExcerpt(run.failure, 160)
          : null;
  return eventEntry(
    `${run.id}:outcome`,
    {
      kind: "outcome",
      tone,
      status,
      reason,
      message,
      wait: status === "waiting" ? runWait(run) : null,
    },
    run.sealed_at ?? run.updated_at,
  );
}
