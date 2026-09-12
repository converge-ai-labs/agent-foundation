import { ApiError, type Schema, type Transport } from "../transport/client";
import { consumeSse } from "../transport/events";

export type DisplayBlock = {
  id: string;
  kind:
    "assistant" | "user" | "thinking" | "tool" | "activity" | "media" | "task";
  text: string;
  name?: string;
  result?: string;
  done?: boolean;
  metadata?: Record<string, unknown>;
  value?: unknown;
  diagnostic?: boolean;
};
type Payload = Record<string, unknown>;
function object(value: unknown): value is Payload {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function string(value: unknown) {
  return typeof value === "string" ? value : "";
}
export type FocusFrame =
  | (Schema<"FocusSnapshotFrame"> & { kind: "snapshot" })
  | (Schema<"FocusReplayFrame"> & { kind: "root_stream" })
  | (Schema<"FocusReadyFrame"> & { kind: "ready" })
  | (Schema<"FocusEventFrame"> & { kind: "event" })
  | (Schema<"ResetFrame"> & { kind: "reset" });
export function focusFrame(value: unknown): FocusFrame {
  if (!object(value)) throw new Error("Invalid conversation stream frame.");
  if (
    value.kind === "snapshot" &&
    object(value.snapshot) &&
    object(value.snapshot.thread) &&
    typeof value.snapshot.epoch === "string" &&
    typeof value.snapshot.cutover_sequence === "number" &&
    (value.resume_cursor === null || typeof value.resume_cursor === "string")
  )
    return value as FocusFrame;
  if (
    value.kind === "root_stream" &&
    typeof value.run_id === "string" &&
    Array.isArray(value.events)
  )
    return value as FocusFrame;
  if (
    value.kind === "event" &&
    typeof value.resume_cursor === "string" &&
    object(value.event) &&
    typeof value.event.sequence === "number" &&
    typeof value.event.event_type === "string"
  )
    return value as FocusFrame;
  if (value.kind === "ready" && typeof value.resume_cursor === "string")
    return value as FocusFrame;
  if (value.kind === "reset" && typeof value.reason === "string")
    return value as FocusFrame;
  throw new Error("Invalid conversation stream frame.");
}

class RootRunChanged extends Error {}

// Rendering only: history/receipt queries remain the continuation/control owners.
export class FocusDisplay {
  snapshot?: Schema<"ThreadFocusSnapshot">;
  cursor?: string;
  runId?: string;
  baseContinuation?: string | null;
  blocks = new Map<string, DisplayBlock>();
  ready = false;
  replayCount = 0;
  sequence = 0;
  gap = false;
  private fragments = new Map<
    string,
    { count: number; parts: string[]; size: number }
  >();
  private fragmentBytes = 0;
  reset() {
    this.snapshot = undefined;
    this.cursor = undefined;
    this.runId = undefined;
    this.baseContinuation = undefined;
    this.blocks.clear();
    this.fragments.clear();
    this.fragmentBytes = 0;
    this.ready = false;
    this.replayCount = 0;
    this.sequence = 0;
    this.gap = false;
  }
  accept(frame: FocusFrame) {
    if (frame.kind === "reset") {
      this.reset();
      return;
    }
    if (frame.kind === "snapshot") {
      this.reset();
      this.snapshot = frame.snapshot;
      this.sequence = frame.snapshot.cutover_sequence;
      this.runId = frame.snapshot.root_stream?.run_id;
      this.baseContinuation = frame.snapshot.root_stream
        ? frame.snapshot.root_stream.base_continuation_id
        : frame.snapshot.thread.continuation_id;
      this.cursor = frame.resume_cursor ?? undefined;
      this.ready = !!frame.resume_cursor;
    } else if (frame.kind === "root_stream") {
      if (
        !this.snapshot?.root_stream ||
        this.ready ||
        frame.run_id !== this.runId
      )
        throw new Error("Unexpected root replay.");
      for (const event of frame.events) {
        if (event.index !== this.replayCount++)
          throw new Error("Incomplete root replay.");
        this.fold(event.event_type, event.payload, event.payload_omitted);
      }
    } else if (frame.kind === "ready") {
      if (
        !this.snapshot?.root_stream ||
        this.replayCount !== this.snapshot.root_stream.event_count
      )
        throw new Error("Incomplete root replay.");
      this.cursor = frame.resume_cursor;
      this.ready = true;
    } else {
      if (!this.ready)
        throw new Error("Live output arrived before replay was ready.");
      if (frame.event.epoch !== this.snapshot?.epoch)
        throw new Error("Conversation stream epoch changed.");
      if (frame.event.sequence <= this.sequence) return;
      if (
        frame.event.run_kind === "root" &&
        this.runId !== frame.event.run_id
      ) {
        // A live event has no base continuation. Re-bootstrap from the existing
        // observer's finite prefix instead of assigning the previous Run's base.
        this.cursor = undefined;
        throw new RootRunChanged(
          "Root Run changed; reload its focused snapshot.",
        );
      }
      this.sequence = frame.event.sequence; // Global sequences are sparse within one Thread.
      this.cursor = frame.resume_cursor;
      if (frame.event.run_kind !== "root") return;
      this.fold(
        frame.event.event_type,
        frame.event.payload,
        frame.event.payload_omitted,
      );
    }
  }
  private custom(payload: Payload): Payload | undefined {
    if (payload.name !== "a13n.stream.fragment") return payload;
    const value = payload.value;
    if (
      !object(value) ||
      typeof value.id !== "string" ||
      !Number.isInteger(value.index) ||
      !Number.isInteger(value.count) ||
      typeof value.data !== "string"
    ) {
      this.gap = true;
      return;
    }
    const index = Number(value.index),
      count = Number(value.count);
    if (index === 0) {
      const old = this.fragments.get(value.id);
      if (old) this.fragmentBytes -= old.size;
      if (this.fragments.size >= 8) {
        this.gap = true;
        return;
      }
      this.fragments.set(value.id, { count, parts: [], size: 0 });
    }
    const assembly = this.fragments.get(value.id);
    const size = new TextEncoder().encode(value.data).length;
    if (
      !assembly ||
      index !== assembly.parts.length ||
      count !== assembly.count ||
      count < 1 ||
      index >= count ||
      this.fragmentBytes + size > 64 * 1024 * 1024
    ) {
      if (assembly) this.fragmentBytes -= assembly.size;
      this.fragments.delete(value.id);
      this.gap = true;
      return;
    }
    assembly.parts.push(value.data);
    assembly.size += size;
    this.fragmentBytes += size;
    if (assembly.parts.length !== count) return;
    this.fragments.delete(value.id);
    this.fragmentBytes -= assembly.size;
    try {
      const result: unknown = JSON.parse(assembly.parts.join(""));
      if (object(result) && result.name !== "a13n.stream.fragment")
        return result;
    } catch {
      /* Display a gap rather than partial domain data. */
    }
    this.gap = true;
  }
  private fold(type: string, payload: Payload | null, omitted: boolean) {
    if (omitted) this.gap = true;
    if (!payload) return;
    if (object(payload.metadata) && payload.metadata.display === false) return;
    const id = string(
      type.startsWith("TOOL_CALL_")
        ? (payload.tool_call_id ?? payload.toolCallId)
        : (payload.message_id ?? payload.messageId),
    );
    const key = `${this.runId}:${id}`;
    if (type === "TEXT_MESSAGE_START" || type === "REASONING_MESSAGE_START") {
      this.blocks.set(key, {
        id: key,
        kind: type.startsWith("REASONING")
          ? "thinking"
          : payload.role === "user"
            ? "user"
            : "assistant",
        text: "",
        ...(object(payload.metadata) ? { metadata: payload.metadata } : {}),
      });
    } else if (
      type === "TEXT_MESSAGE_CONTENT" ||
      type === "REASONING_MESSAGE_CONTENT"
    ) {
      const block = this.blocks.get(key) ?? {
        id: key,
        kind: "assistant" as const,
        text: "",
      };
      this.blocks.set(key, {
        ...block,
        text: block.text + string(payload.delta),
        ...(object(payload.metadata) ? { metadata: payload.metadata } : {}),
      });
    } else if (
      type === "TEXT_MESSAGE_END" ||
      type === "REASONING_MESSAGE_END" ||
      type === "TOOL_CALL_END"
    ) {
      const block = this.blocks.get(key);
      if (block) this.blocks.set(key, { ...block, done: true });
    } else if (type === "TOOL_CALL_START") {
      this.blocks.set(key, {
        id: key,
        kind: "tool",
        name: string(payload.tool_call_name ?? payload.toolCallName),
        text: "",
      });
    } else if (type === "TOOL_CALL_ARGS") {
      const block = this.blocks.get(key) ?? {
        id: key,
        kind: "tool" as const,
        text: "",
      };
      this.blocks.set(key, {
        ...block,
        text: block.text + string(payload.delta),
      });
    } else if (type === "TOOL_CALL_RESULT") {
      const block = this.blocks.get(key) ?? {
        id: key,
        kind: "tool" as const,
        text: "",
      };
      this.blocks.set(key, {
        ...block,
        result: string(payload.content),
        done: true,
      });
    } else if (type === "RUN_FINISHED" || type === "RUN_ERROR") {
      const status = `${this.runId}:execution`;
      this.blocks.set(status, {
        id: status,
        kind: "activity",
        name:
          type === "RUN_FINISHED"
            ? "Execution completed"
            : payload.code === "run_cancelled"
              ? "Execution cancelled"
              : "Execution failed",
        text:
          string(payload.message) ||
          "Execution finished. Inspect the operation receipt for continuation and Environment outcomes.",
      });
    } else if (type === "CUSTOM") {
      const event = this.custom(payload);
      if (
        !event ||
        (object(event.metadata) && event.metadata.display === false)
      )
        return;
      this.foldCustom(event);
    }
  }
  private foldCustom(event: Payload) {
    const name = string(event.name);
    const value = object(event.value) ? event.value : {};
    const source = object(value.event) ? value.event : {};
    const payload = object(source.payload) ? source.payload : {};
    if (name === "a13n.input.media") {
      const key = `${this.runId}:${string(event.message_id)}`;
      this.blocks.set(key, {
        id: key,
        kind: "media",
        text: "",
        value: source.content,
        metadata: object(event.metadata) ? event.metadata : undefined,
      });
      return;
    }
    if (
      name === "a13n.harness.state" &&
      payload.type === "task_changed" &&
      object(payload.task)
    ) {
      const task = payload.task;
      const key = `${this.runId}:task:${string(task.id)}`;
      this.blocks.set(key, {
        id: key,
        kind: "task",
        name: string(task.status),
        text: string(
          task.status === "in_progress"
            ? task.active_form || task.subject
            : task.subject,
        ),
        result: [
          string(task.owner),
          Array.isArray(task.blocked_by) && task.blocked_by.length
            ? `Blocked by ${task.blocked_by.length} tasks`
            : "",
        ]
          .filter(Boolean)
          .join(" · "),
      });
      return;
    }
    if (
      name === "a13n.harness.context" &&
      payload.type === "context_snapshot"
    ) {
      const key = `${this.runId}:context`;
      this.blocks.set(key, {
        id: key,
        kind: "activity",
        name: "Request context",
        text: `${payload.request_tokens ?? "Unknown"} request tokens · ${payload.trigger_tokens ?? "Unknown"} compaction trigger (not context-window capacity)`,
      });
      return;
    }
    if (
      (name === "a13n.harness.context" &&
        typeof payload.operation_id === "string") ||
      name === "a13n.context.compaction_summary"
    ) {
      const operation =
        name === "a13n.context.compaction_summary" ? source : payload;
      const key = `${this.runId}:context:${string(operation.operation_id)}`;
      const previous = this.blocks.get(key);
      const summary = name === "a13n.context.compaction_summary";
      this.blocks.set(key, {
        id: key,
        kind: "activity",
        name: summary
          ? previous?.name || "Context compaction"
          : string(payload.type).replaceAll("_", " "),
        text: summary
          ? previous?.text || "Context replacement summary"
          : [
              string(payload.error_code),
              string(payload.failed_phase),
              string(payload.reason),
            ]
              .filter(Boolean)
              .join(" · "),
        result: summary ? string(source.summary) : previous?.result,
      });
      return;
    }
    if (name === "a13n.harness.run_result") {
      const key = `${this.runId}:execution`;
      this.blocks.set(key, {
        id: key,
        kind: "activity",
        name: "Execution suspended",
        text:
          string(source.suspend_reason) ||
          "A response is needed before execution can continue.",
      });
      return;
    }
    // Unknown capability events remain inspectable together, not one expanded
    // JSON transcript row for every request lifecycle or usage update.
    const key = `${this.runId}:custom:${this.blocks.size}`;
    this.blocks.set(key, {
      id: key,
      kind: "activity",
      diagnostic: true,
      name,
      text: JSON.stringify(event.value, null, 2),
    });
  }
}

export function showFocusedOutput(
  display: FocusDisplay,
  continuation: string | null | undefined,
  activeRunId?: string | null,
  replacingHistory = false,
) {
  // Transcript pages identify the immutable initial state; focused Run bases
  // use null for the same pre-continuation state. Keep the actual page ID intact.
  const selected = continuation?.startsWith("initial:") ? null : continuation;
  return (
    !!display.runId &&
    (replacingHistory ||
      selected === undefined ||
      selected === display.baseContinuation ||
      activeRunId === display.runId)
  );
}

export function watchThread(
  transport: Transport,
  threadId: string,
  display: FocusDisplay,
  changed: () => void,
  connection: (value: string) => void,
  invalidate: () => void,
) {
  const abort = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let failures = 0;
  async function connect() {
    let runChanged = false;
    connection("Connecting");
    try {
      const params = display.cursor
        ? `?after=${encodeURIComponent(display.cursor)}`
        : "";
      const response = await transport.fetch(
        `/api/threads/${encodeURIComponent(threadId)}/events${params}`,
        { signal: abort.signal, headers: { Accept: "text/event-stream" } },
      );
      await consumeSse(response, (value) => {
        if (abort.signal.aborted) return;
        let frame: FocusFrame;
        try {
          frame = focusFrame(value);
          display.accept(frame);
        } catch (error) {
          // Invalid or incompatible presentation data needs a fresh prefix, not
          // an endless resume from the last cursor before the offending frame.
          display.cursor = undefined;
          throw error;
        }
        if (frame.kind === "snapshot" || frame.kind === "reset") invalidate();
        if (
          frame.kind === "event" &&
          ![
            "TEXT_MESSAGE_CONTENT",
            "TOOL_CALL_ARGS",
            "REASONING_MESSAGE_CONTENT",
          ].includes(frame.event.event_type)
        )
          invalidate();
        failures = 0;
        connection(display.ready ? "Live" : "Loading current output");
        changed();
      });
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) return;
      runChanged = error instanceof RootRunChanged;
      if (error instanceof SyntaxError) display.cursor = undefined;
      if (!display.ready) display.reset();
    }
    if (!abort.signal.aborted) {
      connection("Reconnecting");
      timer = setTimeout(
        () => void connect(),
        runChanged ? 0 : Math.min(1000 * 2 ** failures++, 15000),
      );
    }
  }
  void connect();
  return () => {
    abort.abort();
    clearTimeout(timer);
  };
}
