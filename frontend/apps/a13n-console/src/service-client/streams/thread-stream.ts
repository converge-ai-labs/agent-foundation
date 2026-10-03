import type { ItemChange } from "a13n-ui/display";
import { ApiError, isRecord, ProtocolError } from "../errors.js";
import type { components } from "../schema.js";
import { delay, workspaceHeaders, type Transport } from "../transport.js";
import { decodeSse } from "./sse.js";

/** One server-folded compact display batch at its per-attempt sequence. */
export interface ThreadDelta {
  run_id: string;
  attempt: number;
  sequence: number;
  changes: ItemChange<components["schemas"]["Item"]>[];
}

/**
 * Frames of one thread stream. `delta` and `boundary` carry the resumable
 * cursor; `changed` means the thread snapshot is stale, `reset` that a run
 * changed attempt and `gap` that deltas were skipped, so its items need a
 * fresh read.
 */
export type ThreadFrame =
  | { type: "delta"; cursor: string; delta: ThreadDelta }
  | {
      type: "boundary";
      cursor: string;
      run_id: string;
      attempt: number;
      sequence: number;
    }
  | { type: "changed"; version: number }
  | { type: "reset"; run_id: string }
  | { type: "gap"; run_id: string; position?: string | null };

export interface ThreadResume {
  run: string;
  position: string;
  after?: string;
}

export interface ThreadStreamOptions {
  signal?: AbortSignal;
  after?: string;
  /** Read again for every connection attempt, after the consumer has applied frames. */
  resume?: ThreadResume | (() => ThreadResume | undefined);
}

function json(text: string): Record<string, unknown> {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new ProtocolError("Invalid thread stream frame JSON.");
  }
  if (!isRecord(value)) throw new ProtocolError("Invalid thread stream frame.");
  return value;
}

const isCount = (value: unknown): value is number =>
  Number.isInteger(value) && (value as number) >= 0;

const isPosition = (value: unknown) =>
  typeof value === "string" && /^(0|[1-9]\d*)-(0|[1-9]\d*)$/.test(value);
const isState = (value: unknown) =>
  ["in_progress", "completed", "interrupted", "failed"].includes(String(value));

function isItemChange(value: unknown): boolean {
  if (!isRecord(value)) return false;
  if (value.type === "append")
    return (
      typeof value.id === "string" &&
      (value.field === "text" || value.field === "arguments") &&
      typeof value.text === "string" &&
      isPosition(value.last_stream_id) &&
      isState(value.state) &&
      (value.ended_at === null || typeof value.ended_at === "string")
    );
  if (value.type !== "set" || !isRecord(value.item)) return false;
  const item = value.item;
  return (
    typeof item.id === "string" &&
    isCount(item.ordinal) &&
    item.ordinal > 0 &&
    ["text_message", "reasoning_message", "tool_call", "observation"].includes(
      String(item.kind),
    ) &&
    isState(item.state) &&
    isPosition(item.first_stream_id) &&
    isPosition(item.last_stream_id) &&
    typeof item.started_at === "string" &&
    (item.ended_at == null || typeof item.ended_at === "string") &&
    isRecord(item.content)
  );
}

function parseFrame(event: string, id: string, text: string): ThreadFrame {
  const data = json(text);
  const runId = typeof data.run_id === "string" ? data.run_id : undefined;
  if (event === "changed" && isCount(data.version))
    return { type: "changed", version: data.version };
  if (event === "reset" && runId) return { type: event, run_id: runId };
  if (event === "gap" && runId) {
    if (
      data.position != null &&
      (typeof data.position !== "string" ||
        !/^(0|[1-9]\d*)-(0|[1-9]\d*)$/.test(data.position))
    )
      throw new ProtocolError("Invalid gap position.");
    return {
      type: event,
      run_id: runId,
      ...(data.position === undefined
        ? {}
        : { position: data.position as string | null }),
    };
  }
  if (
    (event === "delta" || event === "boundary") &&
    id &&
    runId &&
    isCount(data.attempt) &&
    isCount(data.sequence)
  ) {
    if (event === "boundary")
      return {
        type: "boundary",
        cursor: id,
        run_id: runId,
        attempt: data.attempt,
        sequence: data.sequence,
      };
    if (Array.isArray(data.changes) && data.changes.every(isItemChange))
      return {
        type: "delta",
        cursor: id,
        // Only the Host interprets native events; clients apply compact changes.
        delta: data as unknown as ThreadDelta,
      };
  }
  throw new ProtocolError(`Invalid thread stream ${event} frame.`);
}

/** Follow live output using consumer coverage when supplied, or the last yielded cursor otherwise. */
export async function* threadStream(
  transport: Transport,
  workspaceId: string,
  threadId: string,
  options: ThreadStreamOptions = {},
): AsyncGenerator<ThreadFrame> {
  const signal = options.signal
    ? AbortSignal.any([options.signal, transport.signal])
    : transport.signal;
  let cursor = options.after;
  for (let attempt = 0; ; attempt++) {
    signal.throwIfAborted();
    const headers = new Headers({
      Accept: "text/event-stream",
      ...workspaceHeaders(workspaceId),
    });
    const resume =
      typeof options.resume === "function" ? options.resume() : options.resume;
    const after = options.resume ? resume?.after : cursor;
    if (after) headers.set("Last-Event-ID", after);
    const url = new URL(
      `${transport.baseUrl}/api/v1/threads/${encodeURIComponent(threadId)}/stream`,
    );
    if (resume) {
      url.searchParams.set("run", resume.run);
      url.searchParams.set("position", resume.position);
    }
    try {
      const response = await transport.fetch(
        new Request(url, { headers, signal }),
      );
      if (
        !response.headers
          .get("Content-Type")
          ?.startsWith("text/event-stream") ||
        !response.body
      ) {
        await response.body?.cancel();
        throw new ProtocolError("Expected a thread event stream.");
      }
      for await (const sse of decodeSse(response.body)) {
        const frame = parseFrame(sse.event, sse.id, sse.data);
        yield frame;
        attempt = 0;
        // Resume advances only when the consumer requests the next frame.
        if ("cursor" in frame) cursor = frame.cursor;
      }
      return;
    } catch (error) {
      if (
        signal.aborted ||
        error instanceof ApiError ||
        error instanceof ProtocolError ||
        attempt >= 2
      )
        throw error;
      await delay(250 * 2 ** attempt, signal);
    }
  }
}
