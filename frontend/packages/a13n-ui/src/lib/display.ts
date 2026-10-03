/** Shared compact-display application. Native and AG-UI semantics stay on the server. */
export interface CompactItem {
  id: string;
  state: "in_progress" | "completed" | "interrupted" | "failed";
  last_stream_id: string;
  ended_at?: string | null;
  content: Record<string, unknown>;
}

export type ItemChange<T extends CompactItem> =
  | { type: "set"; item: T }
  | {
      type: "append";
      id: string;
      field: "text" | "arguments";
      text: string;
      last_stream_id: string;
      state: T["state"];
      ended_at: string | null;
    };

const POSITION = /^(0|[1-9]\d*)-(0|[1-9]\d*)$/;
const compareCounter = (a: string, b: string) =>
  a.length - b.length || (a < b ? -1 : a > b ? 1 : 0);

export function compareDisplayPositions(a: string, b: string): number {
  const left = POSITION.exec(a),
    right = POSITION.exec(b);
  if (!left || !right) throw new Error("Invalid display position.");
  return (
    compareCounter(left[1]!, right[1]!) || compareCounter(left[2]!, right[2]!)
  );
}

/** False means the baseline is missing an item. Never partially apply a batch. */
export function applyItemChanges<T extends CompactItem>(
  items: Map<string, T>,
  changes: readonly ItemChange<T>[],
): boolean {
  const staged = new Map<string, T>();
  for (const change of changes) {
    const id = change.type === "set" ? change.item.id : change.id;
    const previous = staged.get(id) ?? items.get(id);
    const position =
      change.type === "set"
        ? change.item.last_stream_id
        : change.last_stream_id;
    if (
      previous &&
      compareDisplayPositions(position, previous.last_stream_id) <= 0
    )
      continue;
    if (change.type === "set") staged.set(id, structuredClone(change.item));
    else {
      if (!previous) return false;
      staged.set(id, {
        ...previous,
        state: change.state,
        last_stream_id: change.last_stream_id,
        ended_at: change.ended_at,
        content: {
          ...previous.content,
          [change.field]:
            String(previous.content[change.field] ?? "") + change.text,
        },
      });
    }
  }
  for (const [id, item] of staged) items.set(id, item);
  return true;
}
