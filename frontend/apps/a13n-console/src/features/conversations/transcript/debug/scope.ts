import type { Schema } from "../../../../shared/api";

/** What every timeline row of one Run needs to know about that Run. */
export interface RunScope {
  run: Schema["RunResource"];
  /** Start of the Run's measured span and its length, for the time bars. */
  start: number;
  span: number;
  /** Where an asynchronous delegation's child Thread can be opened, if known. */
  child: { path: string; runs: number } | null;
  /** The Run is waiting and this Console may answer it. */
  answerable: boolean;
  /** Move the reader to the dock, where a waiting Run is answered. */
  jumpToDock: () => void;
}

/** Fraction of the Run's span, clamped so every bar stays visible. */
export function bar(
  scope: RunScope,
  startedAt: string | null,
  ms: number | null,
) {
  // An entry that never reported a duration has no span to draw.
  if (!scope.span || !startedAt || !ms) return null;
  const offset = (Date.parse(startedAt) - scope.start) / scope.span;
  if (!Number.isFinite(offset)) return null;
  const width = ms && ms > 0 ? ms / scope.span : 0;
  const left = Math.min(Math.max(offset, 0), 1);
  return {
    left: left * 100,
    width: Math.min(Math.max(width, 0.04), 1 - left) * 100,
  };
}
