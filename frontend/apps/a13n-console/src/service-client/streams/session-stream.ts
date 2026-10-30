import { EventSchema } from "@ag-ui/core/schemas";
import {
  isDisplayItem,
  isItemRef,
  type DisplayContinuation,
  type DisplayEvent,
  type DisplayItem,
} from "a13n-ui/display";
import { ApiError, isRecord, ProtocolError } from "../errors.js";
import type { components } from "../schema.js";
import { delay, workspaceHeaders, type Transport } from "../transport.js";
import { decodeSse } from "./sse.js";

type Schemas = components["schemas"];

/** A thread's current state: the thread, the runs its pointers name and its open inbox entries. */
export interface ThreadState {
  thread: Schemas["ThreadView"];
  current_run: Schemas["RunView"] | null;
  last_run: Schemas["RunView"] | null;
  /** Pending and assigned entries, in inbox order. */
  entries: Schemas["EntryView"][];
}

/**
 * A run's committed items from ordinal `first` on. The receiver replaces the
 * items it holds from `first` on; earlier items are final.
 */
export interface RunSnapshot {
  thread_id: string;
  run_id: string;
  first: number;
  items: DisplayItem[];
  continuation: DisplayContinuation | null;
  /** The run's final display. */
  complete: boolean;
}

/** One raw AG-UI event of a run, at its producer-assigned position. */
export type SessionDelta = DisplayEvent & { thread_id: string };

/** A run's output was lost: its deltas pause until its next `items`. */
export interface SessionGap {
  thread_id: string;
  run_id: string;
}

/** Compact plan state; input and saved execution authority are never in the stream. */
export interface ScheduleEvent {
  session_id: string;
  schedule_id: string;
  configuration: Pick<
    Schemas["Schedule"],
    | "id"
    | "session_id"
    | "name"
    | "state"
    | "pause_reason"
    | "version"
    | "principal_id"
    | "schedule"
    | "max_lateness_seconds"
  >;
  progress: Pick<
    Schemas["ScheduleStatus"],
    "next_fire_at" | "pending_occurrence_id" | "last_occurrence_id"
  >;
}

export interface OccurrenceEvent {
  session_id: string;
  schedule_id: string;
  occurrence: Schemas["OccurrenceSummary"];
}

export type SessionFrame =
  | { type: "thread"; data: ThreadState }
  | { type: "items"; data: RunSnapshot }
  | { type: "delta"; data: SessionDelta }
  | { type: "gap"; data: SessionGap }
  | { type: "schedule"; data: ScheduleEvent }
  | { type: "occurrence"; data: OccurrenceEvent };

/**
 * The connection's state, in order with its frames: `live` precedes the
 * snapshot every connection starts with.
 */
export type SessionConnection = {
  type: "connection";
  state: "connecting" | "live" | "reconnecting";
  error?: unknown;
};

export type SessionEvent = SessionFrame | SessionConnection;

export interface SessionStreamOptions {
  signal?: AbortSignal;
}

/** Reconnect delays grow to this bound; a connection open this long resets them. */
const MAX_BACKOFF_MS = 30_000;
const BASE_BACKOFF_MS = 500;

const isCount = (value: unknown): value is number =>
  Number.isInteger(value) && (value as number) >= 0;
const isId = (value: unknown): value is string =>
  typeof value === "string" && value.length > 0;

function json(text: string): Record<string, unknown> {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new ProtocolError("Invalid session stream frame JSON.");
  }
  if (!isRecord(value))
    throw new ProtocolError("Invalid session stream frame.");
  return value;
}

