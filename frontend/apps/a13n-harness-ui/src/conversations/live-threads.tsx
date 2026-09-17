import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { useLocation } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useTransport } from "../transport/context";
import { FocusDisplay, watchThread } from "./stream";
import {
  seedThreadSnapshot,
  useHistory,
  useThread,
  useThreads,
} from "./queries";
import { refreshThread } from "./refresh";

// Running roots stay observed independently of the bounded history/idle cache.
// All focus channels share one transport; observation never mounts an editor.
const RETAINED_LIMIT = 8;

class LiveThread {
  readonly display = new FocusDisplay();
  private listeners = new Set<() => void>();
  private timer?: ReturnType<typeof setTimeout>;
  private snapshot = {
    display: this.display,
    connection: "Connecting",
    reconnections: 0,
    revision: 0,
  };
  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  changed = () => {
    if (this.timer !== undefined) return;
    this.timer = setTimeout(() => {
      this.timer = undefined;
      this.snapshot = {
        ...this.snapshot,
        revision: this.snapshot.revision + 1,
      };
      this.listeners.forEach((listener) => listener());
    }, 50);
  };
  connection = (connection: string) => {
    if (this.snapshot.connection === connection) return;
    this.snapshot = {
      ...this.snapshot,
      connection,
      reconnections:
        this.snapshot.reconnections + Number(connection === "Reconnecting"),
    };
    this.changed();
  };
  dispose() {
    clearTimeout(this.timer);
    this.timer = undefined;
  }
}

class LiveThreads {
  readonly entries = new Map<string, LiveThread>();
  get(id: string) {
    let entry = this.entries.get(id);
    if (!entry) {
      entry = new LiveThread();
      this.entries.set(id, entry);
    }
    return entry;
  }
  retain(ids: string[]) {
    for (const [id, entry] of this.entries) {
      if (!ids.includes(id)) {
        entry.dispose();
        this.entries.delete(id);
      }
    }
  }
  dispose() {
    for (const entry of this.entries.values()) entry.dispose();
    this.entries.clear();
  }
}
const LiveContext = createContext<LiveThreads | null>(null);

// Observation only: no composer, presence, result acknowledgement or hidden DOM.
// Retained query observers also reconcile a background completion into saved
// history after its focus connection closes. HTTP remains the state authority.
function ObserveThread({
  id,
  entry,
  live,
  warm,
}: {
  id: string;
  entry: LiveThread;
  live: boolean;
  warm: boolean;
}) {
  const transport = useTransport();
  const queries = useQueryClient();
  const detail = useThread(id);
  useHistory(id, detail.data?.continuation_id, warm && !!detail.data);
  useEffect(() => {
    if (!live) return;
    return watchThread(
      transport,
      id,
      entry.display,
      entry.changed,
      entry.connection,
      (reason) => refreshThread(queries, id, reason),
      (snapshot) => seedThreadSnapshot(queries, id, snapshot),
    );
  }, [transport, queries, id, entry, live]);
  return null;
}

export function LiveThreadsProvider({ children }: { children: ReactNode }) {
  const transport = useTransport();
  const store = useMemo(() => new LiveThreads(), [transport]);
  const location = useLocation();
  const selected = /^\/threads\/([^/]+)\/?$/.exec(location.pathname)?.[1];
  const current = selected ? decodeURIComponent(selected) : undefined;
  const [recent, setRecent] = useState<string[]>([]);
  // Global discovery is independent of sidebar pagination, filters and collapse.
  const activity = useThreads("", undefined, false, {
    includeActive: true,
    limit: 1,
  });
  const active = (activity.data?.pages[0]?.active_rows ?? []).map(
    (row) => row.thread.thread_id,
  );
  const live = [...new Set([...(current ? [current] : []), ...active])];
  const warm = [
    ...new Set([...(current ? [current] : []), ...recent, ...active]),
  ].slice(0, RETAINED_LIMIT);
  const retained = [...new Set([...live, ...warm])];
  useEffect(() => {
    if (current)
      setRecent((previous) =>
        previous[0] === current
          ? previous
          : [current, ...previous.filter((id) => id !== current)].slice(
              0,
              RETAINED_LIMIT,
            ),
      );
  }, [current]);
  // Keep formerly running background Threads too, so completion cutover can load
  // before their first visit. Never let the registry grow with the whole archive.
  useEffect(() => {
    setRecent((previous) => {
      const next = [...new Set([...warm, ...previous])].slice(
        0,
        RETAINED_LIMIT,
      );
      return next.join("\0") === previous.join("\0") ? previous : next;
    });
    store.retain(retained);
  });
  useEffect(() => () => store.dispose(), [store]);
  return (
    <LiveContext value={store}>
      {retained.map((id) => (
        <ObserveThread
          key={id}
          id={id}
          entry={store.get(id)}
          live={live.includes(id)}
          warm={warm.includes(id)}
        />
      ))}
      {children}
    </LiveContext>
  );
}

export function useLiveThread(id: string) {
  const store = useContext(LiveContext);
  if (!store) throw new Error("Live Thread observation is missing.");
  const entry = store.get(id);
  return useSyncExternalStore(entry.subscribe, entry.getSnapshot);
}
