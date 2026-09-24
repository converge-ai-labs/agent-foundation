import type { Schema } from "../../shared/api";
import { isRecord } from "../../service-client";

/**
 * There is no retry operation: running the same input again is submitting its
 * message again. A resubmission carries what the message chose, so the new Run
 * starts as the old one did.
 */
export type Resubmission = Pick<
  Schema["Message"],
  "payload" | "agent_revision_id" | "options"
>;

/**
 * What a stopped Run was asked, ready to send again: its message, the options
 * it ran with and, when the message pinned one, its revision. Only a Run a
 * message started can be resubmitted.
 */
export function runResubmission(run: Schema["RunView"]): Resubmission | null {
  if (run.trigger !== "input" && run.trigger !== "queued") return null;
  const payload = messagePayload(run.input);
  return (
    payload && {
      payload,
      agent_revision_id:
        run.revision_selection === "pinned" ? run.agent_revision_id : null,
      options: run.options,
    }
  );
}

/** A pending message, ready to send again as the message it is. */
export function entryResubmission(
  entry: Schema["EntryView"],
): (Resubmission & Pick<Schema["Message"], "agent_id" | "delivery">) | null {
  const payload = entry.kind === "message" && messagePayload(entry.payload);
  return payload && entry.agent_id
    ? {
        payload,
        agent_id: entry.agent_id,
        agent_revision_id: entry.agent_revision_id,
        delivery: entry.delivery,
        options: entry.options,
      }
    : null;
}

/** A stored message's payload as it is submitted, or null for any other value. */
export function messagePayload(
  input: unknown,
): Schema["MessagePayload"] | null {
  if (!isRecord(input) || !Array.isArray(input.content)) return null;
  const content = input.content.flatMap((part): Schema["Part"][] => {
    if (!isRecord(part)) return [];
    if (part.type === "text" && typeof part.text === "string")
      return [{ type: "text", text: part.text }];
    if (part.type === "asset" && typeof part.asset_id === "string")
      return [{ type: "asset", asset_id: part.asset_id }];
    if (part.type === "url" && typeof part.url === "string")
      return [{ type: "url", url: part.url }];
    if (part.type === "json" && "value" in part)
      return [{ type: "json", value: part.value }];
    return [];
  });
  return content.length && content.length === input.content.length
    ? { content }
    : null;
}
