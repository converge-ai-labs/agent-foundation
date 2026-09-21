import { compareCursors } from "../projection";
import type { LogEntry, PreviewRun } from "./model";
import type { PreviewStore } from "./store";

export const TERMINAL_EVENTS = [
  "run.completed",
  "run.failed",
  "run.cancelled",
  "run.waiting",
];

function frame(entry: LogEntry): string {
  return `id: ${entry.cursor}\nevent: ${entry.event.event_type}\ndata: ${JSON.stringify(entry.event)}\n\n`;
}

/**
 * One Run event stream. Retained frames replay immediately; a Run that is still
 * being simulated keeps the connection open until it reaches a terminal event.
 */
export function runStreamResponse(
  store: PreviewStore,
  run: PreviewRun,
  lastEventId: string | null,
  signal: AbortSignal,
): Response {
  const encoder = new TextEncoder();
  let sent = 0;
  let unsubscribe: (() => void) | undefined;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      let closed = false;
      const finish = () => {
        if (closed) return;
        closed = true;
        unsubscribe?.();
        signal.removeEventListener("abort", finish);
        try {
          controller.close();
        } catch {
          /* The consumer already released the stream. */
        }
      };
      const flush = () => {
        if (closed) return;
        while (sent < run.log.length) {
          const entry = run.log[sent++]!;
          if (lastEventId && compareCursors(entry.cursor, lastEventId) <= 0)
            continue;
          controller.enqueue(encoder.encode(frame(entry)));
          if (TERMINAL_EVENTS.includes(entry.event.event_type)) {
            finish();
            return;
          }
        }
        // A Run with nothing left to deliver has said everything it can.
        if (!run.script?.length) finish();
      };
      signal.addEventListener("abort", finish, { once: true });
      unsubscribe = store.subscribe(run.run.id, flush);
      flush();
    },
    cancel() {
      unsubscribe?.();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-store",
      "X-Request-ID": `req_preview_stream_${run.run.id}`,
    },
  });
}
