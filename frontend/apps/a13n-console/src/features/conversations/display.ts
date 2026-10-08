import { DisplayNormalizer, type DisplayItem } from "a13n-ui/display";
import { isRecord, type Client, type ThreadDelta } from "../../service-client";
import { data } from "../../shared/api";

export type { DisplayItem } from "a13n-ui/display";
export const AUTHORED_INPUT_EVENT_NAMES: ReadonlySet<string> = new Set([
  "a13n.input.user",
  "a13n.input.steering",
]);

/** Read a committed display window; only the default window is a live baseline. */
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
  normalizer = new DisplayNormalizer(),
) {
  const previous = delta.item ? items.get(delta.item.id) : undefined;
  normalizer.apply(items, delta);
  const updated = delta.item ? items.get(delta.item.id) : undefined;
  if (!previous?.content_refs || !updated) return;
  const refs = { ...previous.content_refs };
  const appendField = {
    TEXT_MESSAGE_CONTENT: "text",
    REASONING_MESSAGE_CONTENT: "text",
    TOOL_CALL_ARGS: "arguments",
  }[String(delta.event.type)];
  for (const field of Object.keys(refs)) {
    const appendsObservation =
      field === "value" &&
      previous.content.name === "a13n.pydantic_ai.part_delta";
    if (field === appendField || appendsObservation) {
      // This is a preview of a committed prefix, not that prefix. Keep it intact until the next snapshot.
      if (field in previous.content)
        updated.content[field] = previous.content[field];
      else delete updated.content[field];
    } else if (updated.content[field] !== previous.content[field]) {
      delete refs[field];
    }
  }
  updated.content_refs = refs;
}
