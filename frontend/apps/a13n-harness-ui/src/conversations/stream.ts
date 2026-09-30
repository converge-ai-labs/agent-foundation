import {
  DisplayState,
  sameProducer,
  type DisplaySnapshot,
  type DisplayDelta,
  type DisplayBlock as SharedBlock,
} from "a13n-ui/display";
import { contextPeerId, displayBlock } from "./display-presentation";
import type { Schema, Transport } from "../transport/client";
import type { ThreadRefresh } from "./refresh";
import { ProcessObservations } from "./process-observations";
import { type AppliedEdit, type ToolView } from "./tool-presentation";

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
  | Schema<"FocusSnapshotFrame">
  | Schema<"FocusReplayFrame">
  | Schema<"FocusCommitFrame">
  | Schema<"FocusReadyFrame">
  | Schema<"FocusEventFrame">
  | Schema<"ResetFrame">;
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
    value.kind === "display_chunk" &&
    typeof value.run_id === "string" &&
    Number.isSafeInteger(value.index) &&
    typeof value.data === "string"
  )
    return value as FocusFrame;
  if (
    value.kind === "display_commit" &&
    typeof value.run_id === "string" &&
    Number.isSafeInteger(value.chunk_count)
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
class DisplayCapacityError extends Error {}

// Only the shared operation applicator owns display state. Controls remain
// separate observations, never authority for continuation or execution status.
export class FocusDisplay {
  constructor(
    private readonly baselineLimit = 64 * 1024 * 1024,
    readonly processes = new ProcessObservations(),
  ) {}
  retainedPresentation?: FocusDisplay;
  snapshot?: Schema<"ThreadFocusSnapshot">;
  cursor?: string;
  runId?: string;
  baseContinuation?: string | null;
  blocks = new Map<string, DisplayBlock>();
  ready = false;
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
  private readonly blockSequences = new Map<string, number>();
  private state?: DisplayState;
  private readonly staging = new Map<
    string,
    { summary: Schema<"RootStreamSummary">; parts: string[]; bytes: number }
  >();
  readonly children = new Map<
    string,
    { parentId: string; threadId: string; display: FocusDisplay }
  >();

  presentationFor(continuation: string | null | undefined) {
    const selected = continuation?.startsWith("initial:") ? null : continuation;
    if (
      selected === this.baseContinuation ||
      (selected != null && this.checkpoints.has(selected))
    ) {
      this.retainedPresentation = undefined;
      return this;
    }
    return this.retainedPresentation &&
      showFocusedOutput(this.retainedPresentation, continuation)
      ? this.retainedPresentation
      : this;
  }
  blocksAfter(continuation: string | null | undefined) {
    const checkpoint = continuation
      ? this.checkpoints.get(continuation)
      : undefined;
    return [...this.blocks.values()].filter(
      (block) =>
        checkpoint === undefined ||
        (this.blockSequences.get(block.id) ?? Infinity) > checkpoint,
    );
  }
  childOutput(child: Schema<"ChildExecutionView">) {
    const value = this.children.get(child.execution_id);
    return value?.parentId === child.parent_thread_id &&
      value.threadId === child.child_thread_id
      ? value.display
      : undefined;
  }
  reset() {
    this.snapshot = undefined;
    this.retainedPresentation = undefined;
    this.cursor = undefined;
    this.runId = undefined;
    this.baseContinuation = undefined;
    this.blocks.clear();
    this.children.clear();
    this.processes.clear();
    this.staging.clear();
    this.ready = false;
    this.sequence = 0;
    this.gap = false;
    this.contextUsage = undefined;
    this.recovery = undefined;
    this.terminalFailure = undefined;
    this.checkpoints.clear();
    this.blockSequences.clear();
    this.state = undefined;
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
      for (const summary of [
        ...(frame.snapshot.root_stream ? [frame.snapshot.root_stream] : []),
        ...(frame.snapshot.child_streams ?? []),
      ])
        this.staging.set(summary.run_id, { summary, parts: [], bytes: 0 });
      if (frame.resume_cursor && this.staging.size)
        throw new Error("Baseline has not committed.");
      this.cursor = frame.resume_cursor ?? undefined;
      this.ready = !!frame.resume_cursor;
      return;
    }
    if (frame.kind === "display_chunk") {
      const stage = this.staging.get(frame.run_id);
      if (this.ready || !stage || frame.index !== stage.parts.length)
        throw new Error("Incomplete display baseline.");
      stage.bytes += new TextEncoder().encode(frame.data).length;
      if (stage.bytes > this.baselineLimit)
        throw new DisplayCapacityError(
          "Current output exceeds the browser display budget; saved history remains available.",
        );
      stage.parts.push(frame.data);
      return;
    }
    if (frame.kind === "display_commit") {
      const stage = this.staging.get(frame.run_id);
      if (
        !stage ||
        stage.parts.length !== frame.chunk_count ||
        frame.chunk_count < 1
      )
        throw new Error("Incomplete display baseline.");
      const baseline = JSON.parse(stage.parts.join("")) as {
        display: DisplaySnapshot;
        block_sequences: Record<string, number>;
        controls: Schema<"LiveEvent">[];
      };
      const snapshot = baseline.display;
      snapshot.continuity = {};
      const state = new DisplayState(snapshot);
      if (
        !sameProducer(
          state.position.producer,
          stage.summary.position.producer,
        ) ||
        state.position.sequence !== stage.summary.position.sequence
      )
        throw new Error("Display baseline coverage differs.");
      let target: FocusDisplay = this;
      if (stage.summary.execution_id && stage.summary.parent_thread_id) {
        target = new FocusDisplay(this.baselineLimit, this.processes);
        this.children.set(stage.summary.execution_id, {
          parentId: stage.summary.parent_thread_id,
          threadId: stage.summary.thread_id,
          display: target,
        });
      }
      target.runId = frame.run_id;
      target.baseContinuation = stage.summary.base_continuation_id;
      target.state = state;
      target.gap = snapshot.omitted > 0;
      for (const [id, sequence] of Object.entries(baseline.block_sequences))
        target.blockSequences.set(id, sequence);
      for (const [id, sequence] of Object.entries(
        stage.summary.checkpoints ?? {},
      ))
        target.checkpoints.set(id, sequence);
      target.present(snapshot.blocks);
      for (const control of baseline.controls) target.control(control);
      this.staging.delete(frame.run_id);
      return;
    }
    if (frame.kind === "ready") {
      if (!this.snapshot || this.staging.size)
        throw new Error("Incomplete display baseline.");
      this.cursor = frame.resume_cursor;
      this.ready = true;
      return;
    }
    if (!this.ready)
      throw new Error("Live output arrived before baseline commit.");
    const event = frame.event;
    if (event.epoch !== this.snapshot?.epoch)
      throw new Error("Conversation stream epoch changed.");
    if (event.sequence <= this.sequence) return;
    if (event.root_thread_id !== this.snapshot.thread.thread.thread_id)
      throw new Error("Conversation lineage changed.");
    let target: FocusDisplay = this;
    if (event.run_kind === "child") {
      const child = event.execution_id
        ? this.children.get(event.execution_id)
        : undefined;
      if (
        !child ||
        child.parentId !== event.parent_thread_id ||
        child.threadId !== event.thread_id ||
        child.display.runId !== event.run_id
      )
        throw new RootRunChanged(
          "Child producer changed; reload its baseline.",
        );
      target = child.display;
    } else if (this.runId !== event.run_id)
      throw new RootRunChanged("Root producer changed; reload its baseline.");
    if (event.event_type === "DISPLAY_DELTA") {
      if (event.payload_omitted || !event.delta || !target.state)
        throw new Error("Display delta was omitted.");
      const delta = event.delta as DisplayDelta;
      const removed = delta.operations.flatMap((operation) =>
        operation.op === "blocks.remove" ? operation.ids : [],
      );
      const touched = new Set(
        target.state
          .selectBlocks(removed)
          .flatMap((block) => contextPeerId(block) ?? []),
      );
      if (target.state.apply(delta)) {
        for (const operation of delta.operations) {
          if (operation.op === "block.put" || operation.op === "block.append") {
            const id =
              operation.op === "block.put" ? operation.block.id : operation.id;
            target.blockSequences.set(
              id,
              target.blockSequences.get(id) ?? delta.through_sequence,
            );
            touched.add(id);
          } else if (operation.op === "blocks.remove") {
            for (const id of operation.ids) {
              target.blocks.delete(id);
              target.blockSequences.delete(id);
            }
            target.gap = operation.omitted > 0;
          }
        }
        target.present(target.state.selectBlocks(touched));
        if (
          target.recovery?.state === "retrying" &&
          [...touched].some((id) =>
            ["assistant", "thinking", "tool"].includes(
              target.blocks.get(id)?.kind ?? "",
            ),
          )
        )
          target.recovery = { ...target.recovery, state: "resumed" };
      }
    } else target.control(event);
    // Transport cursors are committed only after the whole semantic batch succeeds.
    this.sequence = event.sequence;
    this.cursor = frame.resume_cursor;
  }
  private present(blocks: SharedBlock[]) {
    const peers =
      this.state?.selectBlocks(
        blocks.flatMap((block) => contextPeerId(block) ?? []),
      ) ?? [];
    const joined = new Map(
      [...blocks, ...peers].map((block) => [block.id, block]),
    );
    for (const block of joined.values()) {
      if (!this.blockSequences.has(block.id)) continue; // The selected saved prefix is rendered by history.
      const peerId = contextPeerId(block);
      const view = displayBlock(block, peerId ? joined.get(peerId) : undefined);
      if (view) {
        this.blocks.set(block.id, view);
        if (view.kind === "tool" && "result" in block.content && this.runId)
          this.processes.result(
            this.runId,
            view.name,
            view.text,
            block.content.result,
          );
      } else this.blocks.delete(block.id);
    }
  }
  private control(event: Schema<"LiveEvent">) {
    if (event.payload_omitted) {
      this.gap = true;
      return;
    }
    const data = event.payload;
    if (!data) return;
    if (
      event.event_type === "RUN_FINISHED" ||
      event.event_type === "RUN_ERROR"
    ) {
      this.processes.end(this.runId);
      this.terminalFailure =
        event.event_type === "RUN_ERROR" && data.code !== "run_cancelled"
          ? string(data.message) || "The operation could not finish."
          : undefined;
      if (this.recovery)
        this.recovery = {
          ...this.recovery,
          state: event.event_type === "RUN_FINISHED" ? "resumed" : "ended",
        };
      return;
    }
    const value = object(data.value) ? data.value : {};
    const source = object(value.event) ? value.event : {};
    const payload = object(source.payload) ? source.payload : {};
    if (
      data.name === "a13n.shell.status" &&
      this.runId &&
      typeof source.process_id === "string" &&
      typeof source.phase === "string"
    )
      this.processes.status(
        this.runId,
        source.process_id,
        source.phase,
        source.exit_code,
      );
    if (
      data.name === "a13n.harness.recovery" &&
      payload.type === "model_retry_scheduled"
    )
      this.recovery = {
        id: `${this.runId}:retry:${payload.attempt}`,
        state: "retrying",
        retries: typeof payload.attempt === "number" ? payload.attempt - 1 : 1,
      };
    if (
      data.name === "a13n.harness_ui.checkpoint" &&
      typeof source.continuation_id === "string" &&
      typeof source.display_sequence === "number"
    ) {
      this.checkpoints.set(source.continuation_id, source.display_sequence);
      while (this.checkpoints.size > 256)
        this.checkpoints.delete(this.checkpoints.keys().next().value!);
    }
    if (payload.type === "usage_report" && Array.isArray(payload.records)) {
      const resumed =
        typeof payload.usage_id === "string" && value.run_id === this.runId;
      for (const record of payload.records) {
        if (
          !object(record) ||
          record.kind !== "model" ||
          (record.source ?? "agent") !== "agent" ||
          (record.run_id !== this.runId && !resumed) ||
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
        )
          this.contextUsage = {
            tokens: input_tokens + output_tokens,
            ordinal: record.response_ordinal,
          };
      }
    }
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
        if (error instanceof DisplayCapacityError) {
          display.gap = true;
          changed();
          connection(error.message);
          subscription();
          return;
        }
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
