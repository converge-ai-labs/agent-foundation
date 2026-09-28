import { ApiError, isRecord, ProtocolError } from "../errors.js";
import type { components } from "../schema.js";
import { delay, workspaceHeaders, type Transport } from "../transport.js";
import { decodeSse } from "./sse.js";

type ItemRef = Pick<components["schemas"]["Item"], "id" | "kind" | "state">;

/** One AG-UI event of a run attempt at its per-attempt sequence, and the display item it changed. */
export interface ThreadDelta {
  run_id: string;
  attempt: number;
  sequence: number;
  event: Record<string, unknown> & { type: string };
  item: ItemRef | null;
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
  | { type: "gap"; run_id: string };

export interface ThreadStreamOptions {
  signal?: AbortSignal;
  after?: string;
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

function parseFrame(event: string, id: string, text: string): ThreadFrame {
  const data = json(text);
  const runId = typeof data.run_id === "string" ? data.run_id : undefined;
  if (event === "changed" && isCount(data.version))
    return { type: "changed", version: data.version };
  if ((event === "reset" || event === "gap") && runId)
    return { type: event, run_id: runId };
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
    if (
      isRecord(data.event) &&
      typeof data.event.type === "string" &&
      (data.item === null || isRecord(data.item))
    )
      return {
        type: "delta",
        cursor: id,
        // The envelope is checked here; event payloads belong to their consumers.
        delta: data as unknown as ThreadDelta,
      };
  }
  throw new ProtocolError(`Invalid thread stream ${event} frame.`);
}

/** Follow one thread's live output, resuming after the last delta or boundary it yielded. */
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
    if (cursor) headers.set("Last-Event-ID", cursor);
    try {
      const response = await transport.fetch(
        new Request(
          `${transport.baseUrl}/api/v1/threads/${encodeURIComponent(threadId)}/stream`,
          { headers, signal },
        ),
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