/** A frame, or undefined for an event this client does not know. */
function parseFrame(event: string, text: string): SessionFrame | undefined {
  if (
    !["thread", "items", "delta", "gap", "schedule", "occurrence"].includes(
      event,
    )
  )
    return undefined;
  const data = json(text);
  if (event === "schedule" || event === "occurrence") {
    if (!isId(data.session_id) || !isId(data.schedule_id))
      throw new ProtocolError(`Invalid session stream ${event} frame.`);
    if (
      event === "schedule" &&
      isRecord(data.configuration) &&
      isId(data.configuration.id) &&
      isCount(data.configuration.version) &&
      isRecord(data.progress)
    )
      return { type: "schedule", data: data as unknown as ScheduleEvent };
    if (
      event === "occurrence" &&
      isRecord(data.occurrence) &&
      isId(data.occurrence.id) &&
      isCount(data.occurrence.version) &&
      [
        "pending",
        "submitted",
        "completed",
        "skipped",
        "failed",
        "cancelled",
      ].includes(String(data.occurrence.status))
    )
      return { type: "occurrence", data: data as unknown as OccurrenceEvent };
    throw new ProtocolError(`Invalid session stream ${event} frame.`);
  }
  if (
    event === "thread" &&
    isRecord(data.thread) &&
    isId(data.thread.id) &&
    (data.current_run === null || isRecord(data.current_run)) &&
    (data.last_run === null || isRecord(data.last_run)) &&
    Array.isArray(data.entries)
  )
    return { type: "thread", data: data as unknown as ThreadState };
  if (!isId(data.thread_id) || !isId(data.run_id))
    throw new ProtocolError(`Invalid session stream ${event} frame.`);
  if (event === "gap")
    return {
      type: "gap",
      data: { thread_id: data.thread_id, run_id: data.run_id },
    };
  if (
    event === "items" &&
    isCount(data.first) &&
    data.first > 0 &&
    Array.isArray(data.items) &&
    data.items.every(isDisplayItem) &&
    (data.continuation === null || isRecord(data.continuation)) &&
    typeof data.complete === "boolean"
  )
    return { type: "items", data: data as unknown as RunSnapshot };
  if (
    event === "delta" &&
    isCount(data.attempt) &&
    isCount(data.sequence) &&
    isRecord(data.event) &&
    // Validate AG-UI within the Host-owned position and identity envelope.
    EventSchema.safeParse(data.event).success &&
    (data.item === null || isItemRef(data.item))
  )
    return { type: "delta", data: data as unknown as SessionDelta };
  throw new ProtocolError(`Invalid session stream ${event} frame.`);
}

/** Full jitter, or the Service's own `Retry-After` when it is busy. */
function backoff(failures: number, error: unknown) {
  if (error instanceof ApiError && error.retryAfter !== null) {
    const seconds = Number(error.retryAfter);
    const milliseconds = Number.isFinite(seconds)
      ? seconds * 1000
      : Date.parse(error.retryAfter) - Date.now();
    if (milliseconds >= 0) return Math.min(milliseconds, MAX_BACKOFF_MS);
  }
  return (
    Math.random() *
    Math.min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * 2 ** Math.min(failures, 16))
  );
}

/**
 * Follow a session until the signal aborts. Every connection starts from a
 * snapshot, so a reconnect needs no cursor. Errors other than a refusal
 * (any 4xx except 429) or an invalid frame reconnect with backoff.
 */
export async function* sessionStream(
  transport: Transport,
  workspaceId: string,
  sessionId: string,
  options: SessionStreamOptions = {},
): AsyncGenerator<SessionEvent> {
  const signal = options.signal
    ? AbortSignal.any([options.signal, transport.signal])
    : transport.signal;
  const url = `${transport.baseUrl}/api/v1/sessions/${encodeURIComponent(sessionId)}/stream`;
  let failures = 0;
  let error: unknown;
  for (;;) {
    signal.throwIfAborted();
    yield {
      type: "connection",
      state: failures ? "reconnecting" : "connecting",
      ...(error === undefined ? {} : { error }),
    };
    let opened: number | undefined;
    try {
      const response = await transport.fetch(
        new Request(url, {
          headers: {
            Accept: "text/event-stream",
            ...workspaceHeaders(workspaceId),
          },
          signal,
        }),
      );
      if (
        !response.headers
          .get("Content-Type")
          ?.startsWith("text/event-stream") ||
        !response.body
      ) {
        await response.body?.cancel();
        throw new ProtocolError("Expected a session event stream.");
      }
      opened = Date.now();
      yield { type: "connection", state: "live" };
      for await (const sse of decodeSse(response.body)) {
        const frame = parseFrame(sse.event, sse.data);
        if (frame) yield frame;
      }
      error = new Error("The session stream ended.");
    } catch (caught) {
      if (
        signal.aborted ||
        caught instanceof ProtocolError ||
        (caught instanceof ApiError &&
          caught.status >= 400 &&
          caught.status < 500 &&
          caught.status !== 429)
      )
        throw caught;
      error = caught;
    }
    if (opened !== undefined && Date.now() - opened >= MAX_BACKOFF_MS)
      failures = 0;
    await delay(backoff(failures, error), signal);
    failures++;
  }
}
