import type { DisplayBlock, DisplaySnapshot } from "a13n-ui/display";
import { isRecord, type Client } from "../../service-client";
import { data } from "../../shared/api";

export const AUTHORED_INPUT_EVENT_NAMES = new Set([
  "a13n.input.user",
  "a13n.input.steering",
]);

/** Presentation-only adapter; all incremental semantics live in a13n-ui/display. */
export interface DisplayItem {
  id: string;
  scope_id?: string;
  kind: "text_message" | "reasoning_message" | "tool_call" | "observation";
  state: "in_progress" | "completed" | "interrupted" | "failed";
  first_stream_id: string;
  last_stream_id: string;
  started_at: string | null;
  ended_at?: string | null;
  content: Record<string, unknown>;
}

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
function compareCounters(a: string, b: string) {
  return a.length - b.length || (a < b ? -1 : a > b ? 1 : 0);
}
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
    (content.omitted === true || content.truncated === true)
  );
}

function item(
  block: DisplayBlock,
  index: number,
  sealed: boolean,
): DisplayItem {
  const kind =
    block.kind === "tool_chunk"
      ? "tool_call"
      : block.kind === "reasoning"
        ? "reasoning_message"
        : block.kind === "extension"
          ? "observation"
          : "text_message";
  const content: Record<string, unknown> = { ...block.content };
  if (block.kind === "input") content.role = "user";
  if (block.kind === "tool_chunk") {
    content.toolCallId = content.tool_call_id;
    content.toolCallName = content.name;
    content.result_parts = content.content_parts;
    if (content.result === null) delete content.result;
  }
  if (block.kind === "reasoning" && content.signature)
    content.encrypted_value = content.signature;
  if (block.kind === "media" && !content.text) {
    const media = isRecord(content.media) ? content.media : {};
    content.text = `[${String(media.media_type ?? media.type ?? "Media")}]`;
  }
  if (block.kind === "extension" && typeof content.event_kind === "string")
    content.name = `a13n.harness.${content.event_kind}`;
  if (
    block.kind === "extension" &&
    isRecord(content.value) &&
    String(content.name).startsWith("a13n.display.")
  )
    content.value = { ...content.value, status: block.status };
  const state =
    block.status === "succeeded"
      ? "completed"
      : block.status === "failed"
        ? "failed"
        : ["cancelled", "unknown"].includes(block.status) || sealed
          ? "interrupted"
          : "in_progress";
  // Local presentation ordinals, never transport coverage or invented timestamps.
  const order = `0-${index}`;
  return {
    id: block.id,
    scope_id: block.scope_id,
    kind,
    state,
    first_stream_id: order,
    last_stream_id: order,
    started_at: null,
    ended_at: null,
    content,
  };
}

export function displayItems(
  snapshot: DisplaySnapshot,
  sealed = false,
): Map<string, DisplayItem> {
  const result = new Map(
    snapshot.blocks.map((block, index) => [
      block.id,
      item(block, index, sealed),
    ]),
  );
  for (const scope of snapshot.scopes) {
    if (!scope.parent_scope_id || !scope.parent_tool_call_id) continue;
    const parent = result.get(
      `${scope.parent_scope_id}:tool:${scope.parent_tool_call_id}`,
    );
    if (parent) parent.content.inline_scope = scope;
  }
  return result;
}
