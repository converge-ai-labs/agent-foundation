import { isRecord, type Client, type ThreadDelta } from "../../service-client";
import { data, type Schema } from "../../shared/api";

export const AUTHORED_INPUT_EVENT_NAMES: ReadonlySet<string> = new Set([
  "a13n.input.user",
  "a13n.input.steering",
]);

/**
 * One item of a Run's display: a committed item as the Service returned it, or
 * one the thread stream changed since. A live event that carried no time
 * leaves its item's times unknown.
 */
export type DisplayItem = Omit<Schema["Item"], "started_at" | "ended_at"> & {
  started_at: string | null;
  ended_at?: string | null;
};

/** The whole committed display of a Run, with the Run it describes. */
export function readDisplay(
  client: Client,
  workspaceId: string,
  runId: string,
  signal: AbortSignal,
) {
  return client
    .workspace(workspaceId)
    .GET("/api/v1/runs/{run_id}/items", {
      params: { path: { run_id: runId } },
      signal,
    })
    .then(data);
}

const POSITION = /^(0|[1-9]\d*)-(0|[1-9]\d*)$/;

/** Orders two decimal counters of any size, written without leading zeros. */
function compareCounters(a: string, b: string) {
  return a.length - b.length || (a < b ? -1 : a > b ? 1 : 0);
}

/** Orders two `{attempt}-{sequence}` display positions by their sign. */
export function comparePositions(a: string, b: string): number {
  const left = POSITION.exec(a),
    right = POSITION.exec(b);
  if (!left || !right) throw new Error("Invalid display position.");
  return (
    compareCounters(left[1]!, right[1]!) || compareCounters(left[2]!, right[2]!)
  );
}

/** Items in the order they first appeared. */
export function inDisplayOrder<T extends Pick<DisplayItem, "first_stream_id">>(
  items: Iterable<T>,
): T[] {
  return [...items].sort((a, b) =>
    comparePositions(a.first_stream_id, b.first_stream_id),
  );
}

/** A transport fragment of a large CUSTOM event; the display keeps its observation. */
export function isFragment(delta: ThreadDelta) {
  return (
    delta.event.type === "CUSTOM" && delta.event.name === "a13n.stream.fragment"
  );
}

/** The display dropped the content of its oldest Items to stay within its limit. */
export function isOmitted(content: unknown) {
  return (
    isRecord(content) &&
    content.omitted === true &&
    Object.keys(content).length === 1
  );
}

/** Fields the display copies from the AG-UI events of a message or tool call. */
const COPIED = [
  "messageId",
  "role",
  "toolCallId",
  "toolCallName",
  "parentMessageId",
  "metadata",
];
/** Events that append their `delta` to one content field. */
const ACCUMULATED: Record<string, string | undefined> = {
  TEXT_MESSAGE_CONTENT: "text",
  REASONING_MESSAGE_CONTENT: "text",
  TOOL_CALL_ARGS: "arguments",
};

/**
 * Fold one live delta into the display item it changed, the way the Service
 * folds the same event into the display it commits: the delta names the item
 * and its state after the event, and the event supplies the content. A delta
 * at or before the item's latest position is already folded.
 */
export function applyDelta(
  items: Map<string, DisplayItem>,
  delta: ThreadDelta,
) {
  const { item: ref, event } = delta;
  if (!ref) return;
  const position = `${delta.attempt}-${delta.sequence}`;
  const at =
    typeof event.timestamp === "number"
      ? new Date(event.timestamp).toISOString()
      : null;
  // A tool result the Harness did not present fails its call: the Service
  // keeps the observation and names only the call in the delta, so the
  // observation is held under its position until the display replaces it.
  if (
    event.type === "CUSTOM" &&
    ref.kind !== "observation" &&
    !inputText(event)
  )
    put(
      items,
      { id: position, kind: "observation", state: "completed" },
      position,
      at,
      event,
    );
  put(items, ref, position, at, event);
}

function put(
  items: Map<string, DisplayItem>,
  ref: NonNullable<ThreadDelta["item"]>,
  position: string,
  at: string | null,
  event: ThreadDelta["event"],
) {
  const previous = items.get(ref.id);
  if (previous && comparePositions(position, previous.last_stream_id) <= 0)
    return;
  const started = previous ? previous.started_at : at;
  const finished = ref.state === "completed" || ref.state === "failed";
  items.set(ref.id, {
    id: ref.id,
    kind: ref.kind,
    state: ref.state,
    first_stream_id: previous?.first_stream_id ?? position,
    last_stream_id: position,
    started_at: started,
    // An observation is timed by its first event.
    ended_at: finished ? (ref.kind === "observation" ? started : at) : null,
    content: content(ref.kind, event, previous?.content),
  });
}

function content(
  kind: Schema["ItemKind"],
  event: ThreadDelta["event"],
  previous: Schema["Item"]["content"] = {},
): Schema["Item"]["content"] {
  if (event.type === "CUSTOM") {
    const input = inputText(event);
    if (kind === "text_message" && input) {
      const text = input.content.slice(0, 262144);
      return {
        messageId: event.message_id,
        role: "user",
        text,
        ...(isRecord(event.metadata) ? { metadata: event.metadata } : {}),
        ...(input.content.length > text.length ? { truncated: true } : {}),
      };
    }
    // The observation that failed a tool call leaves the call's content as it was.
    if (kind !== "observation") return previous;
    if (!("name" in previous)) return { name: event.name, value: event.value };
    // A repeated observation continues its streamed tool-call arguments.
    const held = argumentStream(previous.value);
    const next = argumentStream(event.value);
    if (!held || !next) return previous;
    return {
      ...previous,
      value: {
        ...held.value,
        event: {
          ...held.event,
          delta: { ...held.delta, args_delta: held.text + next.text },
        },
      },
    };
  }
  const next = { ...previous };
  for (const field of COPIED) if (field in event) next[field] = event[field];
  const accumulated = ACCUMULATED[event.type];
  if (accumulated)
    next[accumulated] = String(next[accumulated] ?? "") + String(event.delta);
  if (event.type === "REASONING_ENCRYPTED_VALUE")
    next.encrypted_value = event.encryptedValue;
  if (event.type === "TOOL_CALL_RESULT")
    next.result = String(event.content ?? "");
  return next;
}

/** Authored input text, distinct from lifecycle and generated input observations. */
function inputText(event: ThreadDelta["event"]) {
  if (
    event.type !== "CUSTOM" ||
    typeof event.name !== "string" ||
    !AUTHORED_INPUT_EVENT_NAMES.has(event.name) ||
    !isRecord(event.value) ||
    !isRecord(event.value.event) ||
    typeof event.value.event.content !== "string"
  )
    return null;
  return event.value.event as Record<string, unknown> & { content: string };
}

/** A streamed tool-call argument observation and the text it holds so far. */
function argumentStream(value: unknown) {
  if (!isRecord(value)) return null;
  const { event } = value;
  if (!isRecord(event)) return null;
  const { delta } = event;
  if (!isRecord(delta) || typeof delta.args_delta !== "string") return null;
  return { value, event, delta, text: delta.args_delta };
}
