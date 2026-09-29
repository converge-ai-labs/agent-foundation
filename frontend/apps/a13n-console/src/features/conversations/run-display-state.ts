import type { ThreadDelta } from "../../service-client";
import type { Schema } from "../../shared/api";
import {
  applyDelta,
  comparePositions,
  isFragment,
  type DisplayItem,
} from "./display";

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
  items = new Map<string, DisplayItem>();
  position?: string;
  after?: string;
  private pending: { delta: ThreadDelta; cursor: string }[] = [];
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
    attempt = Math.max(
      attempt,
      this.attempt,
      Number(next.position?.split("-")[0] ?? 0),
    );
    if (discard || attempt !== this.attempt || next.complete) {
      this.pending = [];
      this.missingThrough = undefined;
      this.uncertain = false;
      this.boundarySeen = undefined;
    }
    this.attempt = attempt;
    this.read = next;
    this.items = new Map(next.items.map((item) => [item.id, item]));
    const sameAttempt = next.position?.split("-")[0] === String(attempt);
    this.position = sameAttempt ? next.position! : `${attempt}-0`;
    this.after = sameAttempt ? (next.resume_after ?? undefined) : undefined;
    if (next.complete) return;
    if (this.missingThrough && atLeast(this.position, this.missingThrough))
      this.missingThrough = undefined;
    this.pending = this.pending.filter(
      ({ delta }) =>
        delta.attempt === attempt && !atLeast(this.position, positionOf(delta)),
    );
    for (const frame of this.pending) this.apply(frame.delta, frame.cursor);
  }

  gap(position?: string | null) {
    if (this.read?.complete || (position && atLeast(this.position, position)))
      return false;
    const wasIncomplete = this.incomplete;
    if (position) {
      if (!atLeast(this.missingThrough, position))
        this.missingThrough = position;
    } else this.uncertain = true;
    return !wasIncomplete;
  }

  receive(delta: ThreadDelta, cursor: string) {
    if (this.read?.complete || delta.attempt < this.attempt) return false;
    if (delta.attempt > this.attempt && this.read)
      this.reconcile(this.read, delta.attempt, true);
    if (atLeast(this.position, positionOf(delta))) return false;
    const wasIncomplete = this.incomplete;
    this.pending.push({ delta, cursor });
    this.apply(delta, cursor);
    return !wasIncomplete && this.incomplete && !isFragment(delta);
  }

  private apply(delta: ThreadDelta, cursor: string) {
    const position = positionOf(delta);
    if (this.position && position !== successor(this.position)) {
      this.gap(`${delta.attempt}-${BigInt(delta.sequence) - 1n}`);
      return;
    }
    // A fragment's full observation is available only from a later display.
    if (isFragment(delta) && delta.item) {
      this.gap(position);
      return;
    }
    applyDelta(this.items, delta);
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
      if (attempt === this.attempt) this.after = cursor;
      return false;
    }
    this.gap(position);
    this.uncertain = false; // The boundary now supplies a definite recovery target.
    if (atLeast(this.boundarySeen, position)) return false;
    this.boundarySeen = position;
    return true;
  }
}
