import type { RunEvent } from "@converge.ai/a13n";
import type { Schema } from "../../shared/api";
export interface PresentedItem {
  id: string;
  kind: string;
  state: string;
  parentId: string | null;
  firstCursor: string;
  lastCursor: string;
  text: string;
  role: string;
  toolName: string;
  arguments: string;
  result?: unknown;
  failure?: unknown;
  protectedReasoning: boolean;
  detail?: unknown;
  display?: boolean;
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
function emptyItem(id: string, kind: string, cursor: string): PresentedItem {
  return {
    id,
    kind,
    state: "streaming",
    parentId: null,
    firstCursor: cursor,
    lastCursor: cursor,
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
): PresentedItem {
  const next = { ...item };
  if (typeof payload.item_kind === "string") next.kind = payload.item_kind;
  if (typeof payload.parent_item_id === "string")
    next.parentId = payload.parent_item_id;
  if (typeof payload.item_state === "string") next.state = payload.item_state;
  if (typeof payload.role === "string") next.role = payload.role;
  if (isObject(payload.metadata)) {
    if (typeof payload.metadata.display === "boolean")
      next.display = payload.metadata.display;
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
export function applyRunEvent(
  items: ReadonlyMap<string, PresentedItem>,
  entry: RunEvent,
): Map<string, PresentedItem> {
  const { event, cursor } = entry;
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
      ),
    event.event_type,
    event.payload,
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
  let presented = emptyItem(item.id, item.kind, item.first_stream_id);
  if (isObject(item.content) && Array.isArray(item.content.events))
    for (const event of item.content.events) {
      if (
        isObject(event) &&
        typeof event.event_type === "string" &&
        isObject(event.payload)
      )
        presented = applyPayload(presented, event.event_type, event.payload);
    }
  else presented.detail = item.content;
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
      merged.set(item.id, presentRetainedItem(item));
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
