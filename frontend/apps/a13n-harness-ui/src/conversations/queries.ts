import { useEffect, useRef } from "react";
import {
  useInfiniteQuery,
  useQuery,
  useQueryClient,
  type Query,
  type QueryClient,
} from "@tanstack/react-query";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";

const pendingRefreshes = new WeakSet<Query>();

export async function refreshThreadLists(client: QueryClient) {
  const cache = client.getQueryCache();
  await Promise.all(
    cache.findAll({ queryKey: ["threads"] }).map(async (query) => {
      // A hint during Show more must neither cancel pagination nor be consumed by it.
      // Coalesce hints per list, without making other Projects wait for this fetch.
      if (query.state.fetchStatus === "fetching" && query.promise) {
        query.invalidate();
        if (pendingRefreshes.has(query)) return;
        pendingRefreshes.add(query);
        try {
          await query.promise;
        } catch {
          /* Refresh after a failed observation as well. */
        } finally {
          pendingRefreshes.delete(query);
        }
      }
      // Authentication replacement may have discarded this entire query cache.
      if (cache.get(query.queryHash) === query) {
        await client.invalidateQueries(
          { queryKey: query.queryKey, exact: true },
          { cancelRefetch: false },
        );
      }
    }),
  );
}

export function useThreads(
  query = "",
  projectId?: string,
  archived = false,
  {
    scope = "all",
    enabled = true,
    limit = 30,
    archivedOnly = false,
    includeActive = false,
  }: {
    scope?: "all" | "projectless" | "unavailable";
    enabled?: boolean;
    limit?: number;
    archivedOnly?: boolean;
    includeActive?: boolean;
  } = {},
) {
  const { client } = useTransport();
  const list = useInfiniteQuery({
    queryKey: [
      "threads",
      query,
      projectId,
      archived,
      scope,
      limit,
      archivedOnly,
      includeActive,
    ],
    enabled,
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam, signal }) => ({
      ...(await result(
        client.GET("/api/threads/activity", {
          params: {
            query: {
              query: query || undefined,
              project_id: projectId,
              project_scope: scope,
              include_archived: archived,
              archived_only: archivedOnly,
              include_active: includeActive,
              cursor: pageParam,
              limit,
            },
          },
          signal,
        }),
      )),
      // Pagination observes only its new page, not the rows already loaded.
      observedAt: Date.now(),
    }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  // Archive toggles replace a query, not the visible list. Retain only the same
  // search/project scope, including on failure; never reuse its pagination cursor.
  const previous = useRef<{
    client: typeof client;
    identity: string;
    data: typeof list.data;
  }>(undefined);
  const identity = JSON.stringify([
    query,
    projectId,
    scope,
    limit,
    archivedOnly,
    includeActive,
  ]);
  useEffect(() => {
    if (list.isSuccess)
      previous.current = { client, identity, data: list.data };
  }, [client, identity, list.data, list.isSuccess]);
  const retained =
    previous.current?.client === client &&
    previous.current.identity === identity
      ? previous.current.data
      : undefined;
  const data = list.data ?? retained;
  return {
    ...list,
    data: data && {
      ...data,
      pages: data.pages.map((page) => ({
        ...page,
        rows: archivedOnly
          ? page.rows.filter((row) => row.thread.archived)
          : archived
            ? page.rows
            : page.rows.filter((row) => !row.thread.archived),
      })),
    },
    isPreviousData: !list.data && !!retained,
    hasNextPage: !!list.data && list.hasNextPage,
  };
}
export function useOperation(threadId: string, receipt?: string | null) {
  const { client } = useTransport();
  return useQuery({
    queryKey: ["thread", threadId, "operation", receipt],
    enabled: !!receipt,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/operations/{receipt_id}", {
          params: { path: { receipt_id: receipt! } },
          signal,
        }),
      ),
  });
}
// The focused prefix already carries the first detail and exact operation.
// Bootstrap only an empty cache: later HTTP observations remain authoritative,
// and a replay must not roll an existing page back to its snapshot cutover.
export function seedThreadSnapshot(
  client: QueryClient,
  threadId: string,
  snapshot: Schema<"ThreadFocusSnapshot">,
) {
  if (snapshot.thread.thread.thread_id !== threadId) return;
  const key = ["thread", threadId, "detail"];
  if (client.getQueryData(key)) return;
  // Cancel before publishing so a slower initial GET cannot overwrite the prefix.
  void client.cancelQueries({ queryKey: key, exact: true });
  client.setQueryData(key, snapshot.thread);
  const operation = snapshot.root_operation;
  if (operation) {
    const operationKey = [
      "thread",
      threadId,
      "operation",
      operation.receipt.receipt_id,
    ];
    if (!client.getQueryData(operationKey)) {
      void client.cancelQueries({ queryKey: operationKey, exact: true });
      client.setQueryData(operationKey, operation);
    }
  }
}

