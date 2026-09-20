import type { Schema } from "../../../shared/api";
import { nextCursor, type LifecycleFact, type LogEntry } from "./model";

export interface LogContext {
  runId: string;
  threadId: string;
  /** Wall-clock start of the Run; cursors and timestamps derive from it. */
  startedAt: string;
  attemptId: string;
  harnessRunId: string;
}

export interface ModelUsage {
  model?: string;
  input: number;
  output: number;
  cacheRead?: number;
  cacheWrite?: number;
  /** Decimal string, as the Service serializes a priced cost. */
  cost?: string;
}

const PROVIDER = "openai";

/**
 * One Run's stream log in the vocabulary the Service emits: `agui.*` and
 * `item.*` presentation events from the Harness observer, and lifecycle events
 * projected from committed facts. Offsets are milliseconds from the Run's
 * start, which become both cursors and `occurred_at` timestamps.
 */
export class RunLog {
  readonly entries: LogEntry[] = [];
  readonly facts: LifecycleFact[] = [];
  private offset = 0;
  private ordinal = 0;
  private requests = 0;
  private sequence = 0;
  private attemptNumber = 1;

  constructor(private readonly context: LogContext) {}

  get elapsed() {
    return this.offset;
  }
  get attemptId() {
    return this.context.attemptId;
  }
  get runId() {
    return this.context.runId;
  }
  get harnessRunId() {
    return this.context.harnessRunId;
  }
  /** Later attempts of the same Run carry their own scope. */
  attempt(attemptId: string, harnessRunId: string) {
    this.context.attemptId = attemptId;
    this.context.harnessRunId = harnessRunId;
    this.attemptNumber += 1;
    this.requests = 0;
    return this;
  }
  advance(milliseconds: number) {
    this.offset += milliseconds;
    return this;
  }

  private timestamp() {
    return new Date(
      Date.parse(this.context.startedAt) + Math.round(this.offset),
    ).toISOString();
  }

  emit(
    eventType: string,
    payload: Record<string, unknown>,
    itemId?: string,
    harnessRunId: string | null = this.context.harnessRunId,
  ): LogEntry {
    const occurredAt = this.timestamp();
    const entry: LogEntry = {
      cursor: nextCursor(this.entries.at(-1)?.cursor, occurredAt),
      offsetMs: this.offset,
      event: {
        schema_version: "1",
        event_id: `evt_${this.context.runId}_${(this.ordinal++).toString(36).padStart(4, "0")}`,
        run_id: this.context.runId,
        thread_id: this.context.threadId,
        occurred_at: occurredAt,
        event_type: eventType,
        item_id: itemId ?? null,
        harness_run_id: harnessRunId,
        run_attempt_id: this.context.attemptId,
        payload: payload as Schema["RunStreamEvent"]["payload"],
      },
    };
    this.entries.push(entry);
    return entry;
  }

  /** An `agui.custom` observation in the Stream Protocol value envelope. */
  custom(name: string, kind: string, payload: Record<string, unknown>) {
    const occurredAt = this.timestamp();
    return this.emit("agui.custom", {
      name,
      value: {
        thread_id: this.context.threadId,
        run_id: this.context.harnessRunId,
        sequence: this.sequence++,
        occurred_at: occurredAt,
        event: { schema_version: "1", kind, payload },
      },
    });
  }

  /** A Toolset capability observation: its payload is the event itself. */
  capability(name: string, payload: Record<string, unknown>) {
    const occurredAt = this.timestamp();
    return this.emit("agui.custom", {
      name,
      value: {
        thread_id: this.context.threadId,
        run_id: this.context.harnessRunId,
        sequence: this.sequence++,
        occurred_at: occurredAt,
        event: { event_kind: "capability", kind: name, ...payload },
      },
    });
  }

  /* Lifecycle -------------------------------------------------------------- */

  /** A lifecycle fact reaches the stream inside its projection envelope. */
  private lifecycle(
    eventType: string,
    entityType: Schema["LifecycleEntityType"],
    data: Record<string, unknown>,
  ) {
    const harnessRunId =
      eventType === "run_attempt.running" ? this.context.harnessRunId : null;
    const fact: LifecycleFact = {
      eventType,
      entityType,
      occurredAt: this.timestamp(),
      attemptId: entityType === "run" ? null : this.context.attemptId,
      harnessRunId,
      data,
    };
    this.facts.push(fact);
    return this.emit(
      eventType,
      {
        resource_type: entityType,
        resource_id:
          entityType === "run" ? this.context.runId : this.context.attemptId,
        resource_seq: this.facts.length,
        resource_version: this.facts.length,
        schema_version: "1",
        actor_type: "worker",
        actor_id: null,
        data,
      },
      undefined,
      harnessRunId,
    );
  }

