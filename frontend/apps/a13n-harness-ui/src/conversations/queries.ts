import { useEffect, useRef } from "react";
import {
  useInfiniteQuery,
  useQuery,
  type Query,
  type QueryClient,
} from "@tanstack/react-query";
import { useTransport } from "../transport/context";
import { result } from "../transport/client";

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
  }: {
    scope?: "all" | "projectless" | "unavailable";
    enabled?: boolean;
    limit?: number;
  } = {},
) {
  const { client } = useTransport();
  return useInfiniteQuery({
    queryKey: ["threads", query, projectId, archived, scope, limit],
    enabled,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      result(
        client.GET("/api/threads/activity", {
          params: {
            query: {
              query: query || undefined,
              project_id: projectId,
              project_scope: scope,
              include_archived: archived,
              cursor: pageParam,
              limit,
            },
          },
          signal,
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
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
) {
  const { client } = useTransport();
  const query = useInfiniteQuery({
    queryKey: ["thread", threadId, "history", continuation],
    enabled,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      result(
        client.GET("/api/threads/{thread_id}/transcript", {
          params: {
            path: { thread_id: threadId },
            query: {
              expected_continuation_id: continuation ?? undefined,
              cursor: pageParam,
              limit: 30,
            },
          },
          signal,
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  // Keep the last successful history, with its real continuation identity, while
  // a replacement loads or fails. Query placeholder data disappears on errors.
  const previous = useRef<{ threadId: string; data: typeof query.data }>(
    undefined,
  );
  useEffect(() => {
    if (query.isSuccess) previous.current = { threadId, data: query.data };
  }, [threadId, query.data, query.isSuccess]);
  return {
    ...query,
    data:
      query.data ??
      (previous.current?.threadId === threadId
        ? previous.current.data
        : undefined),
    hasNextPage: !!query.data && query.hasNextPage,
    isPreviousHistory:
      !query.data &&
      previous.current?.threadId === threadId &&
      !!previous.current.data,
  };
}