export function useThread(threadId: string) {
  const { client } = useTransport();
  return useQuery({
    queryKey: ["thread", threadId, "detail"],
    enabled: !!threadId,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/threads/{thread_id}", {
          params: { path: { thread_id: threadId } },
          signal,
        }),
      ),
  });
}
export function useHistory(
  threadId: string,
  continuation: string | null | undefined,
  enabled: boolean,
  turnId?: string,
) {
  const { client } = useTransport();
  const queries = useQueryClient();
  const query = useInfiniteQuery({
    queryKey: [
      "thread",
      threadId,
      "history",
      continuation,
      ...(turnId ? [turnId] : []),
    ],
    enabled,
    // A selected continuation is immutable. New checkpoints use a new key;
    // returning to a cached page must not refetch every loaded history page.
    staleTime: Infinity,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      result(
        client.GET("/api/threads/{thread_id}/transcript", {
          params: {
            path: { thread_id: threadId },
            query: {
              expected_continuation_id: continuation ?? undefined,
              cursor: pageParam,
              turn_id: turnId,
              limit: 30,
            },
          },
          signal,
        }),
      ),
    getNextPageParam: (last) =>
      (last.earlier_turns_cursor === undefined
        ? last.next_cursor
        : last.earlier_turns_cursor) ?? undefined,
    getPreviousPageParam: (first) =>
      (first.later_turns_cursor === undefined
        ? first.newer_cursor
        : first.later_turns_cursor) ?? undefined,
  });
  // Keep the last successful history, with its real continuation identity, while
  // a replacement loads or fails. Query placeholder data disappears on errors.
  const previous = useRef<{ threadId: string; data: typeof query.data }>(
    undefined,
  );
  useEffect(() => {
    if (query.isSuccess) previous.current = { threadId, data: query.data };
  }, [threadId, query.data, query.isSuccess]);
  // A newly opened page can reuse history warmed by the workbench even while a
  // newer continuation is loading. Keep its original identity, never relabel it.
  const cached =
    !query.data && previous.current?.threadId !== threadId
      ? queries
          .getQueryCache()
          .findAll({ queryKey: ["thread", threadId, "history"] })
          .filter((candidate) => candidate.state.data !== undefined)
          .sort((a, b) => b.state.dataUpdatedAt - a.state.dataUpdatedAt)[0]
      : undefined;
  const retained =
    previous.current?.threadId === threadId
      ? previous.current.data
      : cached
        ? queries.getQueryData<NonNullable<typeof query.data>>(cached.queryKey)
        : undefined;
  return {
    ...query,
    data: query.data ?? retained,
    hasNextPage: !!query.data && query.hasNextPage,
    isPreviousHistory: !query.data && !!retained,
  };
}

// Complete each visible turn in one load. Transport pages stay bounded, but
// intermediate pages never become user-facing pagination or partial segments.
export function useTurnHistory(
  threadId: string,
  continuation: string | null | undefined,
  turn: Schema<"TranscriptTurn">,
  enabled: boolean,
) {
  const { client } = useTransport();
  return useQuery({
    queryKey: ["thread", threadId, "turn-history", continuation, turn.turn_id],
    enabled,
    staleTime: Infinity,
    retry: false,
    queryFn: async ({ signal }) => {
      const entries = new Map<number, Schema<"TranscriptEntry">>();
      let cursor: string | undefined;
      let earliest = turn.end_position;
      do {
        const page = await result(
          client.GET("/api/threads/{thread_id}/transcript", {
            params: {
              path: { thread_id: threadId },
              query: {
                expected_continuation_id: continuation ?? undefined,
                turn_id: turn.turn_id,
                cursor,
                limit: 100,
              },
            },
            signal,
          }),
        );
        for (const entry of [
          ...(page.boundary_entries ?? []),
          ...page.entries,
        ]) {
          if (
            entry.position >= turn.input_position &&
            entry.position < turn.end_position
          )
            entries.set(entry.position, entry);
        }
        const first = page.entries[0]?.position;
        if (first === undefined || first >= earliest)
          throw new Error("Turn history is incomplete.");
        earliest = first;
        cursor =
          earliest > turn.input_position
            ? (page.next_cursor ?? undefined)
            : undefined;
      } while (cursor);
      if (entries.size !== turn.end_position - turn.input_position)
        throw new Error("Turn history is incomplete.");
      return [...entries.values()].sort((a, b) => a.position - b.position);
    },
  });
}
