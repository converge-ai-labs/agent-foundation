import {
  useQuery,
  useQueryClient,
  type Query,
  type QueryClient,
  type QueryKey,
} from "@tanstack/react-query";
import { result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";

const generations = new WeakMap<Query, number>();

// Hints arriving during HTTP reads must not let an old Run response become
// current, even temporarily. One follow-up read reconciles all coalesced hints.
export function invalidateWorkRead(query: Query) {
  if (query.queryKey[0] === "thread" && query.queryKey[2] === "work")
    generations.set(query, (generations.get(query) ?? 0) + 1);
}
export async function readCurrentWork(
  client: QueryClient,
  key: QueryKey,
  signal: AbortSignal,
  read: () => Promise<Schema<"ThreadWork">>,
): Promise<Schema<"ThreadWork">> {
  for (;;) {
    signal.throwIfAborted();
    const query = client.getQueryCache().find({ queryKey: key, exact: true });
    const generation = query ? (generations.get(query) ?? 0) : 0;
    const value = await read();
    signal.throwIfAborted();
    if (!query || generation === (generations.get(query) ?? 0)) return value;
  }
}

function useWorkQuery(
  threadId: string,
  section: "summary" | "tasks" | "notes",
  enabled: boolean,
) {
  const { client } = useTransport();
  const queries = useQueryClient();
  const key = ["thread", threadId, "work", section];
  return useQuery({
    queryKey: key,
    enabled,
    refetchOnWindowFocus: "always",
    queryFn: ({ signal }) =>
      readCurrentWork(queries, key, signal, () =>
        result(
          client.GET("/api/threads/{thread_id}/work", {
            params: {
              path: { thread_id: threadId },
              query: { include: section === "summary" ? [] : [section] },
            },
            signal,
          }),
        ),
      ),
  });
}

export function workIdentity(work?: Schema<"ThreadWork">) {
  return work
    ? `${work.epoch}:${work.source}:${work.run_id ?? work.continuation_id}:${work.revision}`
    : undefined;
}

export function useThreadWork(
  threadId: string,
  include: { tasks?: boolean; notes?: boolean } = {},
) {
  const summary = useWorkQuery(threadId, "summary", true);
  const tasks = useWorkQuery(threadId, "tasks", !!include.tasks);
  const notes = useWorkQuery(threadId, "notes", !!include.notes);
  const stale =
    !!summary.error ||
    (!!include.tasks &&
      (!!tasks.error ||
        (!!tasks.data &&
          workIdentity(tasks.data) !== workIdentity(summary.data)))) ||
    (!!include.notes &&
      (!!notes.error ||
        (!!notes.data &&
          workIdentity(notes.data) !== workIdentity(summary.data))));
  return { summary, tasks, notes, stale };
}

export function workCaption(
  work?: Schema<"ThreadWork">,
  stale = false,
  refreshing = false,
) {
  const source =
    work?.source === "live"
      ? `Live Run ${work.run_id}, revision ${work.revision}; not a saved checkpoint.`
      : work?.source === "saved"
        ? `Saved continuation ${work.continuation_id ?? "initial state"}.`
        : "Work observation unavailable.";
  return `${stale ? "Stale observation. " : ""}${refreshing ? "Refreshing. " : ""}${source}`;
}
