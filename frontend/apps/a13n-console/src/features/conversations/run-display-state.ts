import type { ThreadDelta } from "../../service-client";
import type { Schema } from "../../shared/api";
import {
  DisplayGap,
  DisplayState,
  type DisplaySnapshot,
} from "a13n-ui/display";
import { comparePositions, displayItems, type DisplayItem } from "./display";

const positionOf = (delta: ThreadDelta) => `${delta.attempt}-${delta.sequence}`;
const atLeast = (have: string | undefined, need: string) =>
  have !== undefined && comparePositions(have, need) >= 0;
const successor = (position: string) => {
  const [attempt, sequence] = position.split("-");
  return `${attempt}-${BigInt(sequence!) + 1n}`;
};

/** A saved baseline plus its contiguous live suffix. Its lifetime exceeds any one connection. */
export class RunDisplayState {
  read?: Schema["RunItems"];
  attempt = 0;
  get items(): Map<string, DisplayItem> {
    return this.state
      ? displayItems(this.state.capture(), this.read?.complete)
      : new Map();
  }
  position?: string;
  after?: string;
  private state?: DisplayState;
  private pending: { delta: ThreadDelta; cursor: string; bytes: number }[] = [];
  private pendingBytes = 0;
  private replayFloor?: string;
  private missingThrough?: string;
  private uncertain = false;
  private boundarySeen?: string;

  get incomplete() {
    return this.uncertain || this.missingThrough !== undefined;
  }

  resume(run: string) {
    return this.position
      ? { run, position: this.position, after: this.after }
      : undefined;
  }

  /** Rebuild only the contiguous suffix; later events wait for a snapshot to fill any holes. */
  reconcile(next: Schema["RunItems"], attempt: number, discard = false) {
    // Only selected durable coverage may replace a saved baseline, never a stale HTTP response.
    if (this.read && next.run.version < this.read.run.version) return;
    if (
      this.read?.position &&
      next.position &&
      comparePositions(next.position, this.read.position) < 0
    )
      return;
    if (this.read?.complete && !next.complete) return;
    // Evicted deltas are already represented in the current state. An older
    // baseline cannot replace it until durable coverage spans that prefix.
    if (
      !discard &&
      !next.complete &&
      attempt <= this.attempt &&
      this.replayFloor &&
      !atLeast(next.position ?? undefined, this.replayFloor)
    )
      return;
    attempt = Math.max(
      attempt,
      this.attempt,
      Number(next.position?.split("-")[0] ?? 0),
    );
    if (discard || attempt !== this.attempt || next.complete) {
      this.pending = [];
      this.pendingBytes = 0;
      this.replayFloor = undefined;
      this.missingThrough = undefined;
      this.uncertain = false;
      this.boundarySeen = undefined;
    }
    this.attempt = attempt;
    this.read = next;
    const sameAttempt = next.position?.split("-")[0] === String(attempt);
    // OpenAPI represents arbitrary JSON content as unknown; HTTP JSON is a shared JsonValue.
    const baseline = structuredClone(next.snapshot) as DisplaySnapshot;
    if (!sameAttempt)
      baseline.position = {
        producer: { run_id: next.run.id, generation: String(attempt) },
        sequence: 0,
      };
    this.state = new DisplayState(baseline);
    this.position = sameAttempt ? next.position! : `${attempt}-0`;
    this.after = sameAttempt ? (next.resume_after ?? undefined) : undefined;
    if (next.complete) return;
    if (this.missingThrough && atLeast(this.position, this.missingThrough))
      this.missingThrough = undefined;
    this.pending = this.pending.filter(
      ({ delta }) =>
        delta.attempt === attempt && !atLeast(this.position, positionOf(delta)),
    );
    this.pendingBytes = this.pending.reduce(
      (sum, frame) => sum + frame.bytes,
      0,
    );
    this.replayFloor = undefined;
    for (const frame of this.pending) this.apply(frame.delta, frame.cursor);
  }

  gap(position?: string | null) {
    if (
      this.read?.complete ||
      (position && comparePositions(position, `${this.attempt}-0`) < 0)
    )
      return false;
    const wasIncomplete = this.incomplete;
    const clarified = this.uncertain && Boolean(position);
    if (position) {
      // A known range replaces uncertainty, even when the local display already covers it.
      this.uncertain = false;
      if (atLeast(this.position, position)) return false;
      if (!atLeast(this.missingThrough, position))
        this.missingThrough = position;
    } else this.uncertain = true;
    return !wasIncomplete || clarified;
  }

  receive(delta: ThreadDelta, cursor: string) {
    if (this.read?.complete || delta.attempt < this.attempt) return false;
    if (delta.attempt > this.attempt && this.read)
      this.reconcile(this.read, delta.attempt, true);
    if (atLeast(this.position, positionOf(delta))) return false;
    const wasIncomplete = this.incomplete;
    // Bound both count and encoded bytes, including a single oversized batch.
    const bytes = new TextEncoder().encode(JSON.stringify(delta)).byteLength;
    this.pending.push({ delta, cursor, bytes });
    this.pendingBytes += bytes;
    while (this.pending.length > 1024 || this.pendingBytes > 4 * 1024 * 1024) {
      const removed = this.pending.shift()!;
      this.pendingBytes -= removed.bytes;
      this.replayFloor = positionOf(removed.delta);
    }
    this.apply(delta, cursor);
    return !wasIncomplete && this.incomplete;
  }

  private apply(delta: ThreadDelta, cursor: string) {
    const position = positionOf(delta);
    if (this.position && position !== successor(this.position)) {
      this.gap(`${delta.attempt}-${BigInt(delta.sequence) - 1n}`);
      return;
    }
    if (!this.state) {
      this.gap(position);
      return;
    }
    try {
      this.state.apply(delta.delta);
    } catch (error) {
      if (!(error instanceof DisplayGap)) throw error;
      this.gap(position);
      return;
    }
    this.position = position;
    this.after = cursor;
    this.uncertain = false;
    if (this.missingThrough && atLeast(position, this.missingThrough))
      this.missingThrough = undefined;
  }

  boundary(attempt: number, sequence: number, cursor: string) {
    if (this.read?.complete || attempt < this.attempt) return false;
    const position = `${attempt}-${sequence}`;
    if (atLeast(this.position, position)) {
      this.uncertain = false;
      if (attempt === this.attempt && cursor) this.after = cursor;
      return false;
    }
    this.gap(position);
    this.uncertain = false; // The boundary now supplies a definite recovery target.
    if (atLeast(this.boundarySeen, position)) return false;
    this.boundarySeen = position;
    return true;
  }
}
