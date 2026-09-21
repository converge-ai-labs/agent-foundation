import type { Schema } from "../transport/client";
import type { ThreadDraft } from "./draft";

type LocalDraft = {
  nonempty: boolean;
  override: boolean;
  since: string;
  draftId?: string;
};

// Observe only existing composers, without keeping their sockets or editors open.
export class UnsentStore {
  private snapshot = new Map<string, LocalDraft>();
  private listeners = new Set<() => void>();
  private subscriptions = new Map<
    string,
    { draft: ThreadDraft; close: () => void }
  >();
  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  track(id: string, draft: ThreadDraft) {
    if (this.subscriptions.get(id)?.draft === draft) return;
    this.subscriptions.get(id)?.close();
    const update = () => {
      const previous = this.snapshot.get(id);
      const nonempty = draft.hasUnsentInput;
      // A clean, disconnected replica can be stale after a peer sends/clears.
      // Unsynchronized local edits (including deletion) still take precedence.
      const override = draft.replacement
        ? nonempty
        : draft.status === "Connected" || draft.hasUnacknowledgedEdits;
      if (
        previous?.nonempty === nonempty &&
        previous.override === override &&
        previous.draftId === draft.draftId
      )
        return;
      this.snapshot = new Map(this.snapshot).set(id, {
        nonempty,
        override,
        draftId: draft.draftId,
        since:
          nonempty && previous?.nonempty
            ? previous.since
            : new Date().toISOString(),
      });
      this.listeners.forEach((listener) => listener());
    };
    this.subscriptions.set(id, { draft, close: draft.subscribe(update) });
    update();
  }
  dispose() {
    for (const { close } of this.subscriptions.values()) close();
    this.subscriptions.clear();
  }
}

export function unsentInputs(
  shared: readonly Schema<"DraftSummary">[],
  local: ReadonlyMap<string, LocalDraft>,
): ReadonlyMap<string, string> {
  const inputs = new Map(
    shared.map((draft) => [draft.thread_id, draft.unsent_since]),
  );
  for (const [id, draft] of local) {
    if (!draft.override) continue;
    if (draft.nonempty) inputs.set(id, inputs.get(id) ?? draft.since);
    // A deletion belongs to one room incarnation, never its replacement.
    else if (
      shared.find((item) => item.thread_id === id)?.draft_id === draft.draftId
    )
      inputs.delete(id);
  }
  return new Map(
    [...inputs].sort(
      ([a, left], [b, right]) =>
        right.localeCompare(left) || a.localeCompare(b),
    ),
  );
}
