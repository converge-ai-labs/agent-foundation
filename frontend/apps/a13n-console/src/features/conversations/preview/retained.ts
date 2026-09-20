import type { Schema } from "../../../shared/api";
import type { LogEntry } from "./model";

type Content = Record<string, Schema["JsonValue"]>;
type Item = Schema["ItemResource"];

/** Payload fields the Service copies verbatim onto a retained Item. */
const COPIED = [
  "messageId",
  "role",
  "toolCallId",
  "toolCallName",
  "parentMessageId",
  "source_tool_call_id",
  "metadata",
];
const DELTA_FIELD: Record<string, string> = {
  "agui.text_message_content": "text",
  "agui.reasoning_message_content": "text",
  "agui.tool_call_args": "arguments",
};
const IMPLIES_COMPLETED = [
  "agui.text_message_end",
  "agui.reasoning_message_end",
  "agui.tool_call_result",
];
const EXPLICIT_STATES = ["completed", "failed", "interrupted"];

function asContent(value: Schema["JsonValue"]): Content {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? { ...(value as Content) }
    : {};
}

/**
 * The Service retains Items as the projection of its own Run stream, so this
 * mirrors `display_projection.project_display`: a snapshot read and a live
 * replay of the same Run must agree exactly.
 */
export function retainedItems(log: readonly LogEntry[]): Item[] {
  const items = new Map<string, Item>();
  for (const entry of log) {
    const { event } = entry;
    if (event.event_type === "run.recovery")
      for (const [id, item] of items)
        if (item.state === "in_progress")
          items.set(id, { ...item, state: "interrupted" });
    if (!event.item_id) continue;
    items.set(event.item_id, merge(items.get(event.item_id), entry));
  }
  return [...items.values()];
}

/** Close every Item still open once a Run's display is finalized. */
export function finalizeItems(items: Item[]): Item[] {
  return items.map((item) =>
    item.state === "in_progress" ? { ...item, state: "interrupted" } : item,
  );
}

function merge(previous: Item | undefined, entry: LogEntry): Item {
  const { event } = entry;
  const payload = event.payload;
  const kind = typeof payload.item_kind === "string" ? payload.item_kind : "";
  const parent =
    typeof payload.parent_item_id === "string"
      ? payload.parent_item_id
      : (previous?.parent_item_id ?? null);
  const content: Content =
    kind === "run_output"
      ? asContent(payload.content ?? null)
      : mergeContent(previous ? asContent(previous.content) : {}, entry);
  let state = previous?.state ?? "in_progress";
  if (
    typeof payload.item_state === "string" &&
    EXPLICIT_STATES.includes(payload.item_state)
  )
    state = payload.item_state;
  else if (IMPLIES_COMPLETED.includes(event.event_type)) state = "completed";
  return {
    id: event.item_id!,
    kind: kind || (previous?.kind ?? "unknown"),
    state,
    parent_item_id: parent,
    first_stream_id: previous?.first_stream_id ?? entry.cursor,
    last_stream_id: entry.cursor,
    content,
  };
}

function mergeContent(content: Content, entry: LogEntry): Content {
  const { event } = entry;
  const payload = event.payload;
  for (const field of COPIED)
    if (field in payload) content[field] = payload[field]!;
  content.run_attempt_id = event.run_attempt_id ?? null;
  content.harness_run_id = event.harness_run_id ?? null;
  const delta = DELTA_FIELD[event.event_type];
  if (delta !== undefined && typeof payload.delta === "string") {
    const current = content[delta];
    content[delta] =
      `${typeof current === "string" ? current : ""}${payload.delta}`;
  }
  if (event.event_type === "agui.tool_call_result")
    content.result = payload.content ?? null;
  if (event.event_type === "agui.reasoning_encrypted_value")
    content.encrypted_value = payload.encryptedValue ?? null;
  for (const field of ["failure", "interruption"])
    if (field in payload) content[field] = payload[field]!;
  return content;
}
