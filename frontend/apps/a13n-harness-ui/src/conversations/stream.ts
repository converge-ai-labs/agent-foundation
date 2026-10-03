import type { ContentPart } from "@ag-ui/core";
import { readContentParts } from "a13n-ui";
import { applyItemChanges, type ItemChange } from "a13n-ui/display";
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
  resultParts?: ContentPart[];
  subagentRunId?: string;
  done?: boolean;
  outcome?: ToolView["outcome"];
  failure?: string;
  retry?: boolean;
  stopped?: boolean;
  edit?: AppliedEdit;
  images?: Schema<"ToolImageView">[];
  apps?: Schema<"AppReference">[];
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
  constructor(readonly processes = new ProcessObservations()) {}
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
  private readonly items = new Map<string, Schema<"Item">>();
  private itemPosition?: number;
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
  reset() {
    this.snapshot = undefined;
    this.retainedPresentation = undefined;
    this.cursor = undefined;
    this.runId = undefined;
    this.baseContinuation = undefined;
    this.blocks.clear();
    this.items.clear();
    this.children.clear();
    this.processes.clear();
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
      for (const [id, position] of Object.entries(
        frame.snapshot.root_stream?.checkpoints ?? {},
      ))
        this.checkpoints.set(id, position);
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
        this.apply(event.changes);
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
      if (
        frame.event.root_thread_id === this.snapshot?.thread.thread.thread_id
      ) {
        if (frame.event.run_kind === "child") this.foldChild(frame.event);
        else {
          if (frame.event.changes == null)
            throw new Error("Missing compact display changes.");
          this.apply(frame.event.changes);
        }
      }
      // Global sequences are sparse within one Thread. Never advance the
      // recoverable cursor across a missing compact baseline or malformed batch.
      this.sequence = frame.event.sequence;
      this.cursor = frame.resume_cursor;
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
      child.display = new FocusDisplay(this.processes);
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
        display: new FocusDisplay(this.processes),
      };
      child.display.runId = event.run_id;
      this.children.set(event.execution_id, child);
    }
    if (event.changes == null)
      throw new Error("Missing child display changes.");
    // Child output is an explicitly lossy suffix, with saved child output read
    // separately. Missing/evicted child baselines must not reset the root lane.
    const available = new Set(child.display.items.keys());
    const applicable = event.changes.filter((change) => {
      if (change.type === "set") {
        available.add(change.item.id);
        return true;
      }
      if (available.has(change.id)) return true;
      child!.display.gap = true;
      return false;
    });
    child.display.apply(applicable);
    child.display.trimChildOutput();
  }
  private apply(changes: readonly ItemChange<Schema<"Item">>[]) {
    const before = new Map(
      changes.map((change) => {
        const id = change.type === "set" ? change.item.id : change.id;
        return [id, this.items.get(id)?.last_stream_id];
      }),
    );
    if (!applyItemChanges(this.items, changes))
      throw new Error("Missing compact display baseline.");
    for (const [id, previousPosition] of before) {
      const item = this.items.get(id)!;
      if (item.last_stream_id === previousPosition) continue;
      const content = item.content;
      const position = Number(item.last_stream_id.split("-")[1]);
      this.itemPosition = position;
      if (object(content.metadata) && content.metadata.display === false)
        continue;
      if (item.kind === "observation") {
        if (object(content.event)) this.lifecycle(content.event);
        else this.foldCustom({ type: "CUSTOM", ...content });
        this.itemPosition = undefined;
        continue;
      }
      const subagentRunId = string(content.subagentRunId) || undefined;
      const scope = subagentRunId
        ? `${this.runId}:${subagentRunId}`
        : this.runId;
      const source = string(
        item.kind === "tool_call" ? content.toolCallId : content.messageId,
      );
      const key =
        content.provider != null
          ? `${scope}:native:${string(content.provider)}:${source}`
          : `${scope}:${source}`;
      if (object(content.metadata) && content.metadata.display === false)
        continue;
      const previous = this.blocks.get(key);
      const block: DisplayBlock = {
        ...previous,
        id: key,
        kind:
          item.kind === "tool_call"
            ? "tool"
            : item.kind === "reasoning_message"
              ? "thinking"
              : content.input_media != null
                ? "media"
                : content.role === "user"
                  ? "user"
                  : "assistant",
        text: string(
          item.kind === "tool_call" ? content.arguments : content.text,
        ),
        subagentRunId,
        done: item.state !== "in_progress",
        metadata: object(content.metadata) ? content.metadata : undefined,
      };
      if (item.kind === "tool_call") {
        block.toolCallId = source;
        block.name = string(content.toolCallName);
        block.provider = string(content.provider) || undefined;
        block.result =
          "value" in content
            ? sourceText(content.value)
            : typeof content.result === "string"
              ? content.result
              : undefined;
        block.retry = content.retry === true;
        block.stopped = item.state === "interrupted";
        block.edit = object(content.applied_edit)
          ? (content.applied_edit as AppliedEdit)
          : undefined;
        block.images = Array.isArray(content.tool_images)
          ? (content.tool_images as Schema<"ToolImageView">[])
          : undefined;
        block.apps = Array.isArray(content.mcp_apps)
          ? (content.mcp_apps as Schema<"AppReference">[])
          : undefined;
        block.imageUnavailable = content.tool_image_unavailable === true;
        if (
          ["success", "failed", "denied", "interrupted"].includes(
            string(content.outcome),
          )
        )
          block.outcome = content.outcome as ToolView["outcome"];
        if (block.retry)
          block.failure = block.result || "Tool input validation failed.";
        if (content.result_parts != null)
          block.resultParts = readContentParts(content.result_parts);
        if (object(content.failure))
          block.failure = string(content.failure.message);
        if (item.state === "failed" && !block.outcome) block.outcome = "failed";
        if (
          content.result != null ||
          content.result_parts != null ||
          "value" in content
        )
          this.processes.result(
            scope!,
            block.name,
            block.text,
            content.value ?? content.result ?? content.result_parts,
          );
      }
      if (content.input_media != null) block.value = content.input_media;
      this.blocks.set(key, block);
      this.savedBlocks.set(key, position);
      if (
        !subagentRunId &&
        this.recovery?.state === "retrying" &&
        block.kind !== "user"
      )
        this.recovery = { ...this.recovery, state: "resumed" };
    }
    this.itemPosition = undefined;
  }
  private trimChildOutput() {
    let itemBytes = 0;
    let itemCount = 0;
    for (const [id, item] of [...this.items].reverse()) {
      itemBytes += JSON.stringify(item).length;
      if (++itemCount > 128 || itemBytes > 128 * 1024) {
        this.items.delete(id);
        this.gap = true;
      }
    }
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
          subagentRunId: block.subagentRunId,
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
  private lifecycle(payload: Payload) {
    const type = string(payload.type);
    const subagentRunId = string(payload.subagentRunId) || undefined;
    if (
      !subagentRunId &&
      this.recovery &&
      (type === "RUN_FINISHED" || type === "RUN_ERROR")
    )
      this.recovery = {
        ...this.recovery,
        state:
          this.recovery.state === "resumed" || type === "RUN_FINISHED"
            ? "resumed"
            : "ended",
      };
    if (type === "RUN_FINISHED" || type === "RUN_ERROR") {
      this.stopTools();
      const failed = type === "RUN_ERROR";
      const outcome = object(payload.outcome)
        ? payload.outcome.type
        : "success";
      this.terminalFailure = failed
        ? string(payload.message) || "The operation could not finish."
        : undefined;
      const status = `${this.runId}:execution`;
      if (type === "RUN_FINISHED" && outcome === "success") {
        // Ordinary completion belongs to controls, not a temporary transcript row.
        this.blocks.delete(status);
        return;
      }
      this.blocks.set(status, {
        id: status,
        kind: "activity",
        diagnostic: failed,
        name:
          outcome === "cancelled"
            ? "Execution cancelled"
            : outcome === "interrupt"
              ? "Execution suspended"
              : "Execution failed",
        text:
          string(payload.message) ||
          (outcome === "interrupt"
            ? "A response is needed before execution can continue."
            : "Execution finished. Inspect the operation receipt for continuation and Environment outcomes."),
      });
    } else if (
      type === "SUBAGENT_STARTED" ||
      type === "SUBAGENT_FINISHED" ||
      type === "SUBAGENT_ERROR"
    ) {
      if (type !== "SUBAGENT_STARTED") this.stopTools(subagentRunId);
      const childKey = `${this.runId}:inline:${subagentRunId}`;
      const previous = this.blocks.get(childKey);
      this.blocks.set(childKey, {
        id: childKey,
        kind: "activity",
        name:
          previous?.name ||
          `Subagent · ${string(payload.name) || subagentRunId}`,
        text:
          type === "SUBAGENT_STARTED"
            ? "Running"
            : type === "SUBAGENT_ERROR"
              ? string(payload.message)
              : object(payload.outcome) && payload.outcome.type === "suspended"
                ? "Suspended"
                : "Completed",
        done: type !== "SUBAGENT_STARTED",
      });
    }
  }
  private stopTools(subagentRunId?: string) {
    this.processes.end(
      subagentRunId ? `${this.runId}:${subagentRunId}` : this.runId,
    );
    for (const [key, block] of this.blocks) {
      if (subagentRunId && block.subagentRunId !== subagentRunId) continue;
      if (block.subagentRunId)
        this.processes.end(`${this.runId}:${block.subagentRunId}`);
      if (block.kind === "tool")
        this.blocks.set(key, { ...block, stopped: true });
    }
  }
  private foldCustom(event: Payload) {
    const name = string(event.name);
    const subagentRunId = string(event.subagentRunId) || undefined;
    const scope = subagentRunId ? `${this.runId}:${subagentRunId}` : this.runId;
    const setBlock = (key: string, block: DisplayBlock) => {
      this.blocks.set(key, {
        ...block,
        ...(subagentRunId ? { subagentRunId } : {}),
      });
      if (this.itemPosition !== undefined)
        this.savedBlocks.set(key, this.itemPosition);
    };
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
        scope!,
        source.process_id,
        source.phase,
        source.exit_code,
      );
      return;
    }
    if (
      !subagentRunId &&
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
      !subagentRunId &&
      name === "a13n.harness_ui.checkpoint" &&
      typeof source.continuation_id === "string"
    ) {
      if (this.itemPosition !== undefined)
        this.checkpoints.set(source.continuation_id, this.itemPosition);
      return;
    }
    if (
      !subagentRunId &&
      payload.type === "usage_report" &&
      Array.isArray(payload.records)
    ) {
      const resumedScope =
        typeof payload.usage_id === "string" && value.run_id === this.runId;
      for (const record of payload.records) {
        if (
          !object(record) ||
          record.kind !== "model" ||
          (record.source ?? "agent") !== "agent" ||
          (record.run_id !== this.runId && !resumedScope) ||
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
          record.response_ordinal >= (this.contextUsage?.ordinal ?? -1)
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
      name === "a13n.harness.state" &&
      payload.type === "task_changed" &&
      object(payload.task)
    ) {
      const task = payload.task;
      const key = `${scope}:task:${string(task.id)}`;
      setBlock(key, {
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
      const key = `${subagentRunId ? `${scope}:` : ""}context:${string(operation.operation_id)}`;
      const previous = this.blocks.get(key);
      const summary =
        name === "a13n.context.compaction_summary" ||
        name === "a13n.context.handoff_summary";
      const context =
        name === "a13n.context.compaction_summary" ||
        string(payload.type).startsWith("compaction_")
          ? "compaction"
          : "handoff";
      setBlock(key, {
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
  let observedAt = Date.now();
  // Publication hints are best effort. A quiet model/tool wait must still
  // converge to a selected checkpoint without requiring another stream event.
  const quietRefresh = setInterval(() => {
    if (display.ready && display.runId && Date.now() - observedAt >= 10_000)
      invalidate("checkpoint");
  }, 10_000);
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
      observedAt = Date.now();
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
    clearInterval(quietRefresh);
    subscription();
  };
}
