import { isObject } from "./projection";

export function inputText(input: unknown, fallback?: string | null): string {
  const ordinary =
    isObject(input) && isObject(input.input) ? input.input : input;
  if (!isObject(ordinary) || !Array.isArray(ordinary.content))
    return fallback ?? "";
  return (
    ordinary.content
      .flatMap((block) =>
        isObject(block) &&
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
