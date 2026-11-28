import { readContentParts } from "a13n-ui";
import {
  readToolProgress,
  toolExecutionReceipt,
  type ToolProgress,
} from "a13n-ui/display";
import type { ContentPart } from "@ag-ui/core";
import { isRecord } from "../../service-client";
import { inDisplayOrder, type DisplayItem } from "./display";

/** A message or tool call of the display, as the transcript reads it. */
export interface PresentedItem {
  id: string;
  kind: string;
  state: string;
  firstPosition: string;
  lastPosition: string;
  /**
   * When the Item's first event occurred, and the event that gave it a
   * completed or failed state. A live event that carried no time leaves them
   * null, and `endedAt` stays null for an Item that never finished.
   */
  startedAt: string | null;
  endedAt: string | null;
  text: string;
  role: string;
  toolName: string;
  toolCallId?: string;
  progress?: ToolProgress;
  publication?: PublishedAsset;
  arguments: string;
  incomplete?: boolean;
  result?: unknown;
  resultParts?: ContentPart[];
  subagentRunId?: string;
  failure?: unknown;
  protectedReasoning: boolean;
  display?: boolean;
  /** `a13n.steering-source` presentation provenance for enqueued user content. */
  steeringSource?: string;
  sourceId?: string;
  inputSource?: string;
  realtimeAssetIds?: string[];
  speech?: boolean;
}

export interface PublishedAsset {
  assetId: string;
  toolCallId: string;
}

export function isAssetId(value: unknown): value is string {
  return typeof value === "string" && /^ast_[a-zA-Z0-9]+$/.test(value);
}

export function publishedAsset(item: DisplayItem): PublishedAsset | undefined {
  const { content } = item;
  if (
    item.kind !== "observation" ||
    content.name !== "a13n.harness.tool.asset_published" ||
    !isRecord(content.value) ||
    !isRecord(content.value.event)
  )
    return;
  const { payload } = content.value.event;
  if (
    isRecord(payload) &&
    payload.tool_id === "service.publish_asset" &&
    isRecord(payload.value) &&
    isAssetId(payload.value.asset_id) &&
    typeof payload.tool_call_id === "string" &&
    payload.tool_call_id
  )
    return {
      assetId: payload.value.asset_id,
      toolCallId: payload.tool_call_id,
    };
}

const text = (value: unknown) => (typeof value === "string" ? value : "");

export function presentItem(item: DisplayItem): PresentedItem {
  const { content } = item;
  const metadata = isRecord(content.metadata) ? content.metadata : {};
  const source = metadata["a13n.steering-source"];
  const sourceId = text(metadata.source_id);
  return {
    id: item.id,
    kind: item.kind,
    state:
      item.kind === "tool_call" && toolExecutionReceipt(content.result)
        ? "handed_off"
        : item.kind === "tool_call" &&
            (content.outcome === "expired" || content.outcome === "interrupted")
          ? content.outcome
          : item.state,
    firstPosition: item.first_stream_id,
    lastPosition: item.last_stream_id,
    startedAt: item.started_at,
    endedAt: item.ended_at ?? null,
    text: text(content.text),
    role: text(content.role) || "assistant",
    toolName: text(content.toolCallName),
    toolCallId: content.provider
      ? undefined
      : text(content.toolCallId) || undefined,
    progress:
      item.state === "in_progress"
        ? readToolProgress(content.progress)
        : undefined,
    arguments: text(content.arguments),
    ...(content.incomplete === true || item.content_refs?.arguments
      ? { incomplete: true }
      : {}),
    result: content.result,
    resultParts: readContentParts(content.result_parts),
    subagentRunId: text(content.subagentRunId) || undefined,
    failure: content.failure,
    // Harness input groups are not Service entry IDs. Service authorship lives in metadata.
    sourceId: sourceId && sourceId.length <= 72 ? sourceId : undefined,
    inputSource: text(content.input_source) || undefined,
    ...(Array.isArray(metadata.realtime_asset_ids)
      ? { realtimeAssetIds: metadata.realtime_asset_ids.filter(isAssetId) }
      : {}),
    // Older realtime runs retained only the synthetic message ID.
    speech:
      content.speech === true ||
      /^run-[\w-]+:speech:\d+$/.test(text(content.messageId)) ||
      undefined,
    protectedReasoning: "encrypted_value" in content,
    ...(typeof metadata.display === "boolean"
      ? { display: metadata.display }
      : {}),
    ...(typeof source === "string" ? { steeringSource: source } : {}),
  };
}

/** The display's messages and tool calls in order; observations are execution facts. */
export function presentItems(items: Iterable<DisplayItem>): PresentedItem[] {
  return inDisplayOrder(items).flatMap((item) => {
    if (item.kind !== "observation") return [presentItem(item)];
    const publication = publishedAsset(item);
    return publication ? [{ ...presentItem(item), publication }] : [];
  });
}

export function parseItemValue(value: unknown, incomplete = false): unknown {
  if (incomplete || typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}
