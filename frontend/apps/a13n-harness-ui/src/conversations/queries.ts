import { useEffect, useRef } from "react";
import {
  useInfiniteQuery,
  useQuery,
  useQueryClient,
  type Query,
  type QueryClient,
} from "@tanstack/react-query";
import { useTransport } from "../transport/context";
import { result, type Schema, type Transport } from "../transport/client";
import { retainThreadSelections } from "./thread-updates";

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
    includeStarred = false,
    coordinatorThreadId,
    independentOnly = false,
  }: {
    scope?: "all" | "projectless" | "unavailable";
    enabled?: boolean;
    limit?: number;
    archivedOnly?: boolean;
    includeActive?: boolean;
    includeStarred?: boolean;
    coordinatorThreadId?: string;
    independentOnly?: boolean;
  } = {},
) {
  const { client } = useTransport();
  const queries = useQueryClient();
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
      includeStarred,
      coordinatorThreadId,
      independentOnly,
    ],
    enabled,
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam, signal }) => {
      const page = await result(
        client.GET("/api/threads/activity", {
          params: {
            query: {
              query: query || undefined,
              project_id: projectId,
              project_scope: scope,
              include_archived: archived,
              archived_only: archivedOnly,
              include_active: includeActive,
              include_starred: includeStarred,
              coordinator_thread_id: coordinatorThreadId,
              independent_only: independentOnly,
              cursor: pageParam,
              limit,
            },
          },
          signal,
        }),
      );
      const retain = (row: Schema<"ThreadActivityView">) => ({
        ...row,
        thread: retainThreadSelections(queries, row.thread),
      });
      return {
        ...page,
        rows: page.rows.map(retain),
        active_rows: page.active_rows?.map(retain),
        starred_rows: page.starred_rows?.map(retain),
        // Pagination observes only its new page, not the rows already loaded.
        observedAt: Date.now(),
      };
    },
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
    includeStarred,
    coordinatorThreadId,
    independentOnly,
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
// Missing owner anchors can be archived or outside the loaded recent pages.
export function useThreadOwners(ids: string[], enabled: boolean) {
  const { client } = useTransport();
  const owners = [...new Set(ids)].sort();
  return useQuery<Schema<"ThreadSummary">[]>({
    queryKey: ["threads", "owners", owners],
    enabled: enabled && owners.length > 0,
    // Pagination can change the missing-owner set. Keep known anchors mounted
    // while fetching the new set; the caller filters them against current IDs.
    placeholderData: (previous) => previous,
    queryFn: async ({ signal }) => {
      const threads: Schema<"ThreadSummary">[] = [];
      for (let offset = 0; offset < owners.length; offset += 100) {
        const page = await result(
          client.POST("/api/threads/lookup", {
            body: { thread_ids: owners.slice(offset, offset + 100) },
            signal,
          }),
        );
        threads.push(...page.threads);
      }
      return threads;
    },
  });
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
  const queries = useQueryClient();
  return useQuery({
    queryKey: ["thread", threadId, "detail"],
    enabled: !!threadId,
    queryFn: async ({ signal }) => {
      const detail = await result(
        client.GET("/api/threads/{thread_id}", {
          params: { path: { thread_id: threadId } },
          signal,
        }),
      );
      return {
        ...detail,
        thread: retainThreadSelections(queries, detail.thread),
      };
    },
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
    queryFn: async ({ pageParam, signal }) => {
      const replacing =
        pageParam === undefined &&
        queries.getQueryCache().findAll({
          queryKey: ["thread", threadId, "history"],
          predicate: (candidate) =>
            candidate.queryKey[3] !== continuation &&
            candidate.queryKey[4] === turnId &&
            candidate.state.data !== undefined,
        }).length > 0;
      const page = await result(
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
      );
      if (!replacing) return page;
      // Keep the old source (and its live suffix) until the replacement is
      // readable. Publishing only boundaries would unmount loaded messages and
      // execution readers on every checkpoint, then flash a turn-loading gap.
      const entries = new Map(
        [...(page.boundary_entries ?? []), ...page.entries].map((entry) => [
          entry.position,
          entry,
        ]),
      );
      for (const turn of page.turns ?? []) {
        const loaded = [...entries.keys()].filter(
          (position) =>
            position >= turn.input_position && position < turn.end_position,
        ).length;
        if (loaded === turn.end_position - turn.input_position) continue;
        for (const entry of await readTurnHistory(
          client,
          threadId,
          continuation,
          turn,
          signal,
        ))
          entries.set(entry.position, entry);
      }
      return {
        ...page,
        entries: [...entries.values()].sort((a, b) => a.position - b.position),
      };
    },
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
  turn: Schema<"TranscriptTurn"> | undefined,
  enabled: boolean,
) {
  const { client } = useTransport();
  return useQuery({
    queryKey: ["thread", threadId, "turn-history", continuation, turn?.turn_id],
    enabled: enabled && !!turn,
    staleTime: Infinity,
    retry: false,
    queryFn: ({ signal }) =>
      readTurnHistory(client, threadId, continuation, turn!, signal),
  });
}

async function readTurnHistory(
  client: Transport["client"],
  threadId: string,
  continuation: string | null | undefined,
  turn: Schema<"TranscriptTurn">,
  signal: AbortSignal,
) {
  const entries = new Map<number, Schema<"TranscriptEntry">>();
  let cursor: string | undefined;
  let earliest = turn.end_position;
  do {
    signal.throwIfAborted();
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
    for (const entry of [...(page.boundary_entries ?? []), ...page.entries]) {
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
}

// Scope identity is shared by Memory navigation and the existing live observer.
// No Thread is created by this read; empty scopes remain file-only views.
export function useMemoryThread(
  projectId: string | undefined,
  enabled: boolean,
) {
  const { client } = useTransport();
  return useQuery({
    queryKey: ["memory", "threads", projectId ?? null],
    enabled,
    refetchInterval: enabled ? 5000 : false,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/threads", {
          params: {
            query: {
              memory: true,
              project_id: projectId,
              projectless: !projectId,
              limit: 1,
            },
          },
          signal,
        }),
      ),
  });
}
