import { applyItemChanges, compareDisplayPositions } from "a13n-ui/display";
import { isRecord, type Client, type ThreadDelta } from "../../service-client";
import { data, type Schema } from "../../shared/api";

export const AUTHORED_INPUT_EVENT_NAMES: ReadonlySet<string> = new Set([
  "a13n.input.user",
  "a13n.input.steering",
]);

/** Live and committed items use the same server-owned compact representation. */
export type DisplayItem = Schema["Item"];

/** Read the mutable tail and newest page, or a bounded window before/after an ordinal. */
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

export const comparePositions = compareDisplayPositions;

export function inDisplayOrder<T extends Pick<DisplayItem, "first_stream_id">>(
  items: Iterable<T>,
): T[] {
  return [...items].sort((a, b) =>
    comparePositions(a.first_stream_id, b.first_stream_id),
  );
}

/** The display omitted an observation's value beyond its size limit. */
export function isOmitted(content: unknown) {
  return (
    isRecord(content) &&
    content.omitted === true &&
    Object.keys(content).length === 1
  );
}

/** No native or AG-UI interpretation belongs in a browser. */
export function applyDelta(
  items: Map<string, DisplayItem>,
  delta: ThreadDelta,
) {
  return applyItemChanges(items, delta.changes);
}
