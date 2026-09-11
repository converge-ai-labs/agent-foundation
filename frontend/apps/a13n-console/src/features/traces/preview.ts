import type { Schema } from "../../shared/api";

/** Decode JSON containers without losing large integers in retained content. */
export function decodeContent(value: unknown): unknown {
  if (typeof value !== "string" || !/^[\s]*[\[{]/.test(value)) return value;
  try {
    let unsafeNumber = false;
    const parsed: unknown = JSON.parse(value, (_key, item: unknown) => {
      if (
        typeof item === "number" &&
        (!Number.isFinite(item) ||
          (Number.isInteger(item) && !Number.isSafeInteger(item)))
      )
        unsafeNumber = true;
      return item;
    });
    return unsafeNumber ? value : parsed;
  } catch {
    return value;
  }
}

/** Small plain-text excerpts only: no Markdown, HTML, or remote media rendering. */
export function contentPreview(content: Schema["Content"] | null): string {
  if (content === null) return "-";
  let remaining = 240;
  let visited = 0;
  function excerpt(value: unknown, depth = 0): string {
    if (remaining <= 0 || depth > 8 || visited++ > 80) return "";
    if (typeof value === "string" && value.length <= 20_000) {
      const decoded = decodeContent(value);
      if (decoded !== value) return excerpt(decoded, depth + 1);
    }
    if (Array.isArray(value)) {
      if (value.length === 0) return "[]";
      const parts: string[] = [];
      for (const item of value) {
        if (remaining <= 0 || visited > 80) break;
        parts.push(excerpt(item, depth + 1));
      }
      return parts.filter(Boolean).join(" · ");
    }
    if (typeof value === "object" && value !== null) {
      for (const key of [
        "messages",
        "parts",
        "content",
        "text",
        "message",
        "choices",
      ]) {
        if (key in value)
          return excerpt((value as Record<string, unknown>)[key], depth + 1);
      }
    }
    const text = (
      typeof value === "string" ? value : (JSON.stringify(value) ?? "")
    )
      .slice(0, remaining + 1)
      .replace(/\s+/g, " ")
      .trim();
    const result = text.slice(0, remaining) || '""';
    remaining -= result.length;
    return result;
  }
  const text = excerpt(content.value);
  return text
    ? `${text.slice(0, 240)}${remaining <= 0 || text.length > 240 ? "…" : ""}`
    : "…";
}
