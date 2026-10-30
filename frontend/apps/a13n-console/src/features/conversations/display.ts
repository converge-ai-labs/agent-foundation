import {
  DisplayNormalizer,
  type DisplayEvent,
  type DisplayItem,
} from "a13n-ui/display";
import { isRecord, type Client } from "../../service-client";
import { data, type Schema } from "../../shared/api";

export type { DisplayItem } from "a13n-ui/display";
export const AUTHORED_INPUT_EVENT_NAMES: ReadonlySet<string> = new Set([
  "a13n.input.user",
  "a13n.input.steering",
]);

/** Read a committed display window: by default the newest Items, always with the whole tail. */
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

export type RunDisplay = Schema["RunItems"] & {
  /** Items before this ordinal were read from immutable history pages. */
  immutableBefore: number;
};

/** Read the newest window immediately; refresh only history the reader already loaded. */
export async function readRunDisplay(
  client: Client,
  workspaceId: string,
  runId: string,
  signal?: AbortSignal,
  previous?: RunDisplay,
): Promise<RunDisplay> {
  const latest = await readDisplay(client, workspaceId, runId, signal);
  signal?.throwIfAborted();
  const first = latest.items[0]?.ordinal ?? 1;
  const start = Math.min(previous?.items[0]?.ordinal ?? first, first);
  // A former tail may have changed before becoming history. Reuse only the
  // contiguous prefix that was already immutable when read.
  const history =
    previous?.items.filter(
      (item) => item.ordinal < Math.min(first, previous.immutableBefore),
    ) ?? [];
  await fillHistory(client, workspaceId, runId, history, start, first, signal);
  return {
    ...latest,
    items: [...history, ...latest.items],
    immutableBefore: first,
  };
}

/** One older window per reader request. The live Run may already show more than the committed read. */
export async function readEarlierDisplay(
  client: Client,
  workspaceId: string,
  runId: string,
  current: RunDisplay,
  before: number,
  signal?: AbortSignal,
): Promise<RunDisplay> {
  const page = await readDisplay(client, workspaceId, runId, signal, {
    before,
    limit: 200,
  });
  signal?.throwIfAborted();
  const history = page.items;
  const first = history[0]?.ordinal;
  if (
    first !== Math.max(1, before - 200) ||
    history.at(-1)?.ordinal !== before - 1
  )
    throw new Error("Earlier run content could not be loaded.");
  for (let index = 0; index < history.length; index++)
    if (history[index]!.ordinal !== first + index)
      throw new Error("Earlier run content could not be loaded.");
  const currentFirst = current.items[0]?.ordinal ?? before;
  await fillHistory(
    client,
    workspaceId,
    runId,
    history,
    first,
    currentFirst,
    signal,
  );
  return {
    ...current,
    items: [
      ...history.filter((item) => item.ordinal < currentFirst),
      ...current.items,
    ],
  };
}

async function fillHistory(
  client: Client,
  workspaceId: string,
  runId: string,
  history: Schema["Item"][],
  start: number,
  before: number,
  signal?: AbortSignal,
) {
  let after = history.at(-1)?.ordinal ?? start - 1;
  while (after < before - 1) {
    signal?.throwIfAborted();
    const page = await readDisplay(client, workspaceId, runId, signal, {
      after,
      limit: Math.min(500, before - 1 - after),
    });
    if (!page.items.length) throw new Error("Run history could not be loaded.");
    for (const item of page.items) {
      if (item.ordinal !== after + 1 || item.ordinal >= before)
        throw new Error("Run history could not be loaded.");
      history.push(item);
      after = item.ordinal;
    }
  }
  signal?.throwIfAborted();
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
  delta: DisplayEvent,
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