  accepted() {
    return this.lifecycle("run.accepted", "run", { status: "accepted" });
  }
  leased() {
    this.lifecycle("run_attempt.leased", "run_attempt", {
      attempt_number: this.attemptNumber,
      worker_build_id: "preview-worker-1",
    });
    if (this.attemptNumber > 1)
      this.emit("run.recovery", { reason: "worker_replaced" });
    return this.lifecycle("run_attempt.running", "run_attempt", {
      attempt_number: this.attemptNumber,
      harness_run_id: this.context.harnessRunId,
    });
  }
  running() {
    return this.lifecycle("run.running", "run", { status: "running" });
  }
  completed() {
    this.lifecycle("run_attempt.succeeded", "run_attempt", {
      attempt_number: this.attemptNumber,
    });
    return this.lifecycle("run.completed", "run", { status: "completed" });
  }
  failed(failure: Record<string, unknown>) {
    this.lifecycle("run_attempt.failed", "run_attempt", {
      attempt_number: this.attemptNumber,
      failure,
    });
    return this.lifecycle("run.failed", "run", { status: "failed", failure });
  }
  attemptFailed(failure: Record<string, unknown>) {
    return this.lifecycle("run_attempt.failed", "run_attempt", {
      attempt_number: this.attemptNumber,
      failure,
    });
  }
  cancelled() {
    this.lifecycle("run_attempt.cancelled", "run_attempt", {
      attempt_number: this.attemptNumber,
    });
    return this.lifecycle("run.cancelled", "run", { status: "cancelled" });
  }
  waiting(reason: string) {
    this.lifecycle("run_attempt.yielded", "run_attempt", {
      attempt_number: this.attemptNumber,
      yield_reason: reason,
    });
    return this.lifecycle("run.waiting", "run", {
      status: "waiting",
      wait_reason: reason,
    });
  }

  /* Model requests --------------------------------------------------------- */

  /** Bracket one model request and report its usage, as the Harness does. */
  modelRequest(durationMs: number, usage: ModelUsage, contextTokens?: number) {
    const index = this.requests++;
    const requestId = `model-request-${index + 1}`;
    if (contextTokens !== undefined)
      this.custom("a13n.harness.lifecycle", "lifecycle", {
        type: "context_snapshot",
        request_index: index,
        request_tokens: contextTokens,
        trigger_tokens: 120_000,
      });
    this.custom("a13n.harness.lifecycle", "lifecycle", {
      type: "model_request_started",
      request_id: requestId,
      request_index: index,
      message_count: 2 + index * 2,
    });
    this.advance(durationMs);
    this.custom("a13n.harness.lifecycle", "lifecycle", {
      type: "model_request_completed",
      request_id: requestId,
      request_index: index,
    });
    this.usageReport(requestId, index, usage);
    return requestId;
  }

  modelRequestFailed(durationMs: number, errorCode: string) {
    const index = this.requests++;
    const requestId = `model-request-${index + 1}`;
    this.custom("a13n.harness.lifecycle", "lifecycle", {
      type: "model_request_started",
      request_id: requestId,
      request_index: index,
      message_count: 2 + index * 2,
    });
    this.advance(durationMs);
    this.custom("a13n.harness.lifecycle", "lifecycle", {
      type: "model_request_failed",
      request_id: requestId,
      request_index: index,
      error_code: errorCode,
    });
    return requestId;
  }

  usageReport(requestId: string, ordinal: number, usage: ModelUsage) {
    const cost =
      usage.cost ?? ((usage.input * 1.25 + usage.output * 10) / 1e6).toFixed(6);
    return this.custom("a13n.harness.usage", "usage", {
      type: "usage_report",
      report_id: `usage-${this.context.harnessRunId}-${ordinal}`,
      reason: "model_request",
      trigger_record_id: `${requestId}-response`,
      chunk_index: 0,
      chunk_count: 1,
      records: [
        {
          kind: "model",
          record_id: `${requestId}-response`,
          run_id: this.context.harnessRunId,
          response_ordinal: ordinal,
          agent_instance_id: `${this.context.harnessRunId}:root`,
          response_state: "final",
          model_name: usage.model ?? "gpt-5",
          provider_name: PROVIDER,
          response_timestamp: this.timestamp(),
          request_usage: {
            input_tokens: usage.input,
            output_tokens: usage.output,
            cache_read_tokens: usage.cacheRead ?? 0,
            cache_write_tokens: usage.cacheWrite ?? 0,
            cost,
          },
          cost_source: "catalog",
          pricing_status: "priced",
        },
      ],
    });
  }

  /* Items ------------------------------------------------------------------ */

