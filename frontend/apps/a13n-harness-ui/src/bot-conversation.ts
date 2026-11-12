import type { Schema } from "./transport/client";
import type { DisplayBlock } from "./conversations/stream";

type Message = {
  message_id: string;
  role: "user" | "assistant";
  text: string;
  parts?: Schema<"BotMessage">["parts"];
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
  // A final publication replaces its correlated prose at that prose's original
  // position, not at completion time (which can be after a later human steer).
  const timestamps = new Map(
    prose.map((item) => [item.item_id, item.created_at]),
  );
  for (const block of blocks)
    if (block.timestamp !== undefined)
      timestamps.set(block.id, new Date(block.timestamp).toISOString());
  const position = new Map<string, string>();
  for (const message of messages) {
    const anchors = (message.display_ids ?? []).flatMap((id) => {
      const time = timestamps.get(id);
      return time ? [time] : [];
    });
    position.set(
      message.message_id,
      anchors.sort((a, b) => Date.parse(a) - Date.parse(b))[0] ??
        message.created_at,
    );
    rows.set(message.message_id, message);
  }
  return [...rows.values()].sort((a, b) => {
    const left = position.get(a.message_id) ?? a.created_at;
    const right = position.get(b.message_id) ?? b.created_at;
    // Untimed live blocks follow the saved prefix, never jump to the top.
    if (!left) return right ? 1 : 0;
    if (!right) return -1;
    return Date.parse(left) - Date.parse(right);
  });
}
