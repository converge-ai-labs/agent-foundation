import type { ActionEntry, ContentEntry, TimelineEntry } from "../timeline";
import { workState, type WorkState } from "./entry-language";

/**
 * One Chat reading of the same Run timeline the Debug level renders: the
 * agent's prose is its own block, guidance the reader sent mid-run is its own
 * block, and everything else joins the surrounding work block that the work
 * line summarizes. Chat and Debug therefore never disagree on what happened.
 */
export type TranscriptBlock =
  | { kind: "message"; id: string; entry: ContentEntry }
  | { kind: "guidance"; id: string; entry: ContentEntry }
  | { kind: "work"; id: string; entries: WorkEntry[] };

/** One compact row: the timeline entry itself, read against its own Run. */
export interface WorkEntry {
  id: string;
  /** Reasoning arrives as content; everything else is a step that ran. */
  entry: ActionEntry | ContentEntry;
  state: WorkState;
  /** Observed wall clock; null for a step read from a retained snapshot. */
  startedMs: number | null;
  endedMs: number | null;
}

export function transcriptBlocks(
  entries: readonly TimelineEntry[],
  runState?: string,
): TranscriptBlock[] {
  const blocks: TranscriptBlock[] = [];
  for (const entry of flatten(entries)) {
    if (entry.kind === "reply") {
      blocks.push({ kind: "message", id: entry.id, entry });
      continue;
    }
    if (entry.kind === "guidance") {
      blocks.push({ kind: "guidance", id: entry.id, entry });
      continue;
    }
    const work = workEntry(entry, runState);
    if (!work) continue;
    const last = blocks.at(-1);
    if (last?.kind === "work") last.entries.push(work);
    else blocks.push({ kind: "work", id: work.id, entries: [work] });
  }
  return blocks;
}

/**
 * Model requests are the Debug skeleton; Chat reads what they emitted. Nested
 * calls are work in their own right and keep their place in the order.
 */
function flatten(entries: readonly TimelineEntry[]): TimelineEntry[] {
  return entries.flatMap((entry) =>
    entry.kind === "model"
      ? flatten(entry.children)
      : "children" in entry && entry.children.length
        ? [entry, ...flatten(entry.children)]
        : [entry],
  );
}

const instant = (value: string | null) => {
  const ms = value ? Date.parse(value) : Number.NaN;
  return Number.isFinite(ms) ? ms : null;
};

function workEntry(entry: TimelineEntry, runState?: string): WorkEntry | null {
  const included =
    entry.kind === "reasoning" ? entry : "arguments" in entry ? entry : null;
  if (!included) return null;
  return {
    id: included.id,
    entry: included,
    state:
      included.kind === "reasoning"
        ? "done"
        : workState(included.state, runState),
    startedMs: instant(included.startedAt),
    endedMs: instant(included.endedAt),
  };
}

/** Reasoning counts toward the work line but never names it. */
const isStep = (work: WorkEntry) => work.entry.kind !== "reasoning";

export interface WorkSummary {
  /** The steps the line names, in observation order. */
  named: WorkEntry[];
  /** Everything beyond them, reasoning included. */
  more: number;
  /** Wall time from the first step to the last one that finished. */
  durationMs: number | null;
  /** The one step the reader is being asked about, when there is one. */
  waiting: ActionEntry | null;
  running: boolean;
  failed: boolean;
  /** A step the run never resolved: the line must not read as success. */
  interrupted: boolean;
}

export function workSummary(
  entries: readonly WorkEntry[],
  limit = 3,
): WorkSummary {
  const named = entries.filter(isStep).slice(0, limit);
  const started = entries
    .map((entry) => entry.startedMs)
    .filter((value): value is number => value !== null);
  const ended = entries
    .map((entry) => entry.endedMs)
    .filter((value): value is number => value !== null);
  const waiting = entries.find(
    (work): work is WorkEntry & { entry: ActionEntry } =>
      work.state === "waiting" && "arguments" in work.entry,
  );
  return {
    named,
    more: entries.length - named.length,
    durationMs:
      started.length && ended.length
        ? Math.max(0, Math.max(...ended) - Math.min(...started))
        : null,
    waiting: waiting?.entry ?? null,
    running: entries.some((entry) => entry.state === "working"),
    failed: entries.some((entry) => entry.state === "failed"),
    interrupted: entries.some((entry) => entry.state === "interrupted"),
  };
}

/** Whether this run's work line needs a child thread to link to. */
export function hasDelegation(blocks: readonly TranscriptBlock[]): boolean {
  return blocks.some(
    (block) =>
      block.kind === "work" &&
      block.entries.some((work) => work.entry.kind === "subagent"),
  );
}
