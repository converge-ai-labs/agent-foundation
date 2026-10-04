/** Compact display operations. Hosts own coverage, persistence and rendering. */
export interface DisplayItem {
  id: string;
  ordinal: number;
  kind: "text_message" | "reasoning_message" | "tool_call" | "observation";
  state: "in_progress" | "completed" | "interrupted" | "failed";
  first_stream_id: string;
  last_stream_id: string;
  started_at: string;
  ended_at?: string | null;
  content: Record<string, unknown>;
}

export type DisplayChange =
  | { type: "set"; item: DisplayItem }
  | {
      type: "append";
      id: string;
      field: "text" | "arguments";
      text: string;
      after_stream_id: string;
      last_stream_id: string;
      state: DisplayItem["state"];
      ended_at: string | null;
    };

const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const states = new Set(["in_progress", "completed", "interrupted", "failed"]);
const kinds = new Set([
  "text_message",
  "reasoning_message",
  "tool_call",
  "observation",
]);
const position = (value: unknown) =>
  typeof value === "string" && /^\d+-\d+$/.test(value);
const time = (value: unknown) =>
  typeof value === "string" && Number.isFinite(Date.parse(value));

export function isDisplayChange(value: unknown): value is DisplayChange {
  if (!record(value)) return false;
  if (value.type === "set") {
    const item = value.item;
    return (
      record(item) &&
      typeof item.id === "string" &&
      Number.isSafeInteger(item.ordinal) &&
      Number(item.ordinal) > 0 &&
      kinds.has(String(item.kind)) &&
      states.has(String(item.state)) &&
      position(item.first_stream_id) &&
      position(item.last_stream_id) &&
      time(item.started_at) &&
      (item.ended_at == null || time(item.ended_at)) &&
      record(item.content)
    );
  }
  return (
    value.type === "append" &&
    typeof value.id === "string" &&
    (value.field === "text" || value.field === "arguments") &&
    typeof value.text === "string" &&
    position(value.after_stream_id) &&
    position(value.last_stream_id) &&
    states.has(String(value.state)) &&
    (value.ended_at === null || time(value.ended_at))
  );
}

/** All-or-nothing: a failed append must not advance either items or Host coverage. */
export function applyDisplayChanges(
  items: Map<string, DisplayItem>,
  changes: readonly DisplayChange[],
) {
  const staged = new Map<string, DisplayItem>();
  for (const change of changes) {
    if (change.type === "set") {
      staged.set(change.item.id, structuredClone(change.item));
      continue;
    }
    const previous = staged.get(change.id) ?? items.get(change.id);
    if (
      !previous ||
      previous.last_stream_id !== change.after_stream_id ||
      typeof previous.content[change.field] !== "string"
    )
      throw new Error("Display append is missing its predecessor.");
    staged.set(change.id, {
      ...previous,
      content: {
        ...previous.content,
        [change.field]: previous.content[change.field] + change.text,
      },
      last_stream_id: change.last_stream_id,
      state: change.state,
      ended_at: change.ended_at,
    });
  }
  for (const [id, item] of staged) items.set(id, item);
}
