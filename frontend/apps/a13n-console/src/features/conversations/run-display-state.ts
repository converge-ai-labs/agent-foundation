import type { ThreadDelta } from "../../service-client";
import type { Schema } from "../../shared/api";
import { applyDelta, comparePositions, type DisplayItem } from "./display";

const positionOf = (delta: ThreadDelta) => `${delta.attempt}-${delta.sequence}`;
const atLeast = (have: string | undefined, need: string) =>
  have !== undefined && comparePositions(have, need) >= 0;
const successor = (position: string) => {
  const [attempt, sequence] = position.split("-");
  return `${attempt}-${BigInt(sequence!) + 1n}`;
};
const MAX_PENDING = 1024;

/** A compact baseline plus contiguous live items, never a journal of applied deltas. */
export class RunDisplayState {
  read?: Schema["RunItems"];
  attempt = 0;
  items = new Map<string, DisplayItem>();
  position?: string;
  after?: string;
  // Only unapplied output beyond a hole waits here, under a fixed retention bound.
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

  reconcile(next: Schema["RunItems"], attempt: number, discard = false) {
    if (
      !discard &&
      next.position &&
      this.read?.position &&
      comparePositions(next.position, this.read.position) < 0
    )
      return;
    attempt = Math.max(
      attempt,
      this.attempt,
      Number(next.position?.split("-")[0] ?? 0),
    );
    const reset = discard || attempt !== this.attempt || next.complete;
    const previous = this.items,
      position = this.position,
      after = this.after;
    if (reset) {
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
    // A behind HTTP read cannot erase a contiguous suffix already displayed. Copy
    // its compact items, rather than replaying every token since the last checkpoint.
    if (!reset && position && comparePositions(position, this.position) > 0) {
      for (const [id, item] of previous)
        if (comparePositions(item.last_stream_id, this.position) > 0)
          this.items.set(id, item);
      this.position = position;
      this.after = after;
    }
    if (this.missingThrough && atLeast(this.position, this.missingThrough))
      this.missingThrough = undefined;
    const pending = this.pending;
    this.pending = [];
    for (const frame of pending) {
      if (
        frame.delta.attempt !== attempt ||
        atLeast(this.position, positionOf(frame.delta))
      )
        continue;
      if (!this.apply(frame.delta, frame.cursor)) this.pending.push(frame);
    }
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
    if (!this.apply(delta, cursor)) {
      if (this.pending.length === MAX_PENDING) {
        this.gap(positionOf(this.pending[this.pending.length - 1]!.delta));
        this.pending = [];
      }
      this.pending.push({ delta, cursor });
    }
    return !wasIncomplete && this.incomplete;
  }

  private apply(delta: ThreadDelta, cursor: string): boolean {
    const position = positionOf(delta);
    if (this.position && position !== successor(this.position)) {
      this.gap(`${delta.attempt}-${BigInt(delta.sequence) - 1n}`);
      return false;
    }
    if (!applyDelta(this.items, delta)) {
      this.gap(position);
      return false;
    }
    this.position = position;
    this.after = cursor;
    this.uncertain = false;
    if (this.missingThrough && atLeast(position, this.missingThrough))
      this.missingThrough = undefined;
    return true;
  }

  boundary(attempt: number, sequence: number, cursor: string) {
    if (this.read?.complete || attempt < this.attempt) return false;
    const position = `${attempt}-${sequence}`;
    if (atLeast(this.position, position)) {
      this.uncertain = false;
      // A delayed boundary cannot move the exact cursor behind our applied suffix.
      if (position === this.position) this.after = cursor;
    } else {
      this.gap(position);
      this.uncertain = false;
    }
    if (
      atLeast(this.boundarySeen, position) ||
      atLeast(this.read?.position ?? undefined, position)
    )
      return false;
    this.boundarySeen = position;
    // Also refresh a fully delivered, quiet tail: paging and checkpoint-only state
    // changes are authoritative even when there are no missing transport deltas.
    return true;
  }
}
