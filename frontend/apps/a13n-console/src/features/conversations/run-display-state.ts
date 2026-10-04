import type { ThreadDelta } from "../../service-client";
import type { Schema } from "../../shared/api";
import { applyDelta, comparePositions, type DisplayItem } from "./display";

const MAX_PENDING = 1024;
const MAX_PENDING_BYTES = 1024 * 1024;
const positionOf = (delta: ThreadDelta) => `${delta.attempt}-${delta.sequence}`;
const atLeast = (have: string | undefined, need: string) =>
  have !== undefined && comparePositions(have, need) >= 0;
const successor = (position: string) => {
  const [attempt, sequence] = position.split("-");
  return `${attempt}-${BigInt(sequence!) + 1n}`;
};

/** A durable baseline plus a bounded contiguous live suffix, across reconnects. */
export class RunDisplayState {
  read?: Schema["RunItems"];
  attempt = 0;
  items = new Map<string, DisplayItem>();
  position?: string;
  after?: string;
  private savedPosition?: string;
  private pending: { delta: ThreadDelta; cursor: string; bytes: number }[] = [];
  private pendingBytes = 0;
  private missingThrough?: string;
  private discardedThrough?: string;
  private uncertain = false;
  private boundarySeen?: string;

  get incomplete() {
    return (
      this.uncertain ||
      this.missingThrough !== undefined ||
      this.discardedThrough !== undefined
    );
  }

  resume(run: string) {
    return this.position
      ? { run, position: this.position, after: this.after }
      : undefined;
  }

  /** A durable read may lag live coverage, but never an already installed durable read. */
  reconcile(next: Schema["RunItems"], attempt: number, discard = false) {
    const incoming = next.position ?? "0-0";
    if (this.read?.complete && !next.complete) return false;
    if (
      !next.complete &&
      this.savedPosition &&
      comparePositions(incoming, this.savedPosition) < 0
    )
      return false;
    attempt = Math.max(attempt, this.attempt, Number(incoming.split("-")[0]));
    if (discard || attempt !== this.attempt || next.complete) {
      this.pending = [];
      this.pendingBytes = 0;
      this.missingThrough = undefined;
      this.discardedThrough = undefined;
      this.uncertain = false;
      this.boundarySeen = undefined;
    }
    this.attempt = attempt;
    this.savedPosition = incoming;
    this.read = next;
    this.items = new Map(next.items.map((item) => [item.id, item]));
    const sameAttempt = incoming.split("-")[0] === String(attempt);
    this.position = sameAttempt ? incoming : `${attempt}-0`;
    this.after = sameAttempt ? (next.resume_after ?? undefined) : undefined;
    if (next.complete) return true;
    // A completed baseline read resolves unknown transport loss only through
    // the coverage it actually has, not merely because a request succeeded.
    if (this.missingThrough && atLeast(this.position, this.missingThrough)) {
      this.missingThrough = undefined;
    }
    if (this.discardedThrough && atLeast(this.position, this.discardedThrough))
      this.discardedThrough = undefined;
    this.pending = this.pending.filter(
      ({ delta }) =>
        delta.attempt === attempt && !atLeast(this.position, positionOf(delta)),
    );
    this.pendingBytes = this.pending.reduce(
      (sum, frame) => sum + frame.bytes,
      0,
    );
    for (const frame of this.pending) this.apply(frame.delta, frame.cursor);
    return true;
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
      if (atLeast(this.position, position)) {
        if (this.missingThrough && atLeast(this.position, this.missingThrough))
          this.missingThrough = undefined;
        return false;
      }
      if (!atLeast(this.missingThrough, position))
        this.missingThrough = position;
    } else {
      this.uncertain = true;
      const target = successor(this.position ?? `${this.attempt}-0`);
      if (!atLeast(this.missingThrough, target)) this.missingThrough = target;
    }
    return !wasIncomplete || clarified;
  }

  receive(delta: ThreadDelta, cursor: string) {
    if (this.read?.complete || delta.attempt < this.attempt) return false;
    if (delta.attempt > this.attempt && this.read)
      this.reconcile(this.read, delta.attempt, true);
    if (atLeast(this.position, positionOf(delta))) return false;
    const wasIncomplete = this.incomplete;
    const bytes = new TextEncoder().encode(JSON.stringify(delta)).byteLength;
    this.pending.push({ delta, cursor, bytes });
    this.pendingBytes += bytes;
    this.apply(delta, cursor);
    if (
      this.pending.length > MAX_PENDING ||
      this.pendingBytes > MAX_PENDING_BYTES
    ) {
      // Lost replay data cannot be healed by another live frame. Require a
      // durable baseline covering everything discarded, without silent truncation.
      const target = this.pending.reduce(
        (max, frame) =>
          atLeast(max, positionOf(frame.delta)) ? max : positionOf(frame.delta),
        positionOf(delta),
      );
      if (!atLeast(this.discardedThrough, target))
        this.discardedThrough = target;
      this.pending = [];
      this.pendingBytes = 0;
    }
    return !wasIncomplete && this.incomplete;
  }

  private missing(position: string) {
    if (!atLeast(this.missingThrough, position)) this.missingThrough = position;
  }

  private apply(delta: ThreadDelta, cursor: string) {
    const position = positionOf(delta);
    if (this.position && position !== successor(this.position)) {
      this.missing(`${delta.attempt}-${BigInt(delta.sequence) - 1n}`);
      return;
    }
    try {
      applyDelta(this.items, delta);
    } catch {
      this.missing(position);
      return;
    }
    this.position = position;
    this.after = cursor;
    if (
      !this.uncertain &&
      this.missingThrough &&
      atLeast(position, this.missingThrough)
    )
      this.missingThrough = undefined;
  }

  boundary(attempt: number, sequence: number, cursor: string) {
    if (this.read?.complete || attempt < this.attempt) return false;
    const position = `${attempt}-${sequence}`;
    this.gap(position);
    if (atLeast(this.position, position) && attempt === this.attempt)
      this.after = cursor;
    // Even a fully received boundary advances durability and frees pending data.
    if (
      atLeast(this.savedPosition, position) ||
      atLeast(this.boundarySeen, position)
    )
      return false;
    this.boundarySeen = position;
    return true;
  }
}
