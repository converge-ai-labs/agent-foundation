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
import { ResultStore, type FollowedThread } from "./result-store";

export type ResultObservation = {
  thread: Schema<"ThreadSummary">;
  observedAt: number;
};
type ResultSnapshot = {
  followed: ReadonlyMap<string, number>;
  threads: ReadonlyMap<string, ResultObservation>;
  storageError: string;
  lookupError: string;
};

export class ResultTracker {
  private snapshot: ResultSnapshot = {
    followed: new Map(),
    threads: new Map(),
    storageError: "",
    lookupError: "",
  };
  private listeners = new Set<() => void>();
  private channel?: BroadcastChannel;
  private timer?: ReturnType<typeof setTimeout>;
  private refreshing?: Promise<void>;
  private dirty = false;
  private acknowledgements = new Map<string, number>();
  private failedFollows = new Map<string, number>();
  private failedAcknowledgements = new Map<string, number>();

  constructor(
    private readonly transport: Transport,
    private readonly store = new ResultStore(),
  ) {}
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
  private saved(records: FollowedThread[]) {
    const followed = new Map(this.snapshot.followed);
    for (const record of records)
      followed.set(
        record.threadId,
        Math.max(followed.get(record.threadId) ?? 0, record.acknowledged),
      );
    this.publish({ followed });
  }
  private storageFailed() {
    this.publish({
      storageError:
        "New-result tracking could not be saved in this browser. Reminders may be incomplete or reappear after reload. Enable site storage, then retry.",
    });
  }
  observe(thread: Schema<"ThreadSummary">, observedAt = Date.now()) {
    const threads = new Map(this.snapshot.threads);
    const previous = threads.get(thread.thread_id);
    // Lookup, detail, and cross-tab invalidations may race. Never regress a marker.
    if (previous && previous.observedAt > observedAt) {
      if (
        (thread.completion?.version ?? 0) <=
        (previous.thread.completion?.version ?? 0)
      )
        return;
      thread = { ...previous.thread, completion: thread.completion };
      observedAt = previous.observedAt;
    }
    threads.set(thread.thread_id, {
      observedAt,
      thread:
        previous &&
        (previous.thread.completion?.version ?? 0) >
          (thread.completion?.version ?? 0)
          ? { ...thread, completion: previous.thread.completion }
          : thread,
    });
    this.publish({ threads });
  }
  follow = async (thread: Schema<"ThreadSummary">, observedAt = Date.now()) => {
    if (thread.parent_thread_id) return;
    this.observe(thread, observedAt);
    const baseline =
      this.failedFollows.get(thread.thread_id) ??
      thread.completion?.version ??
      0;
    try {
      const record = await this.store.update(thread.thread_id, baseline, true);
      this.failedFollows.delete(thread.thread_id);
      if (record) this.saved([record]);
      this.channel?.postMessage(null);
      this.invalidate();
    } catch {
      this.failedFollows.set(thread.thread_id, baseline);
      this.storageFailed();
    }
  };
  // Admission must not outrun initial interest registration, including a newly
  // created Thread's first Send. A storage failure is visible, not a Send failure.
  beforeRun = async (threadId: string) => {
    if (this.snapshot.followed.has(threadId)) return;
    const detail = await result(
      this.transport.client.GET("/api/threads/{thread_id}", {
        params: { path: { thread_id: threadId } },
      }),
    );
    await this.follow(detail.thread);
  };
  acknowledge = async (threadId: string, version: number) => {
    const known = this.snapshot.followed.get(threadId);
    if (
      known === undefined ||
      version <= known ||
      version <= (this.acknowledgements.get(threadId) ?? 0)
    )
      return;
    this.acknowledgements.set(threadId, version);
    try {
      const record = await this.store.update(threadId, version, false);
      if ((this.failedAcknowledgements.get(threadId) ?? 0) <= version)
        this.failedAcknowledgements.delete(threadId);
      if (record) this.saved([record]);
      this.channel?.postMessage(null);
    } catch {
      this.failedAcknowledgements.set(
        threadId,
        Math.max(version, this.failedAcknowledgements.get(threadId) ?? 0),
      );
      this.storageFailed();
    } finally {
      if (this.acknowledgements.get(threadId) === version)
        this.acknowledgements.delete(threadId);
    }
  };
  isUnread(threadId: string) {
    const acknowledged = this.snapshot.followed.get(threadId);
    return (
      acknowledged !== undefined &&
      (this.snapshot.threads.get(threadId)?.thread.completion?.version ?? 0) >
        acknowledged
    );
  }
  invalidate = (threadId?: string) => {
    if (threadId && !this.snapshot.followed.has(threadId)) return;
    this.dirty = true;
    if (!this.timer && !this.refreshing)
      this.timer = setTimeout(() => {
        this.timer = undefined;
        void this.refresh();
      }, 75);
  };
  refresh = (): Promise<void> => {
    this.dirty = true;
    if (this.refreshing) return this.refreshing;
    clearTimeout(this.timer);
    this.timer = undefined;
    this.refreshing = (async () => {
      while (this.dirty) {
        this.dirty = false;
        try {
          for (const [id, baseline] of this.failedFollows) {
            const record = await this.store.update(id, baseline, true);
            if (record) this.saved([record]);
            this.failedFollows.delete(id);
            this.channel?.postMessage(null);
          }
          for (const [id, version] of this.failedAcknowledgements) {
            const record = await this.store.update(id, version, false);
            if (record) this.saved([record]);
            if (this.failedAcknowledgements.get(id) === version)
              this.failedAcknowledgements.delete(id);
            this.channel?.postMessage(null);
          }
          this.saved(await this.store.read());
          if (!this.failedFollows.size && !this.failedAcknowledgements.size)
            this.publish({ storageError: "" });
        } catch {
          this.storageFailed();
        }
        const ids = [...this.snapshot.followed.keys()];
        try {
          for (let offset = 0; offset < ids.length; offset += 100) {
            const observedAt = Date.now();
            const page = await result(
              this.transport.client.POST("/api/threads/lookup", {
                body: { thread_ids: ids.slice(offset, offset + 100) },
              }),
            );
            for (const thread of page.threads) this.observe(thread, observedAt);
          }
          this.publish({ lookupError: "" });
        } catch {
          this.publish({
            lookupError:
              "New results could not be refreshed. Existing reminders are retained; retry when connected.",
          });
          // Retry on the next event/focus/reconnect, not a failure spin loop.
          this.dirty = false;
        }
      }
    })().finally(() => {
      this.refreshing = undefined;
    });
    return this.refreshing;
  };
  start() {
    try {
      this.channel = new BroadcastChannel("a13n-harness-ui.results");
      // Messages carry no state; the database owns atomic cross-tab updates.
      this.channel.onmessage = () => this.invalidate();
    } catch {
      /* Focus/reconnect also rereads storage when BroadcastChannel is unavailable. */
    }
    const foreground = () => {
      if (document.visibilityState === "visible") this.invalidate();
    };
    window.addEventListener("focus", foreground);
    document.addEventListener("visibilitychange", foreground);
    void this.refresh();
    return () => {
      window.removeEventListener("focus", foreground);
      document.removeEventListener("visibilitychange", foreground);
      this.channel?.close();
      this.channel = undefined;
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
  followed: new Map(),
  threads: new Map(),
  storageError: "",
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
