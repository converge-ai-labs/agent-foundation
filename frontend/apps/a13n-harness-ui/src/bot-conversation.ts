import type { Schema } from "./transport/client";
import type { DisplayBlock } from "./conversations/stream";

type Message = {
  message_id: string;
  role: "user" | "assistant";
  text: string;
  created_at: string;
};

// Native display identity joins the saved prefix, replayed suffix and final publication.
// Intermediate prose stays presentation data; only publications own notifications.
export function botConversation(
  messages: Schema<"BotMessage">[],
  prose: Schema<"BotProse">[],
  blocks: DisplayBlock[],
): Message[] {
  const published = new Set(
    messages.flatMap((message) => message.display_ids ?? []),
  );
  const rows = new Map<string, Message>();
  for (const item of prose) {
    // Legacy publications have no display correlation. Their completed native
    // responses already have a durable final publication.
    if (
      published.has(item.item_id) ||
      (item.final &&
        messages.some(
          (message) =>
            message.role === "assistant" &&
            !message.display_ids?.length &&
            message.text === item.text,
        ))
    )
      continue;
    rows.set(item.item_id, {
      message_id: item.item_id,
      role: "assistant",
      text: item.text,
      created_at: item.created_at,
    });
  }
  for (const block of blocks) {
    if (
      block.kind !== "assistant" ||
      block.subagentRunId ||
      block.context ||
      block.diagnostic ||
      block.metadata?.display === false ||
      !block.text.trim() ||
      published.has(block.id)
    )
      continue;
    rows.set(block.id, {
      message_id: block.id,
      role: "assistant",
      text: block.text,
      created_at:
        block.timestamp === undefined
          ? ""
          : new Date(block.timestamp).toISOString(),
    });
  }
  for (const message of messages) rows.set(message.message_id, message);
  return [...rows.values()].sort((a, b) =>
    a.created_at.localeCompare(b.created_at),
  );
}
