import { DisplayNormalizer } from "a13n-ui/display";
import type { RunSnapshot, SessionDelta } from "../../service-client";
import { applyDelta, isOmitted, type DisplayItem } from "./display";

/** What a session stream shows of one run it follows. */
export interface HeldDisplay {
  /** Items from ordinal `first` on, in ordinal order; earlier ones are read on request. */
  items: DisplayItem[];
  first: number;
  /** The run's final display. */
  complete: boolean;
  /** Output is missing: lost live output, an incomplete fold or content the display omitted. */
  partial: boolean;
  /** Content the saved display omitted. */
  omitted: boolean;
}

interface Held {
  items: Map<string, DisplayItem>;
  first: number;
  normalizer: DisplayNormalizer;
  complete: boolean;
  /** Live output was lost; it resumes with the run's next items. */
  gap: boolean;
  /** A delta failed to apply here, so items may differ from the committed ones. */
  diverged: boolean;
}

const byOrdinal = (a: DisplayItem, b: DisplayItem) => a.ordinal - b.ordinal;

/**
 * The displays of the runs one session stream follows. A run's `items`
 * replace what is held from their first ordinal on, its deltas change them
 * after that, and a gap pauses them until its next `items`. Subscribers hear
 * of a run's changes at most once per animation frame.
 */
export class SessionDisplays {
  private runs = new Map<string, Held>();
  private published = new Map<string, HeldDisplay>();
  private listeners = new Map<string, Set<() => void>>();
  private changed = new Set<string>();
  private frame?: number;

  /** Unfinished output may have ended outside the new watch window. Read it afresh on reconnect. */
  connected() {
    const dropped: string[] = [];
    for (const [runId, held] of this.runs)
      if (!held.complete) {
        this.runs.delete(runId);
        dropped.push(runId);
        this.notify(runId);
      }
    return dropped;
  }

  items(snapshot: RunSnapshot) {
    const previous = this.runs.get(snapshot.run_id);
    // Items before `first` are final when this connection delivered them and
    // they applied; an earlier connection's may have missed output.
    const kept =
      previous && !previous.diverged
        ? [...previous.items.values()].filter(
            (item) => item.ordinal < snapshot.first,
          )
        : [];
    this.runs.set(snapshot.run_id, {
      items: new Map(
        [...kept, ...snapshot.items].map((item) => [item.id, item]),
      ),
      first: previous && kept.length ? previous.first : snapshot.first,
      normalizer: new DisplayNormalizer(snapshot.continuation),
      complete: snapshot.complete,
      gap: false,
      diverged: false,
    });
    this.notify(snapshot.run_id);
    this.prune();
  }

  delta(delta: SessionDelta) {
    const held = this.runs.get(delta.run_id);
    if (!held || held.complete || held.gap) return;
    try {
      applyDelta(held.items, delta, held.normalizer);
    } catch {
      held.diverged = true;
      this.gap(delta.run_id);
      return;
    }
    this.notify(delta.run_id);
  }

  gap(runId: string) {
    const held = this.runs.get(runId);
    if (!held || held.gap) return;
    held.gap = true;
    held.normalizer.gap();
    // Unfinished items stop where the output was lost.
    for (const [id, item] of held.items)
      if (item.state === "in_progress")
        held.items.set(id, {
          ...item,
          content: { ...item.content, incomplete: true },
        });
    this.notify(runId);
  }

  /** The run's display, or undefined when the stream does not hold it. */
  get(runId: string): HeldDisplay | undefined {
    const held = this.runs.get(runId);
    if (!held) return undefined;
    let display = this.published.get(runId);
    if (!display) {
      const items = [...held.items.values()].sort(byOrdinal);
      const omitted = items.some((item) => isOmitted(item.content));
      display = {
        items,
        first: held.first,
        complete: held.complete,
        partial:
          held.gap ||
          held.normalizer.incomplete ||
          omitted ||
          items.some((item) => item.content.incomplete === true),
        omitted,
      };
      this.published.set(runId, display);
    }
    return display;
  }

  subscribe(runId: string, listener: () => void) {
    const listeners = this.listeners.get(runId) ?? new Set();
    this.listeners.set(runId, listeners.add(listener));
    return () => {
      listeners.delete(listener);
      if (!listeners.size) this.listeners.delete(runId);
      this.prune();
    };
  }

  close() {
    if (this.frame !== undefined) cancelAnimationFrame(this.frame);
    this.frame = undefined;
  }

  /** Keep active work and observed output, plus a small cache of recently completed Runs. */
  private prune() {
    const unused = [...this.runs].filter(
      ([id, held]) => held.complete && !this.listeners.has(id),
    );
    for (const [id] of unused.slice(0, Math.max(0, unused.length - 20))) {
      this.runs.delete(id);
      this.published.delete(id);
    }
  }

  private notify(runId: string) {
    this.published.delete(runId);
    this.changed.add(runId);
    this.frame ??= requestAnimationFrame(() => {
      this.frame = undefined;
      const changed = [...this.changed];
      this.changed.clear();
      for (const id of changed)
        for (const listener of this.listeners.get(id) ?? []) listener();
    });
  }
}
