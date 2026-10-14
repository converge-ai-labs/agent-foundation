import type { TFunction } from "i18next";
import { UNKNOWN } from "../../shared/unknown";
import { percent } from "../usage/values";

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

/** When something happened within a Run, counted from the Run's start. */
export function formatOffset(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "";
  const seconds = Math.max(0, ms) / 1000;
  if (seconds < 60) return `+${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60)
    return `+${minutes}m${String(Math.floor(seconds % 60)).padStart(2, "0")}s`;
  return `+${Math.floor(minutes / 60)}h${String(minutes % 60).padStart(2, "0")}m`;
}

export function formatTokens(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value))
    return UNKNOWN;
  if (value >= 1_000_000)
    return `${(value / 1_000_000).toFixed(value < 10_000_000 ? 2 : 1).replace(/\.?0+$/, "")}M`;
  if (value >= 1000) return `${(value / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  return String(value);
}

/** Output tokens per second of generation; unknown when it was not timed. */
export function formatSpeed(t: TFunction, rate: number | null | undefined) {
  if (rate === null || rate === undefined || !Number.isFinite(rate))
    return UNKNOWN;
  return t("{{rate}} tok/s", {
    rate: rate < 10 ? rate.toFixed(1) : Math.round(rate).toLocaleString(),
  });
}

/**
 * A request's or Run's tokens as "input → output", and the cached share of its
 * input when the provider reported one. Exact counts go in the titles.
 */
export function tokenFigures(
  t: TFunction,
  { input, output, cached }: { input: number; output: number; cached: number },
) {
  const exact = (value: number) => value.toLocaleString();
  const rate = input > 0 && cached > 0 ? percent(cached / input) : undefined;
  return {
    pair: `${formatTokens(input)} → ${formatTokens(output)}`,
    pairTitle: t("Input {{input}} tokens, output {{output}} tokens", {
      input: exact(input),
      output: exact(output),
    }),
    /** The cached share alone, for a place already labelled Cache. */
    rate,
    cache: rate && t("{{rate}} cached", { rate }),
    cacheTitle: t("{{cached}} of {{input}} input tokens were cached", {
      cached: exact(cached),
      input: exact(input),
    }),
  };
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
