import type { Schema, Transport } from "./client";

type SummaryFrame =
  | (Schema<"SummaryOpenFrame"> & { kind: "open" })
  | (Schema<"SummaryEventFrame"> & { kind: "invalidation" })
  | (Schema<"ResetFrame"> & { kind: "reset" });

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
    ) {
      const event = value.event;
      if ("notice" in event && event.notice != null) {
        const notice = event.notice;
        if (
          event.kind !== "root_operation" ||
          !("root_thread_id" in event) ||
          typeof event.root_thread_id !== "string" ||
          !("epoch" in event) ||
          typeof event.epoch !== "string" ||
          typeof notice !== "object" ||
          notice === null ||
          !("receipt_id" in notice) ||
          typeof notice.receipt_id !== "string" ||
          !notice.receipt_id ||
          !("status" in notice) ||
          !["completed", "failed", "suspended"].includes(
            String(notice.status),
          ) ||
          !("brief" in notice) ||
          typeof notice.brief !== "string" ||
          !notice.brief ||
          Array.from(notice.brief).length > 320
        )
          throw new Error("Invalid root operation notice.");
      }
      return value as SummaryFrame;
    }
  }
  throw new Error("Invalid summary frame.");
}

export function watchSummary(
  transport: Transport,
  invalidate: (event?: Schema<"SummaryEventFrame">["event"]) => void,
  state: (state: string) => void,
) {
  let cursor: string | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let failures = 0;
  const subscription = transport.realtime.subscribe({
    stream: "summary",
    cursor: () => cursor,
    state,
    receive(data) {
      try {
        const frame = summaryFrame(data);
        if (frame.kind === "reset") throw new Error(frame.reason);
        cursor = frame.resume_cursor;
        failures = 0;
        state("Live");
        // A valid resume replays missed hints, not every HTTP observation.
        if (frame.kind === "open") {
          if (!frame.resumed) invalidate();
        } else invalidate(frame.event);
      } catch {
        cursor = undefined;
        invalidate();
        state("Reconnecting");
        clearTimeout(timer);
        timer = setTimeout(
          () => subscription.restart(),
          Math.min(1000 * 2 ** failures++, 15000),
        );
      }
    },
  });
  const close = () => {
    clearTimeout(timer);
    subscription();
  };
  close.retry = subscription.retry;
  return close;
}
