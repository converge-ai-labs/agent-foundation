import { applyEdits, format as jsonEdits } from "jsonc-parser";
import type { Asset } from "./assets";

export const TEXT_LIMIT = 512 * 1024;
export const MEDIA_LIMIT = 8 * 1024 * 1024;
const imageTypes = new Set([
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/gif",
  "image/avif",
  "image/bmp",
  "image/x-icon",
  "image/vnd.microsoft.icon",
]);
const codeLanguages: Record<string, string> = {
  bash: "bash",
  sh: "bash",
  c: "c",
  cpp: "cpp",
  cs: "csharp",
  css: "css",
  go: "go",
  java: "java",
  js: "javascript",
  jsx: "javascript",
  mjs: "javascript",
  php: "php",
  py: "python",
  rb: "ruby",
  rs: "rust",
  sql: "sql",
  toml: "toml",
  ts: "typescript",
  tsx: "typescript",
  xml: "xml",
  yaml: "yaml",
  yml: "yaml",
};
const genericTypes = new Set(["application/octet-stream", "text/plain", ""]);
const imageExtensions: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  webp: "image/webp",
  gif: "image/gif",
  avif: "image/avif",
  bmp: "image/bmp",
  ico: "image/x-icon",
};

export type PreviewFormat =
  | { kind: "image" | "pdf"; mime: string; limit: number }
  | {
      kind: "text" | "markdown" | "json" | "csv";
      language?: string;
      limit: number;
    };

export function previewFormat({
  name,
  content_type,
}: Pick<Asset, "name" | "content_type">): PreviewFormat | undefined {
  const mime = content_type.split(";", 1)[0]!.trim().toLowerCase();
  const extension = (
    name.includes(".") ? name.split(".").at(-1)! : ""
  ).toLowerCase();
  // Markup always takes the inert source path, even with an inconsistent MIME or extension.
  if (mime === "image/svg+xml" || extension === "svg" || extension === "svgz")
    return { kind: "text", language: "svg", limit: TEXT_LIMIT };
  if (
    mime === "text/html" ||
    mime === "application/xhtml+xml" ||
    ["htm", "html", "xhtml"].includes(extension)
  )
    return { kind: "text", language: "html", limit: TEXT_LIMIT };
  const fallback = genericTypes.has(mime);
  if (mime === "application/pdf" || (fallback && extension === "pdf"))
    return { kind: "pdf", mime: "application/pdf", limit: MEDIA_LIMIT };
  if (imageTypes.has(mime)) return { kind: "image", mime, limit: MEDIA_LIMIT };
  if (fallback && Object.hasOwn(imageExtensions, extension))
    return {
      kind: "image",
      mime: imageExtensions[extension]!,
      limit: MEDIA_LIMIT,
    };
  if (
    mime === "text/markdown" ||
    (fallback && ["md", "markdown"].includes(extension))
  )
    return { kind: "markdown", language: "markdown", limit: TEXT_LIMIT };
  if (
    mime === "text/csv" ||
    mime === "application/csv" ||
    (fallback && extension === "csv")
  )
    return { kind: "csv", language: "csv", limit: TEXT_LIMIT };
  if (
    mime === "application/json" ||
    mime.endsWith("+json") ||
    (fallback && extension === "json")
  )
    return { kind: "json", language: "json", limit: TEXT_LIMIT };
  const language = Object.hasOwn(codeLanguages, extension)
    ? codeLanguages[extension]
    : undefined;
  if (
    mime.startsWith("text/") ||
    mime === "application/xml" ||
    mime.endsWith("+xml") ||
    language ||
    (fallback && ["txt", "log"].includes(extension))
  )
    return { kind: "text", language, limit: TEXT_LIMIT };
}

export function formatJson(text: string) {
  try {
    // Validate with JSON.parse but retain exact number spellings and duplicate keys.
    JSON.parse(text);
    return formatJsonSource(text);
  } catch {
    return text;
  }
}

function formatJsonSource(text: string) {
  // Valid JSON was checked above. Skip quoted strings when budgeting whitespace.
  let depth = 0;
  let indentation = 0;
  for (const [token] of text.matchAll(/"(?:[^"\\]|\\[\s\S])*"|[^\s]/g)) {
    if (token === "{" || token === "[") depth++;
    if (token === "}" || token === "]") depth--;
    indentation += depth * 2;
    if (indentation > TEXT_LIMIT) return text;
  }
  const edits = jsonEdits(text, undefined, { insertSpaces: true, tabSize: 2 });
  // Deeply nested inputs can expand far beyond the size of the original file.
  if (
    text.length +
      edits.reduce(
        (size, edit) => size + edit.content.length - edit.length,
        0,
      ) >
    TEXT_LIMIT
  )
    return text;
  return applyEdits(text, edits);
}

/** CSV accepts CRLF, quoted separators/newlines and doubled quotes. Bound the table's DOM. */
export function csvPreview(text: string) {
  const rowLimit = 101;
  const columnLimit = 30;
  const rows: string[][] = [];
  let row: string[] = [],
    field = "",
    quoted = false,
    truncated = false;
  function endField() {
    if (row.length < columnLimit) row.push(field);
    else truncated = true;
    field = "";
  }
  for (let index = 0; index < text.length; index++) {
    if (rows.length === rowLimit) return { rows, truncated: true };
    const char = text[index]!;
    if (quoted && char === '"' && text[index + 1] === '"') {
      field += '"';
      index++;
    } else if (char === '"' && (quoted || !field)) {
      quoted = !quoted;
    } else if (!quoted && char === ",") {
      endField();
    } else if (!quoted && (char === "\r" || char === "\n")) {
      endField();
      rows.push(row);
      row = [];
      if (char === "\r" && text[index + 1] === "\n") index++;
    } else {
      field += char;
    }
  }
  if (field || row.length || (text && !/[\r\n]$/.test(text))) {
    endField();
    rows.push(row);
  }
  return { rows, truncated };
}
