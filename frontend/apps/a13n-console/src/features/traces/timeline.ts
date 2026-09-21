import type { Schema } from "../../shared/api";
export interface TimelineRow {
  observation: Schema["Observation"];
  depth: number;
  /** Children placed directly beneath this row in the loaded call tree. */
  childCount: number;
}
/** Preserve parent topology, including orphaned or cyclic backend observations, exactly once. */
export function observationRows(
  observations: readonly Schema["Observation"][],
): TimelineRow[] {
  const ids = new Set(observations.map((item) => item.id)),
    children = new Map<string, Schema["Observation"][]>();
  const ordered = [...observations].sort(
    (a, b) =>
      a.started_at.localeCompare(b.started_at) || a.id.localeCompare(b.id),
  );
  for (const observation of ordered)
    if (observation.parent_id && ids.has(observation.parent_id)) {
      const siblings = children.get(observation.parent_id) ?? [];
      siblings.push(observation);
      children.set(observation.parent_id, siblings);
    }
  const roots = ordered.filter(
    (item) => !item.parent_id || !ids.has(item.parent_id),
  );
  const rows: TimelineRow[] = [],
    visited = new Set<string>();
  for (const root of [...roots, ...ordered]) {
    const stack: TimelineRow[] = [
      { observation: root, depth: 0, childCount: 0 },
    ];
    while (stack.length) {
      const row = stack.pop()!;
      if (visited.has(row.observation.id)) continue;
      visited.add(row.observation.id);
      rows.push(row);
      const descendants = children.get(row.observation.id) ?? [];
      for (let index = descendants.length - 1; index >= 0; index--)
        stack.push({
          observation: descendants[index]!,
          depth: row.depth + 1,
          childCount: 0,
        });
    }
  }
  // Count the children actually placed beneath a row, never the cycles skipped above.
  const ancestors: TimelineRow[] = [];
  for (const row of rows) {
    while (ancestors.length && ancestors.at(-1)!.depth >= row.depth)
      ancestors.pop();
    const parent = ancestors.at(-1);
    if (parent) parent.childCount++;
    ancestors.push(row);
  }
  return rows;
}
/** Hide the subtree of every collapsed row; depth-first order keeps it contiguous. */
export function visibleRows(
  rows: readonly TimelineRow[],
  collapsed: ReadonlySet<string>,
): TimelineRow[] {
  const visible: TimelineRow[] = [];
  let hiddenBelow: number | null = null;
  for (const row of rows) {
    if (hiddenBelow !== null) {
      if (row.depth > hiddenBelow) continue;
      hiddenBelow = null;
    }
    visible.push(row);
    if (row.childCount > 0 && collapsed.has(row.observation.id))
      hiddenBelow = row.depth;
  }
  return visible;
}
