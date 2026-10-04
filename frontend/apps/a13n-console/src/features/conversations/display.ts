import { applyDisplayChanges, type DisplayItem } from "a13n-ui/display";
import { isRecord, type Client, type ThreadDelta } from "../../service-client";
import { data } from "../../shared/api";

export type { DisplayItem } from "a13n-ui/display";
export const AUTHORED_INPUT_EVENT_NAMES: ReadonlySet<string> = new Set([
  "a13n.input.user",
  "a13n.input.steering",
]);

/** Read a committed display window; Hosts, not clients, interpret source events. */
export function readDisplay(
  client: Client,
  workspaceId: string,
  runId: string,
  signal?: AbortSignal,
  window: { before?: number; after?: number; limit?: number } = {},
) {
  return client
    .workspace(workspaceId)
    .GET("/api/v1/runs/{run_id}/items", {
      params: { path: { run_id: runId }, query: window },
      signal,
    })
    .then(data);
}

const POSITION = /^(0|[1-9]\d*)-(0|[1-9]\d*)$/;

function compareCounters(a: string, b: string) {
  return a.length - b.length || (a < b ? -1 : a > b ? 1 : 0);
}

/** Orders two decimal `{attempt}-{sequence}` display positions without precision loss. */
export function comparePositions(a: string, b: string): number {
  const left = POSITION.exec(a),
    right = POSITION.exec(b);
  if (!left || !right) throw new Error("Invalid display position.");
  return (
    compareCounters(left[1]!, right[1]!) || compareCounters(left[2]!, right[2]!)
  );
}

export function inDisplayOrder<T extends Pick<DisplayItem, "first_stream_id">>(
  items: Iterable<T>,
): T[] {
  return [...items].sort((a, b) =>
    comparePositions(a.first_stream_id, b.first_stream_id),
  );
}

export function isOmitted(content: unknown) {
  return (
    isRecord(content) &&
    content.omitted === true &&
    Object.keys(content).length === 1
  );
}

export function applyDelta(
  items: Map<string, DisplayItem>,
  delta: ThreadDelta,
) {
  applyDisplayChanges(items, delta.changes);
}
