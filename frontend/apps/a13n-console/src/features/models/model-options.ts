export function apiLabel(value: string) {
  return (
    (
      {
        "openai.chat_completions": "Chat Completions",
        "openrouter.chat_completions": "Chat Completions",
        "openai.responses": "Responses",
        "anthropic.messages": "Messages",
        "google.generate_content": "Generate Content",
      } as Record<string, string>
    )[value] ?? value
  );
}
export function suggestedKey(value: string) {
  return value
    .normalize("NFC")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}._/\-]+/gu, "-")
    .slice(0, 128)
    .replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, "");
}
