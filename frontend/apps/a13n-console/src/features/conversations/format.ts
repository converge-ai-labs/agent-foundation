import { UNKNOWN } from "../../shared/unknown";

/**
 * Presentation helpers for the Run timeline. Values the Service never reported
 * render as the shared unknown mark rather than a zero. Cost formatting stays
 * in the shared decimal helper so Traces and Sessions agree.
 */

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return UNKNOWN;
  if (ms < 1000) return `${Math.max(0, Math.round(ms))}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1).replace(/\.0$/, "")}s`;
  const minutes = Math.floor(ms / 60_000);
  return `${minutes}m ${Math.round((ms % 60_000) / 1000)}s`;
}

export function formatTokens(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value))
    return UNKNOWN;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1000) return `${(value / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  return String(value);
}

/** One flattened line of any retained value, cut at `max` characters. */
export function resultExcerpt(value: unknown, max = 120): string {
  if (value === undefined || value === null) return "";
  const text =
    typeof value === "string" ? value : (JSON.stringify(value) ?? "");
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

/** Argument names Toolsets use for the subject of a call, most specific first. */
const SUBJECT_KEYS = [
  "path",
  "file_path",
  "command",
  "query",
  "task",
  "prompt",
  "url",
  "pattern",
  "name",
];

/** The one argument worth showing on a collapsed tool row. */
export function primaryArgument(args: unknown): string {
  if (typeof args === "string") return resultExcerpt(args, 80);
  if (typeof args !== "object" || args === null || Array.isArray(args))
    return resultExcerpt(args, 80);
  const record = args as Record<string, unknown>;
  for (const key of SUBJECT_KEYS) {
    const value = record[key];
    if (typeof value === "string" && value) return resultExcerpt(value, 80);
  }
  const first = Object.values(record).find(
    (value) => value !== undefined && value !== null && value !== "",
  );
  return resultExcerpt(first, 80);
}
