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
  arguments: string;
  result?: unknown;
  failure?: unknown;
  protectedReasoning: boolean;
  display?: boolean;
  /** `a13n.steering-source` presentation provenance for enqueued user content. */
  steeringSource?: string;
}

const text = (value: unknown) => (typeof value === "string" ? value : "");

export function presentItem(item: DisplayItem): PresentedItem {
  const { content } = item;
  const metadata = isRecord(content.metadata) ? content.metadata : {};
  const source = metadata["a13n.steering-source"];
  return {
    id: item.id,
    kind: item.kind,
    state: item.state,
    firstPosition: item.first_stream_id,
    lastPosition: item.last_stream_id,
    startedAt: item.started_at,
    endedAt: item.ended_at ?? null,
    text: text(content.text),
    role: text(content.role) || "assistant",
    toolName: text(content.toolCallName),
    arguments: text(content.arguments),
    result: content.result,
    failure: content.failure,
    protectedReasoning: "encrypted_value" in content,
    ...(typeof metadata.display === "boolean"
      ? { display: metadata.display }
      : {}),
    ...(typeof source === "string" ? { steeringSource: source } : {}),
  };
}

/** The display's messages and tool calls in order; observations are execution facts. */
export function presentItems(items: Iterable<DisplayItem>): PresentedItem[] {
  return inDisplayOrder(items)
    .filter((item) => item.kind !== "observation")
    .map(presentItem);
}

export function parseItemValue(value: unknown): unknown {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}
