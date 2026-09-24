import { isRecord } from "../../service-client";

export function inputText(input: unknown, fallback?: string | null): string {
  if (!isRecord(input) || !Array.isArray(input.content)) return fallback ?? "";
  return (
    input.content
      .flatMap((block) =>
        isRecord(block) &&
        block.type === "text" &&
        typeof block.text === "string"
          ? [block.text]
          : [],
      )
      .join("\n\n") ||
    fallback ||
    ""
  );
}