  /** Stream prose in chunks so live replay looks like generation. */
  message(
    itemId: string,
    text: string,
    options: {
      role?: string;
      chunkMs?: number;
      kind?: "text_message" | "reasoning_message";
    } = {},
  ) {
    const kind = options.kind ?? "text_message";
    const prefix =
      kind === "reasoning_message"
        ? "agui.reasoning_message"
        : "agui.text_message";
    const first = this.emit(
      `${prefix}_start`,
      {
        messageId: itemId,
        role: options.role ?? "assistant",
        metadata: { display: true },
        item_kind: kind,
      },
      itemId,
    );
    let last = first;
    for (const delta of chunks(text)) {
      this.advance(options.chunkMs ?? 40);
      last = this.emit(
        `${prefix}_content`,
        { messageId: itemId, delta, item_kind: kind },
        itemId,
      );
    }
    last = this.emit(
      `${prefix}_end`,
      { messageId: itemId, item_kind: kind, item_state: "completed" },
      itemId,
    );
    this.terminal(itemId, kind, "completed", first.cursor, last.cursor, {
      terminal_event_type: `${prefix}_end`,
    });
    return itemId;
  }

  /** A tool call from its arguments to its result. */
  tool(
    itemId: string,
    name: string,
    input: unknown,
    options: {
      durationMs?: number;
      result?: unknown;
      state?: "completed" | "failed";
      /** Leave the call open, as a deferred approval or question does. */
      pending?: boolean;
      before?: () => void;
    } = {},
  ) {
    const first = this.emit(
      "agui.tool_call_start",
      {
        toolCallId: itemId,
        toolCallName: name,
        source_tool_call_id: itemId,
        metadata: { display: true },
        item_kind: "tool_call",
      },
      itemId,
    );
    this.emit(
      "agui.tool_call_args",
      {
        toolCallId: itemId,
        delta: JSON.stringify(input),
        item_kind: "tool_call",
      },
      itemId,
    );
    this.emit(
      "agui.tool_call_end",
      { toolCallId: itemId, item_kind: "tool_call" },
      itemId,
    );
    if (options.pending) return itemId;
    this.advance(options.durationMs ?? 400);
    options.before?.();
    const state = options.state ?? "completed";
    const last = this.emit(
      "agui.tool_call_result",
      {
        messageId: `${itemId}_result`,
        toolCallId: itemId,
        source_tool_call_id: itemId,
        role: "tool",
        content: resultText(options.result),
        item_kind: "tool_call",
        item_state: state,
        ...(state === "failed"
          ? {
              failure: {
                code: "tool_result_failed",
                message: "The tool returned a failed presentation outcome.",
              },
            }
          : {}),
      },
      itemId,
    );
    this.terminal(itemId, "tool_call", state, first.cursor, last.cursor, {
      terminal_event_type: "agui.tool_call_result",
    });
    return itemId;
  }

  /** The Service projects its own `item.<state>` event after a terminal one. */
  private terminal(
    itemId: string,
    kind: string,
    state: "completed" | "failed",
    firstStreamId: string,
    lastContentStreamId: string,
    content: Record<string, unknown>,
  ) {
    return this.emit(
      `item.${state}`,
      {
        item_kind: kind,
        item_state: state,
        first_stream_id: firstStreamId,
        last_content_stream_id: lastContentStreamId,
        content,
        ...(state === "failed"
          ? {
              failure: {
                code: "tool_result_failed",
                message: "The tool returned a failed presentation outcome.",
              },
            }
          : {}),
      },
      itemId,
    );
  }

  /** Steering appends the enqueued user content as its own displayed Item. */
  steering(itemId: string, text: string, enqueueId: string) {
    this.custom("a13n.harness.state", "state", {
      type: "steering_input_enqueued",
      enqueue_id: enqueueId,
      source: "external",
      references: [],
    });
    const first = this.emit(
      "agui.text_message_start",
      {
        messageId: itemId,
        role: "user",
        metadata: { display: true, "a13n.steering-source": "external" },
        item_kind: "text_message",
      },
      itemId,
    );
    const last = this.emit(
      "agui.text_message_content",
      { messageId: itemId, delta: text, item_kind: "text_message" },
      itemId,
    );
    this.emit(
      "agui.text_message_end",
      { messageId: itemId, item_kind: "text_message", item_state: "completed" },
      itemId,
    );
    this.terminal(
      itemId,
      "text_message",
      "completed",
      first.cursor,
      last.cursor,
      { terminal_event_type: "agui.text_message_end" },
    );
    return itemId;
  }
}

/** Split prose into streamable deltas without breaking words. */
function chunks(text: string): string[] {
  const parts = text.match(/\S+\s*/g) ?? [text];
  const deltas: string[] = [];
  for (let index = 0; index < parts.length; index += 4)
    deltas.push(parts.slice(index, index + 4).join(""));
  return deltas.length ? deltas : [text];
}

/** AG-UI tool results travel as text; structured results are JSON encoded. */
function resultText(value: unknown): string {
  if (value === undefined || value === null) return "";
  return typeof value === "string" ? value : JSON.stringify(value);
}
