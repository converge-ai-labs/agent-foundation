export const BOT_THREAD_ID = "thread_bot";

export function botInputId(metadata?: Record<string, unknown> | null) {
  const harness = metadata?.harness_ui;
  if (!harness || typeof harness !== "object") return;
  const id = (harness as Record<string, unknown>).bot_input_id;
  return typeof id === "string" ? id : undefined;
}
