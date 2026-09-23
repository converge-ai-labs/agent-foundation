import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import type { ThreadDraft } from "./draft";
import { UnsentStore, unsentInputs } from "./unsent-store";

type UnsentState = {
  store?: UnsentStore;
  inputs: ReadonlyMap<string, string>;
  rows: Schema<"ThreadActivityView">[];
  loading: boolean;
  error: unknown;
  retry?: () => void;
};
const UnsentContext = createContext<UnsentState>({
  inputs: new Map(),
  rows: [],
  loading: false,
  error: null,
});
export const useUnsent = () => useContext(UnsentContext);

export function useTrackUnsent(threadId: string, draft: ThreadDraft) {
  const { store } = useUnsent();
  useEffect(() => {
    store?.track(threadId, draft);
  }, [store, threadId, draft]);
}

export function UnsentProvider({ children }: { children: ReactNode }) {
  const transport = useTransport();
  const store = useMemo(() => new UnsentStore(), [transport]);
  const local = useSyncExternalStore(store.subscribe, store.getSnapshot);
  useEffect(() => () => store.dispose(), [store]);
  const shared = useQuery({
    queryKey: ["unsent-drafts"],
    queryFn: ({ signal }) =>
      result(transport.client.GET("/api/drafts", { signal })),
  });
  const inputs = useMemo(
    () => unsentInputs(shared.data ?? [], local),
    [shared.data, local],
  );
  const ids = [...inputs.keys()].sort();
  const batches: string[][] = [];
  for (let offset = 0; offset < ids.length; offset += 100)
    batches.push(ids.slice(offset, offset + 100));
  const lookups = useQueries({
    queries: batches.map((thread_ids) => ({
      queryKey: ["unsent-threads", thread_ids],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        result(
          transport.client.POST("/api/threads/activity/lookup", {
            body: { thread_ids },
            signal,
          }),
        ),
    })),
  });
  const rows = lookups
    .flatMap((lookup) => lookup.data ?? [])
    .filter((row) => !row.thread.archived);
  rows.sort(
    (a, b) =>
      inputs
        .get(b.thread.thread_id)!
        .localeCompare(inputs.get(a.thread.thread_id)!) ||
      a.thread.thread_id.localeCompare(b.thread.thread_id),
  );
  return (
    <UnsentContext
      value={{
        store,
        inputs,
        rows,
        loading: shared.isPending || lookups.some((lookup) => lookup.isPending),
        error: shared.error ?? lookups.find((lookup) => lookup.error)?.error,
        retry: () => {
          void shared.refetch();
          for (const lookup of lookups) void lookup.refetch();
        },
      }}
    >
      {children}
    </UnsentContext>
  );
}
