import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { result, type Schema, type Transport } from "../transport/client";
import { useTransport } from "../transport/context";

export type ResultObservation = {
  thread: Schema<"ThreadSummary">;
  observedAt: number;
};
type ResultSnapshot = {
  threads: ReadonlyMap<string, ResultObservation>;
  mutationError: string;
  lookupError: string;
};
type ReadReceipt = Schema<"ThreadReadReceipt">;

// A query cache only: SQLite owns read state across browsers and restarts.
export class ResultTracker {
  private snapshot: ResultSnapshot = {
    threads: new Map(),
    mutationError: "",
    lookupError: "",
  };
  private listeners = new Set<() => void>();
  private timer?: ReturnType<typeof setTimeout>;
  private refreshing?: Promise<void>;
  private dirty = false;
  private fullRefresh = false;
  private dirtyIds = new Set<string>();
  private acknowledgements = new Map<string, number>();
  private failedAcknowledgements = new Map<string, ReadReceipt>();

  constructor(private readonly transport: Transport) {}
  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private publish(patch: Partial<ResultSnapshot>) {
    this.snapshot = { ...this.snapshot, ...patch };
    this.listeners.forEach((listener) => listener());
  }
  observe(thread: Schema<"ThreadSummary">, observedAt = Date.now()) {
    this.observeMany([thread], observedAt);
  }
  private observeMany(
    rows: readonly Schema<"ThreadSummary">[],
    observedAt: number,
  ) {
    const threads = new Map(this.snapshot.threads);
    for (const incoming of rows) {
      const previous = threads.get(incoming.thread_id);
      const current = previous?.thread;
      let thread =
        previous && previous.observedAt > observedAt
          ? previous.thread
          : incoming;
      // Read state has its own monotonic revision; wall-clock query freshness
      // cannot undo a read, a manual mark, or a newly completed result.
      const reading =
        current &&
        (current.read_state_version ?? 0) > (incoming.read_state_version ?? 0)
          ? current
          : incoming;
      const completion =
        current &&
        (current.completion?.version ?? 0) > (incoming.completion?.version ?? 0)
          ? current.completion
          : incoming.completion;
      thread = {
        ...thread,
        unread: reading.unread,
        read_state_version: reading.read_state_version,
        completion,
      };
      threads.set(thread.thread_id, {
        thread,
        observedAt: Math.max(previous?.observedAt ?? 0, observedAt),
      });
    }
    this.publish({ threads });
  }
  acknowledge = async (
    threadId: string,
    completionVersion: number,
    readStateVersion: number,
  ) => {
    const thread = this.snapshot.threads.get(threadId)?.thread;
    if (
      !thread?.unread ||
      thread.read_state_version !== readStateVersion ||
      (thread.completion?.version ?? 0) !== completionVersion
    )
      return;
    await this.acknowledgeReceipts([
      {
        thread_id: threadId,
        completion_version: completionVersion,
        read_state_version: readStateVersion,
      },
    ]);
  };
  private acknowledgeReceipts = async (receipts: readonly ReadReceipt[]) => {
    const pending = receipts.filter(
      (item) =>
        this.acknowledgements.get(item.thread_id) !== item.read_state_version,
    );
    for (let offset = 0; offset < pending.length; offset += 100) {
      const batch = pending.slice(offset, offset + 100);
      batch.forEach((item) =>
        this.acknowledgements.set(item.thread_id, item.read_state_version),
      );
      const observedAt = Date.now();
      try {
        const page = await result(
          this.transport.client.POST("/api/threads/read", {
            body: { threads: batch },
          }),
        );
        this.observeMany(page.threads, observedAt);
        batch.forEach((item) => {
          if (
            (this.failedAcknowledgements.get(item.thread_id)
              ?.read_state_version ?? -1) <= item.read_state_version
          )
            this.failedAcknowledgements.delete(item.thread_id);
        });
        this.publish({
          mutationError: this.failedAcknowledgements.size
            ? this.snapshot.mutationError
            : "",
        });
      } catch {
        batch.forEach((item) =>
          this.failedAcknowledgements.set(item.thread_id, item),
        );
        this.publish({
          mutationError: "Read state could not be saved. Retry when connected.",
        });
      } finally {
        batch.forEach((item) => {
          if (
            this.acknowledgements.get(item.thread_id) ===
            item.read_state_version
          )
            this.acknowledgements.delete(item.thread_id);
        });
      }
    }
  };
  acknowledgeAll = async (threads: readonly Schema<"ThreadSummary">[]) => {
    await this.acknowledgeReceipts(
      threads
        .filter((thread) => thread.unread)
        .map((thread) => ({
          thread_id: thread.thread_id,
          read_state_version: thread.read_state_version ?? 0,
          completion_version: thread.completion?.version ?? 0,
        })),
    );
  };
  isUnread(threadId: string) {
    return this.snapshot.threads.get(threadId)?.thread.unread ?? false;
  }
  invalidate = (threadId?: string) => {
    this.dirty = true;
    if (threadId) this.dirtyIds.add(threadId);
    else this.fullRefresh = true;
    if (!this.timer && !this.refreshing)
      this.timer = setTimeout(() => {
        this.timer = undefined;
        void this.refresh(false);
      }, 75);
  };
  refresh = (full = true): Promise<void> => {
    this.dirty = true;
    this.fullRefresh ||= full;
    if (this.refreshing) return this.refreshing;
    clearTimeout(this.timer);
    this.timer = undefined;
    this.refreshing = (async () => {
      while (this.dirty) {
        this.dirty = false;
        const all = this.fullRefresh;
        this.fullRefresh = false;
        const ids = new Set(this.dirtyIds);
        this.dirtyIds.clear();
        try {
          if (all) {
            // Reconcile previously unread identities even if another device
            // already read/deleted them. Discover unseen roots independently
            // of the sidebar's recent pages, in bounded keyset pages.
            for (const { thread } of this.snapshot.threads.values())
              if (thread.unread) ids.add(thread.thread_id);
            let cursor: string | undefined;
            do {
              const observedAt = Date.now();
              const page = await result(
                this.transport.client.GET("/api/threads", {
                  params: { query: { unread: true, limit: 100, cursor } },
                }),
              );
              this.observeMany(page.threads, observedAt);
              cursor = page.next_cursor ?? undefined;
            } while (cursor);
          }
          const identities = [...ids];
          for (let offset = 0; offset < identities.length; offset += 100) {
            const batch = identities.slice(offset, offset + 100);
            const observedAt = Date.now();
            const page = await result(
              this.transport.client.POST("/api/threads/lookup", {
                body: { thread_ids: batch },
              }),
            );
            this.observeMany(page.threads, observedAt);
            const found = new Set(
              page.threads.map((thread) => thread.thread_id),
            );
            const threads = new Map(this.snapshot.threads);
            for (const id of batch)
              if (
                !found.has(id) &&
                (threads.get(id)?.observedAt ?? 0) <= observedAt
              )
                threads.delete(id);
            this.publish({ threads });
          }
          if (this.failedAcknowledgements.size)
            await this.acknowledgeReceipts([
              ...this.failedAcknowledgements.values(),
            ]);
          this.publish({ lookupError: "" });
        } catch {
          this.publish({
            lookupError:
              "New results could not be refreshed. Existing reminders are retained; retry when connected.",
          });
          this.dirty = false;
          this.fullRefresh = true;
        }
      }
    })().finally(() => {
      this.refreshing = undefined;
    });
    return this.refreshing;
  };
  start() {
    const foreground = () => {
      if (document.visibilityState === "visible") this.invalidate();
    };
    window.addEventListener("focus", foreground);
    document.addEventListener("visibilitychange", foreground);
    void this.refresh();
    return () => {
      window.removeEventListener("focus", foreground);
      document.removeEventListener("visibilitychange", foreground);
      clearTimeout(this.timer);
      this.timer = undefined;
    };
  }
}

export const ResultsContext = createContext<ResultTracker | null>(null);
export function ResultsProvider({ children }: { children: ReactNode }) {
  const transport = useTransport();
  const tracker = useMemo(() => new ResultTracker(transport), [transport]);
  useEffect(() => tracker.start(), [tracker]);
  return <ResultsContext value={tracker}>{children}</ResultsContext>;
}
const empty: ResultSnapshot = {
  threads: new Map(),
  mutationError: "",
  lookupError: "",
};
const noSubscribe = () => () => {};
const emptySnapshot = () => empty;
export function useResults() {
  const tracker = useContext(ResultsContext);
  const snapshot = useSyncExternalStore(
    tracker?.subscribe ?? noSubscribe,
    tracker?.getSnapshot ?? emptySnapshot,
  );
  return { tracker, ...snapshot };
}
