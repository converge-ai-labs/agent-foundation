import type { Schema, Transport } from "../transport/client";
import type { ThreadRefresh } from "./refresh";
import { ProcessObservations } from "./process-observations";
import {
  sourceText,
  type AppliedEdit,
  type ToolView,
} from "./tool-presentation";

export type DisplayBlock = {
  id: string;
  toolCallId?: string;
  kind:
    "assistant" | "user" | "thinking" | "tool" | "activity" | "media" | "task";
  text: string;
  name?: string;
  result?: string;
  done?: boolean;
  outcome?: ToolView["outcome"];
  failure?: string;
  retry?: boolean;
  stopped?: boolean;
  edit?: AppliedEdit;
  images?: Schema<"ToolImageView">[];
  imageUnavailable?: boolean;
  provider?: string;
  metadata?: Record<string, unknown>;
  value?: unknown;
  diagnostic?: boolean;
  context?: "handoff" | "compaction";
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
  constructor(
    private readonly fragmentLimit = 64 * 1024 * 1024,
    readonly processes = new ProcessObservations(),
  ) {}
  // Previous visible suffix only; never consulted for cursors, controls or activity.
  retainedPresentation?: FocusDisplay;
  presentationFor(continuation: string | null | undefined) {
    const selected = continuation?.startsWith("initial:") ? null : continuation;
    if (
      selected === this.baseContinuation ||
      (selected != null && this.checkpoints.has(selected))
    ) {
      this.retainedPresentation = undefined;
      return this;
    }
    if (
      this.retainedPresentation &&
      showFocusedOutput(this.retainedPresentation, continuation)
    )
      return this.retainedPresentation;
    return this;
  }
  snapshot?: Schema<"ThreadFocusSnapshot">;
  cursor?: string;
  runId?: string;
  baseContinuation?: string | null;
  blocks = new Map<string, DisplayBlock>();
  ready = false;
  replayCount = 0;
  sequence = 0;
  gap = false;
  recovery?: {
    id: string;
    state: "retrying" | "resumed" | "ended";
    retries: number;
  };
  terminalFailure?: string;
  contextUsage?: { tokens: number; ordinal: number };
  readonly checkpoints = new Map<string, number>();
  private readonly savedBlocks = new Map<string, number>();
  blocksAfter(continuation: string | null | undefined) {
    const checkpoint = continuation
      ? this.checkpoints.get(continuation)
      : undefined;
    return [...this.blocks.values()].filter(
      (block) =>
        checkpoint === undefined ||
        (this.savedBlocks.get(block.id) ?? Infinity) > checkpoint,
    );
  }
  readonly children = new Map<
    string,
    {
      parentId: string;
      threadId: string;
      display: FocusDisplay;
    }
  >();
  childOutput(child: Schema<"ChildExecutionView">) {
    const observed = this.children.get(child.execution_id);
    return observed?.parentId === child.parent_thread_id &&
      observed.threadId === child.child_thread_id
      ? observed.display
      : undefined;
  }
  private fragments = new Map<
    string,
    { count: number; parts: string[]; size: number }
  >();
  private fragmentBytes = 0;
  reset() {
    this.snapshot = undefined;
    this.retainedPresentation = undefined;
    this.cursor = undefined;
    this.runId = undefined;
    this.baseContinuation = undefined;
    this.blocks.clear();
    this.children.clear();
    this.processes.clear();
    this.fragments.clear();
    this.fragmentBytes = 0;
    this.ready = false;
    this.replayCount = 0;
    this.sequence = 0;
    this.gap = false;
    this.contextUsage = undefined;
    this.recovery = undefined;
    this.terminalFailure = undefined;
    this.checkpoints.clear();
    this.savedBlocks.clear();
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
      if (frame.event.root_thread_id !== this.snapshot?.thread.thread.thread_id)
        return;
      if (frame.event.run_kind === "child") {
        this.foldChild(frame.event);
        return;
      }
      this.fold(
        frame.event.event_type,
        frame.event.payload,
        frame.event.payload_omitted,
      );
    }
  }
  private foldChild(event: Schema<"LiveEvent">) {
    if (
      !event.execution_id ||
      !event.parent_thread_id ||
      event.thread_id === event.root_thread_id
    )
      return;
    let child = this.children.get(event.execution_id);
    if (
      child &&
      (child.parentId !== event.parent_thread_id ||
        child.threadId !== event.thread_id)
    )
      return;
    if (child && child.display.runId !== event.run_id) {
      // One execution can start another Run after a deferred checkpoint. The
      // root-lineage stream is ordered; never concatenate two Run suffixes.
      this.processes.end(child.display.runId);
      child.display = new FocusDisplay(128 * 1024, this.processes);
      child.display.runId = event.run_id;
      child.display.gap = true;
    }
    if (!child) {
      // This is an observed suffix, not a replay of the child's full Run.
      // Keep a finite set even when a root starts many sequential executions.
      if (this.children.size >= 64)
        this.children.delete(this.children.keys().next().value!);
      child = {
        parentId: event.parent_thread_id,
        threadId: event.thread_id,
        display: new FocusDisplay(128 * 1024, this.processes),
      };
      child.display.runId = event.run_id;
      this.children.set(event.execution_id, child);
    }
    child.display.fold(event.event_type, event.payload, event.payload_omitted);
    child.display.trimChildOutput();
  }
  private trimChildOutput() {
    // Bound both long deltas and event cardinality; saved output is fetched
    // independently and never synthesized from this lossy display window.
    while (this.blocks.size > 128) {
      this.blocks.delete(this.blocks.keys().next().value!);
      this.gap = true;
    }
    let remaining = 128 * 1024;
    for (const [key, block] of [...this.blocks].reverse()) {
      const size = JSON.stringify(block).length;
      if (remaining <= 0) {
        this.blocks.delete(key);
        this.gap = true;
      } else if (
        size > remaining &&
        (block.kind === "assistant" || block.kind === "thinking")
      ) {
        this.blocks.set(key, {
          id: block.id,
          kind: block.kind,
          name: block.name,
          text: (block.result ?? block.text).slice(-remaining),
          done: block.done,
        });
        remaining = 0;
        this.gap = true;
      } else if (size > remaining) {
        this.blocks.delete(key);
        this.gap = true;
      } else remaining -= size;
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
      this.fragmentBytes + size > this.fragmentLimit
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
    if (this.recovery && (type === "RUN_FINISHED" || type === "RUN_ERROR"))
      this.recovery = {
        ...this.recovery,
        state:
          this.recovery.state === "resumed" || type === "RUN_FINISHED"
            ? "resumed"
            : "ended",
      };
    else if (
      this.recovery?.state === "retrying" &&
      (((type === "TEXT_MESSAGE_CONTENT" ||
        type === "REASONING_MESSAGE_CONTENT") &&
        string(payload.delta)) ||
        type === "TOOL_CALL_START")
    )
      this.recovery = { ...this.recovery, state: "resumed" };
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
        toolCallId: id,
        kind: "tool",
        name: string(payload.tool_call_name ?? payload.toolCallName),
        text: "",
      });
    } else if (type === "TOOL_CALL_ARGS") {
      const block = this.blocks.get(key) ?? {
        id: key,
        kind: "tool" as const,
        toolCallId: id,
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
        toolCallId: id,
        text: "",
      };
      this.blocks.set(key, {
        ...block,
        result: string(payload.content),
        done: true,
      });
      if (this.runId)
        this.processes.result(
          this.runId,
          block.name,
          block.text,
          payload.content,
        );
    } else if (type === "RUN_FINISHED" || type === "RUN_ERROR") {
      this.stopTools();
      const failed = type === "RUN_ERROR" && payload.code !== "run_cancelled";
      this.terminalFailure = failed
        ? string(payload.message) || "The operation could not finish."
        : undefined;
      const status = `${this.runId}:execution`;
      if (type === "RUN_FINISHED") {
        // Ordinary completion belongs to controls, not a temporary transcript row.
        this.blocks.delete(status);
        return;
      }
      this.blocks.set(status, {
        id: status,
        kind: "activity",
        diagnostic: failed,
        name:
          payload.code === "run_cancelled"
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
  private stopTools() {
    this.processes.end(this.runId);
    for (const [key, block] of this.blocks) {
      if (block.kind === "tool")
        this.blocks.set(key, { ...block, stopped: true });
    }
  }
  private foldCustom(event: Payload) {
    const name = string(event.name);
    const value = object(event.value) ? event.value : {};
    const source = object(value.event) ? value.event : {};
    const payload = object(source.payload) ? source.payload : {};
    if (
      name === "a13n.shell.status" &&
      this.runId &&
      typeof source.process_id === "string" &&
      typeof source.phase === "string"
    ) {
      this.processes.status(
        this.runId,
        source.process_id,
        source.phase,
        source.exit_code,
      );
      return;
    }
    if (
      name === "a13n.harness.recovery" &&
      payload.type === "model_retry_scheduled"
    ) {
      this.recovery = {
        id: `${this.runId}:retry:${payload.attempt}`,
        state: "retrying",
        // Attempt 1 is the original model request, not a reconnection.
        retries: typeof payload.attempt === "number" ? payload.attempt - 1 : 1,
      };
      return;
    }
    if (
      name === "a13n.harness_ui.checkpoint" &&
      typeof source.continuation_id === "string"
    ) {
      const ordinal = this.checkpoints.size + 1;
      this.checkpoints.set(source.continuation_id, ordinal);
      for (const id of this.blocks.keys()) {
        if (!this.savedBlocks.has(id)) this.savedBlocks.set(id, ordinal);
      }
      return;
    }
    if (payload.type === "usage_report" && Array.isArray(payload.records)) {
      for (const record of payload.records) {
        if (
          !object(record) ||
          record.kind !== "model" ||
          (record.source ?? "agent") !== "agent" ||
          record.run_id !== this.runId ||
          record.parent_agent_instance_id != null ||
          record.delegation_id != null ||
          typeof record.response_ordinal !== "number" ||
          !object(record.request_usage)
        )
          continue;
        const { input_tokens, output_tokens } = record.request_usage;
        if (
          typeof input_tokens === "number" &&
          typeof output_tokens === "number" &&
          record.response_ordinal > (this.contextUsage?.ordinal ?? -1)
        ) {
          this.contextUsage = {
            tokens: input_tokens + output_tokens,
            ordinal: record.response_ordinal,
          };
        }
      }
      return;
    }
    if (
      ["a13n.pydantic_ai.part_start", "a13n.pydantic_ai.part_end"].includes(
        name,
      ) &&
      object(source.part)
    ) {
      const part = source.part;
      if (
        ["builtin-tool-call", "builtin-tool-return"].includes(
          string(part.part_kind),
        ) &&
        typeof part.tool_call_id === "string"
      ) {
        const provider = string(part.provider_name) || "provider";
        const key = `${this.runId}:native:${provider}:${part.tool_call_id}`;
        const previous = this.blocks.get(key);
        const returned = part.part_kind === "builtin-tool-return";
        this.blocks.set(key, {
          id: key,
          kind: "tool",
          name: string(part.tool_name),
          text: "",
          ...previous,
          provider,
          ...(returned
            ? {
                result: sourceText(part.content),
                done: true,
                outcome: [
                  "success",
                  "failed",
                  "denied",
                  "interrupted",
                ].includes(string(part.outcome))
                  ? (part.outcome as ToolView["outcome"])
                  : undefined,
              }
            : {
                text: sourceText(part.args),
                done: name === "a13n.pydantic_ai.part_end" || previous?.done,
              }),
        });
        return;
      }
    }
    if (
      name === "a13n.harness-ui.tool_images" &&
      typeof source.tool_call_id === "string" &&
      Array.isArray(source.images)
    ) {
      const key = `${this.runId}:${source.tool_call_id}`;
      const block = this.blocks.get(key);
      const images = source.images.filter(
        (image): image is Schema<"ToolImageView"> =>
          object(image) &&
          typeof image.thread_id === "string" &&
          object(image.attachment) &&
          typeof image.attachment.attachment_id === "string" &&
          typeof image.attachment.name === "string" &&
          typeof image.attachment.media_type === "string" &&
          typeof image.attachment.size === "number",
      );
      this.blocks.set(key, {
        id: key,
        kind: "tool",
        text: "",
        name: "computer_observe",
        ...block,
        images,
        imageUnavailable: source.unavailable === true,
      });
      return;
    }
    if (
      name === "a13n.filesystem.edit_applied" &&
      typeof source.tool_call_id === "string" &&
      typeof source.file_path === "string" &&
      typeof source.before === "string" &&
      typeof source.after === "string"
    ) {
      const key = `${this.runId}:${source.tool_call_id}`;
      const block = this.blocks.get(key);
      this.blocks.set(key, {
        id: key,
        kind: "tool",
        text: "",
        name: "edit",
        ...block,
        edit: {
          file_path: source.file_path,
          before: source.before,
          after: source.after,
        },
      });
      return;
    }
    if (
      name === "a13n.pydantic_ai.function_tool_result" &&
      object(source.part) &&
      typeof source.part.tool_call_id === "string"
    ) {
      const part = source.part;
      if (
        part.part_kind === "tool-return" ||
        part.part_kind === "retry-prompt"
      ) {
        const key = `${this.runId}:${part.tool_call_id}`;
        const block = this.blocks.get(key);
        if (this.runId && part.part_kind === "tool-return")
          this.processes.result(
            this.runId,
            string(part.tool_name),
            block?.text,
            part.content,
          );
        this.blocks.set(key, {
          id: key,
          kind: "tool",
          text: "",
          name: string(part.tool_name),
          ...block,
          toolCallId: string(part.tool_call_id),
          result: sourceText(part.content),
          done: true,
          retry: part.part_kind === "retry-prompt",
          outcome:
            part.outcome === "failed" ||
            part.outcome === "denied" ||
            part.outcome === "interrupted" ||
            part.outcome === "success"
              ? part.outcome
              : undefined,
          failure:
            part.part_kind === "retry-prompt"
              ? sourceText(part.content) || "Tool input validation failed."
              : undefined,
        });
        return;
      }
    }
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
      return;
    }
    if (
      (name === "a13n.harness.context" &&
        typeof payload.operation_id === "string") ||
      name === "a13n.context.compaction_summary" ||
      name === "a13n.context.handoff_summary"
    ) {
      const operation =
        name === "a13n.context.compaction_summary" ||
        name === "a13n.context.handoff_summary"
          ? source
          : payload;
      const key = `context:${string(operation.operation_id)}`;
      const previous = this.blocks.get(key);
      const summary =
        name === "a13n.context.compaction_summary" ||
        name === "a13n.context.handoff_summary";
      const context =
        name === "a13n.context.compaction_summary" ||
        string(payload.type).startsWith("compaction_")
          ? "compaction"
          : "handoff";
      this.blocks.set(key, {
        id: key,
        kind: "activity",
        context,
        name: context === "compaction" ? "Compact Summary" : "Summary",
        text: summary
          ? previous?.text || "Summary ready"
          : [
              string(payload.type).endsWith("_failed")
                ? "Failed"
                : string(payload.type).endsWith("_completed")
                  ? "Completed"
                  : string(payload.type).endsWith("_prepared")
                    ? "Prepared"
                    : "In progress",
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
      this.stopTools();
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
    // Internal stream diagnostics are not conversation content.
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
      (selected != null &&
        display.checkpoints.has(selected) &&
        (!activeRunId || activeRunId === display.runId)))
  );
}

export function focusRefresh(frame: FocusFrame): ThreadRefresh | undefined {
  if (frame.kind === "reset") return "reconcile";
  if (frame.kind !== "event") return;
  const event = frame.event;
  if (["RUN_STARTED", "RUN_FINISHED", "RUN_ERROR"].includes(event.event_type))
    return "lifecycle";
  const content = object(event.payload) ? event.payload : {};
  const value = object(content.value) ? content.value : {};
  const source = object(value.event) ? value.event : {};
  const payload = object(source.payload) ? source.payload : {};
  if (content.name === "a13n.harness_ui.checkpoint") return "checkpoint";
  if (payload.type === "usage_report") return "usage";
}

export function watchThread(
  transport: Transport,
  threadId: string,
  display: FocusDisplay,
  changed: () => void,
  connection: (value: string) => void,
  invalidate: (reason: ThreadRefresh) => void,
  snapshotReceived?: (snapshot: Schema<"ThreadFocusSnapshot">) => void,
) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let failures = 0;
  let snapshotRetryAvailable = true;
  let replacement: FocusDisplay | undefined;
  let reconcileReplacement = false;
  let restarting = false;
  const subscription = transport.realtime.subscribe({
    stream: "focus",
    root: threadId,
    cursor: () => display.cursor,
    state(value) {
      if (value === "Reconnecting") {
        display.processes.end();
        replacement = undefined;
        changed();
      }
      connection(
        value === "Live" && !display.ready ? "Loading current output" : value,
      );
    },
    receive(value) {
      if (restarting) return;
      let immediate = false;
      try {
        const frame = focusFrame(value);
        if (frame.kind === "reset") {
          immediate =
            frame.reason === "live_snapshot_changed" && snapshotRetryAvailable;
          snapshotRetryAvailable = false;
          throw new Error(frame.reason);
        }
        if (frame.kind === "snapshot") {
          display.cursor = undefined;
          reconcileReplacement = !!display.snapshot;
          replacement = new FocusDisplay();
          snapshotReceived?.(frame.snapshot);
        }
        (replacement ?? display).accept(frame);
        if (replacement) {
          if (!replacement.ready) {
            connection("Loading current output");
            return;
          }
          const retained =
            display.retainedPresentation ??
            (display.runId && display.blocks.size
              ? Object.assign(new FocusDisplay(), display)
              : undefined);
          Object.assign(display, replacement);
          display.retainedPresentation = retained;
          replacement = undefined;
          // The prefix seeds cold state. Only a replacement needs HTTP
          // reconciliation; do not refetch a just-started initial history read.
          if (reconcileReplacement) invalidate("reconcile");
        }
        const reason = focusRefresh(frame);
        if (reason) invalidate(reason);
        failures = 0;
        snapshotRetryAvailable = true;
        connection(display.ready ? "Live" : "Loading current output");
        changed();
      } catch (error) {
        // Replace only this channel. Other roots and summary keep their cursors
        // and normal Run transitions never reconnect the physical socket.
        display.cursor = undefined;
        display.processes.end();
        replacement = undefined;
        restarting = true;
        if (!display.ready) display.reset();
        changed();
        connection("Loading current output");
        timer = setTimeout(
          () => {
            restarting = false;
            subscription.restart();
          },
          immediate || error instanceof RootRunChanged
            ? 0
            : Math.min(1000 * 2 ** failures++, 15000),
        );
      }
    },
  });
  return () => {
    display.processes.end();
    clearTimeout(timer);
    subscription();
  };
}
