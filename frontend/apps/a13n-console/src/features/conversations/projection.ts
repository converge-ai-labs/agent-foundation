import type { RunEvent } from "../../service-client";
import type { Schema } from "../../shared/api";
export interface PresentedItem {
  id: string;
  kind: string;
  state: string;
  parentId: string | null;
  firstCursor: string;
  lastCursor: string;
  /**
   * `occurred_at` of the first observed event for this Item, and of the event
   * that gave it a terminal state. Both are null for an Item read from a
   * retained snapshot, which carries stream cursors but no timestamps, and
   * `endedAt` stays null when an Item is interrupted without a terminal
   * observation.
   */
  startedAt: string | null;
  endedAt: string | null;
  text: string;
  role: string;
  toolName: string;
  arguments: string;
  result?: unknown;
  failure?: unknown;
  protectedReasoning: boolean;
  detail?: unknown;
  display?: boolean;
  /** `a13n.steering-source` presentation provenance for enqueued user content. */
  steeringSource?: string;
}
export function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
export function compareCursors(a: string, b: string): number {
  if (!/^\d+-\d+$/.test(a) || !/^\d+-\d+$/.test(b))
    throw new Error("Invalid Run stream cursor.");
  const [aMs = "0", aSeq = "0"] = a.split("-"),
    [bMs = "0", bSeq = "0"] = b.split("-");
  return BigInt(aMs) < BigInt(bMs)
    ? -1
    : BigInt(aMs) > BigInt(bMs)
      ? 1
      : BigInt(aSeq) < BigInt(bSeq)
        ? -1
        : BigInt(aSeq) > BigInt(bSeq)
          ? 1
          : 0;
}
function emptyItem(
  id: string,
  kind: string,
  cursor: string,
  occurredAt: string | null = null,
): PresentedItem {
  return {
    id,
    kind,
    state: "in_progress",
    parentId: null,
    firstCursor: cursor,
    lastCursor: cursor,
    startedAt: occurredAt,
    endedAt: null,
    text: "",
    role: "assistant",
    toolName: "",
    arguments: "",
    protectedReasoning: false,
  };
}
function applyPayload(
  item: PresentedItem,
  type: string,
  payload: Record<string, unknown>,
  occurredAt: string,
): PresentedItem {
  const next = { ...item };
  if (typeof payload.item_kind === "string") next.kind = payload.item_kind;
  if (typeof payload.parent_item_id === "string")
    next.parentId = payload.parent_item_id;
  if (typeof payload.item_state === "string") {
    next.state = payload.item_state;
    if (payload.item_state !== "in_progress") next.endedAt = occurredAt;
  }
  if (typeof payload.role === "string") next.role = payload.role;
  if (isObject(payload.metadata)) {
    if (typeof payload.metadata.display === "boolean")
      next.display = payload.metadata.display;
    const source = payload.metadata["a13n.steering-source"];
    if (typeof source === "string") next.steeringSource = source;
  }
  if (
    ["agui.text_message_content", "agui.reasoning_message_content"].includes(
      type,
    ) &&
    typeof payload.delta === "string"
  )
    next.text += payload.delta;
  if (type === "agui.reasoning_encrypted_value") next.protectedReasoning = true;
  if (
    type === "agui.tool_call_start" &&
    typeof payload.toolCallName === "string"
  )
    next.toolName = payload.toolCallName;
  if (type === "agui.tool_call_args" && typeof payload.delta === "string")
    next.arguments += payload.delta;
  if (type === "agui.tool_call_result") next.result = payload.content;
  if (payload.failure !== undefined) next.failure = payload.failure;
  if (payload.interruption !== undefined) next.failure = payload.interruption;
  return next;
}
/** Lifecycle closure must not overwrite a newer Item read from another page. */
export function interruptOpenItems(
  items: ReadonlyMap<string, PresentedItem>,
  cursor: string,
): Map<string, PresentedItem> {
  return new Map(
    [...items].map(([id, item]) => [
      id,
      item.state === "in_progress" &&
      compareCursors(cursor, item.lastCursor) >= 0
        ? { ...item, state: "interrupted", lastCursor: cursor }
        : item,
    ]),
  );
}
export function applyRunEvent(
  items: ReadonlyMap<string, PresentedItem>,
  entry: RunEvent,
): Map<string, PresentedItem> {
  const { event, cursor } = entry;
  if (event.event_type === "run.recovery")
    return interruptOpenItems(items, cursor);
  if (!event.item_id) return new Map(items);
  const previous = items.get(event.item_id);
  if (previous && compareCursors(cursor, previous.lastCursor) <= 0)
    return new Map(items);
  const item = applyPayload(
    previous ??
      emptyItem(
        event.item_id,
        String(event.payload.item_kind ?? "unknown"),
        cursor,
        event.occurred_at,
      ),
    event.event_type,
    event.payload,
    event.occurred_at,
  );
  item.lastCursor = cursor;
  const result = new Map(items);
  result.set(item.id, item);
  return result;
}
export function presentRetainedItem(
  item: Schema["ItemResource"],
): PresentedItem {
  compareCursors(item.first_stream_id, item.last_stream_id);
  const presented = emptyItem(item.id, item.kind, item.first_stream_id);
  const content = item.content;
  if (isObject(content) && item.kind !== "run_output") {
    if (typeof content.text === "string") presented.text = content.text;
    if (typeof content.role === "string") presented.role = content.role;
    if (typeof content.toolCallName === "string")
      presented.toolName = content.toolCallName;
    if (typeof content.arguments === "string")
      presented.arguments = content.arguments;
    presented.result = content.result;
    presented.failure = content.failure ?? content.interruption;
    presented.protectedReasoning = "encrypted_value" in content;
    if (isObject(content.metadata)) {
      if (typeof content.metadata.display === "boolean")
        presented.display = content.metadata.display;
      const source = content.metadata["a13n.steering-source"];
      if (typeof source === "string") presented.steeringSource = source;
    }
  } else presented.detail = content;
  return {
    ...presented,
    state: item.state,
    parentId: item.parent_item_id,
    lastCursor: item.last_stream_id,
  };
}
export function mergeRetainedItems(
  current: ReadonlyMap<string, PresentedItem>,
  items: readonly Schema["ItemResource"][],
) {
  const merged = new Map(current);
  for (const item of items) {
    const previous = merged.get(item.id);
    if (
      !previous ||
      compareCursors(item.last_stream_id, previous.lastCursor) >= 0
    )
      // A retained snapshot has no timestamps; keep any already observed.
      merged.set(item.id, {
        ...presentRetainedItem(item),
        startedAt: previous?.startedAt ?? null,
        endedAt: previous?.endedAt ?? null,
      });
  }
  return merged;
}

export function parseItemValue(value: unknown): unknown {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}
