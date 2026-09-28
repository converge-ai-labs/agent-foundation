import type { Schema } from "../../shared/api";
import type { ModelRetry } from "./execution";
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
    };

type Tone = "neutral" | "warning" | "danger";

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
    { kind: "outcome", tone, status, reason, message },
    run.sealed_at ?? run.updated_at,
  );
}
