import { ApiError, type Schema, type Transport } from "./client";

type SummaryFrame =
  | (Schema<"SummaryOpenFrame"> & { kind: "open" })
  | (Schema<"SummaryEventFrame"> & { kind: "invalidation" })
  | (Schema<"ResetFrame"> & { kind: "reset" });

// A fetch stream keeps the credential in the header, never an EventSource URL.
export async function consumeSse(
  response: Response,
  receive: (data: unknown) => void,
) {
  if (!response.body) throw new Error("Streaming response is unavailable.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let data: string[] = [];
  try {
    while (true) {
      const next = await reader.read();
      if (next.done) break;
      buffer += decoder.decode(next.value, { stream: true });
      if (buffer.length > 1024 * 1024)
        throw new Error("Stream frame is too large.");
      let end: number;
      while ((end = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, end).replace(/\r$/, "");
        buffer = buffer.slice(end + 1);
        if (!line) {
          if (data.length) receive(JSON.parse(data.join("\n")));
          data = [];
        } else if (line.startsWith("data:"))
          data.push(line.slice(5).replace(/^ /, ""));
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export function summaryFrame(value: unknown): SummaryFrame {
  if (typeof value !== "object" || value === null || !("kind" in value))
    throw new Error("Invalid summary frame.");
  if (
    value.kind === "reset" &&
    "reason" in value &&
    typeof value.reason === "string"
  )
    return value as SummaryFrame;
  if (
    (value.kind === "open" || value.kind === "invalidation") &&
    "resume_cursor" in value &&
    typeof value.resume_cursor === "string"
  ) {
    if (value.kind === "open") return value as SummaryFrame;
    if (
      "event" in value &&
      typeof value.event === "object" &&
      value.event !== null &&
      "kind" in value.event &&
      typeof value.event.kind === "string"
    )
      return value as SummaryFrame;
  }
  throw new Error("Invalid summary frame.");
}

export function watchSummary(
  transport: Transport,
  invalidate: () => void,
  state: (state: string) => void,
) {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let cursor: string | undefined;
  let failures = 0;
  async function connect() {
    state(failures ? "Reconnecting" : "Connecting");
    try {
      const response = await transport.fetch(
        `/api/events${cursor ? `?after=${encodeURIComponent(cursor)}` : ""}`,
        { signal: controller.signal, headers: { Accept: "text/event-stream" } },
      );
      await consumeSse(response, (data) => {
        const frame = summaryFrame(data);
        if (frame.kind === "reset") {
          cursor = undefined;
          invalidate();
          return;
        }
        cursor = frame.resume_cursor;
        failures = 0;
        state("Live");
        // Reconcile missed changes on every new subscription, even without a cursor.
        if (
          frame.kind === "open" ||
          ["configuration", "catalog", "project"].includes(frame.event.kind)
        )
          invalidate();
      });
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) return;
    }
    if (!controller.signal.aborted) {
      state("Reconnecting");
      timer = setTimeout(
        () => void connect(),
        Math.min(1000 * 2 ** failures++, 15000),
      );
    }
  }
  void connect();
  return () => {
    controller.abort();
    clearTimeout(timer);
  };
}
