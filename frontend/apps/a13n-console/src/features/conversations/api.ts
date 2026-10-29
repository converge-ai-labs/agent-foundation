import { readRunDisplay, type RunDisplay } from "./display";
import type { Client, paths, ThreadState } from "../../service-client";
import {
  infiniteQueryOptions,
  queryOptions,
  replaceEqualDeep,
  type QueryClient,
} from "@tanstack/react-query";
import { allPages, data, type Schema } from "../../shared/api";

export type SessionFilters = Omit<
  NonNullable<paths["/api/v1/sessions"]["get"]["parameters"]["query"]>,
  "limit" | "cursor"
>;

export type ContextScope =
  { thread_id: string } | { run_id: string } | { record_id: string };

export type UsageScope = { session_id: string } | { run_id: string };
export type ThreadNavigationFilter = Omit<
  NonNullable<
    paths["/api/v1/sessions/{session_id}/threads"]["get"]["parameters"]["query"]
  >,
  "cursor"
>;

export function conversationKeys(workspaceId: string) {
  const root = ["conversations", workspaceId] as const;
  return {
    root,
    sessions: () => [...root, "sessions"] as const,
    session: (sessionId: string) => [...root, "session", sessionId] as const,
    navigation: (sessionId: string) =>
      [...root, "thread-navigation", sessionId] as const,
    thread: (threadId: string) => [...root, "thread", threadId] as const,
    runs: (threadId: string) => [...root, "runs", threadId] as const,
    rankedRuns: (threadId: string) =>
      [...root, "ranked-runs", threadId] as const,
    inbox: (threadId: string) => [...root, "inbox", threadId] as const,
    /** One inbox entry; it changes with its Thread's inbox. */
    entry: (threadId: string, entryId: string) =>
      [...root, "inbox", threadId, "entry", entryId] as const,
    run: (runId: string) => [...root, "run", runId] as const,
    /** The agent one Run executes; frozen at acceptance, it never changes. */
    agent: (runId: string) => [...root, "agent", runId] as const,
    items: (runId: string) => [...root, "items", runId] as const,
    attempts: (runId: string) => [...root, "attempts", runId] as const,
    toolExecutions: (threadId: string, runId?: string) =>
      [...root, "tool-executions", threadId, runId] as const,
    lineage: (runId: string) => [...root, "lineage", runId] as const,
    context: (id: string) => [...root, "context", id] as const,
    requestMetrics: (runId: string, recordId: string) =>
      [...root, "usage", runId, "record", recordId] as const,
    /** Recorded model usage of one Session or one Run. */
    usage: (scope: UsageScope) =>
      [
        ...root,
        "usage",
        "session_id" in scope ? scope.session_id : scope.run_id,
      ] as const,
  };
}

const version = (view: unknown) =>
  (view as { version?: number } | undefined)?.version ?? -1;

/** A view never goes back to an older version, whether a read or a session stream frame lands last. */
function newer(previous: unknown, next: unknown) {
  return version(previous) > version(next)
    ? previous
    : replaceEqualDeep(previous, next);
}

/** A late HTTP response cannot overwrite newer stream metadata on the same page. */
function newerRunPage(previous: unknown, next: unknown) {
  const held = previous as Schema["ThreadRunPage"] | undefined;
  const page = next as Schema["ThreadRunPage"];
  if (!held) return page;
  const versions = new Map(held.items.map((item) => [item.id, item]));
  return replaceEqualDeep(held, {
    ...page,
    total: Math.max(held.total, page.total),
    items: page.items.map((item) => newer(versions.get(item.id), item)),
  });
}

/** Query owns request cancellation; delivery attachments never abort shared reads. */
export function conversationQueries(client: Client, workspaceId: string) {
  const keys = conversationKeys(workspaceId);
  const navigation = (
    session_id: string,
    filters: ThreadNavigationFilter,
    cursor: string | undefined,
    signal: AbortSignal,
  ) =>
    client
      .workspace(workspaceId)
      .GET("/api/v1/sessions/{session_id}/threads", {
        params: { path: { session_id }, query: { ...filters, cursor } },
        signal,
      })
      .then(data);
  return {
    context: (scope: ContextScope) =>
      queryOptions({
        queryKey: keys.context(
          "thread_id" in scope
            ? scope.thread_id
            : "run_id" in scope
              ? scope.run_id
              : scope.record_id,
        ),
        queryFn: ({ signal }) =>
          client
            .workspace(workspaceId)
            .GET("/api/v1/usage/context", { params: { query: scope }, signal })
            .then(data),
      }),
    requestMetrics: (runId: string, record_id: string) =>
      queryOptions({
        queryKey: keys.requestMetrics(runId, record_id),
        queryFn: ({ signal }) =>
          client
            .workspace(workspaceId)
            .GET("/api/v1/usage/records/{record_id}/metrics", {
              params: { path: { record_id } },
              signal,
            })
            .then(data),
      }),
    sessions: (cursor?: string, filters: SessionFilters = {}) =>
      queryOptions({
        queryKey: [...keys.sessions(), filters, cursor],
        queryFn: ({ signal }) =>
          client
            .workspace(workspaceId)
            .GET("/api/v1/sessions", {
              params: {
                query: { ...filters, cursor, limit: 20 },
              },
              signal,
            })
            .then(data),
      }),
    session: (session_id: string) =>
      queryOptions({
        structuralSharing: newer,
        queryKey: keys.session(session_id),
        queryFn: ({ signal }) =>
          client
            .workspace(workspaceId)
            .GET("/api/v1/sessions/{session_id}", {
              params: { path: { session_id } },
              signal,
            })
            .then(data),
      }),
    run: (run_id: string) =>
      queryOptions({
        queryKey: keys.run(run_id),
        structuralSharing: newer,
        queryFn: ({ signal }) =>
          client
            .workspace(workspaceId)
            .GET("/api/v1/runs/{run_id}", {
              params: { path: { run_id } },
              signal,
            })
            .then(data),
      }),
    agent: (run_id: string) =>
      queryOptions({
        queryKey: keys.agent(run_id),
        queryFn: ({ signal }) =>
          client
            .workspace(workspaceId)
            .GET("/api/v1/runs/{run_id}/agent", {
              params: { path: { run_id } },
              signal,
            })
            .then(data),
        staleTime: Infinity,
      }),
    environments: (thread_id: string) =>
      queryOptions({
        queryKey: [...keys.thread(thread_id), "environments"],
        queryFn: ({ signal }) =>
          client
            .workspace(workspaceId)
            .GET("/api/v1/threads/{thread_id}/environments", {
              params: { path: { thread_id } },
              signal,
            })
            .then(data)
            .then((page) => page.items),
      }),
    thread: (thread_id: string) =>
      queryOptions({
        queryKey: keys.thread(thread_id),
        structuralSharing: newer,
        queryFn: ({ signal }) =>
          client
            .workspace(workspaceId)
            .GET("/api/v1/threads/{thread_id}", {
              params: { path: { thread_id } },
              signal,
            })
            .then(data),
      }),
    items: (run_id: string) =>
      queryOptions({
        queryKey: keys.items(run_id),
        queryFn: ({ signal, client: cache }) =>
          readRunDisplay(
            client,
            workspaceId,
            run_id,
            signal,
            cache.getQueryData<RunDisplay>(keys.items(run_id)),
          ),
        staleTime: (query) => (query.state.data?.complete ? Infinity : 0),
      }),
    /**
     * The Run and its ancestors, nearest first, across fork origins. Pages are
     * read as the reader reaches further back, never all up front.
     */
    toolExecutions: (thread_id: string, run_id?: string) =>
      infiniteQueryOptions({
        queryKey: keys.toolExecutions(thread_id, run_id),
        initialPageParam: undefined as string | undefined,
        queryFn: ({ signal, pageParam }) =>
          client
            .workspace(workspaceId)
            .GET("/api/v1/threads/{thread_id}/tool-executions", {
              params: {
                path: { thread_id },
                query: { run_id, cursor: pageParam, limit: 20 },
              },
              signal,
            })
            .then(data),
        getNextPageParam: (page) => page.next_cursor ?? undefined,
      }),
    lineage: (run_id: string) =>
      infiniteQueryOptions({
        queryKey: keys.lineage(run_id),
        initialPageParam: undefined as string | undefined,
        queryFn: ({ signal, pageParam }) =>
          client
            .workspace(workspaceId)
            .GET("/api/v1/runs/{run_id}/lineage", {
              params: {
                path: { run_id },
                query: { cursor: pageParam },
              },
              signal,
            })
            .then(data),
        getNextPageParam: (page) => page.next_cursor ?? undefined,
      }),
    usage: (scope: UsageScope) =>
      queryOptions({
        queryKey: keys.usage(scope),
        queryFn: ({ signal }) =>
          client
            .workspace(workspaceId)
            .GET("/api/v1/usage", {
              params: { query: scope },
              signal,
            })
            .then(data),
      }),
    threads: (
      session_id: string,
      filters: {
        origin?: ("new" | "fork" | "child")[];
        origin_run_id?: string;
        limit: number;
      },
    ) =>
      queryOptions({
        queryKey: [...keys.navigation(session_id), "threads", filters],
        queryFn: ({ signal }) =>
          client
            .workspace(workspaceId)
            .GET("/api/v1/threads", {
              params: { query: { session_id, ...filters } },
              signal,
            })
            .then(data),
      }),
    threadPage: (session_id: string, filters: ThreadNavigationFilter = {}) =>
      queryOptions({
        queryKey: [...keys.navigation(session_id), "page", filters],
        queryFn: ({ signal }) =>
          navigation(session_id, filters, undefined, signal),
      }),
    threadPages: (session_id: string, filters: ThreadNavigationFilter = {}) =>
      infiniteQueryOptions({
        queryKey: [...keys.navigation(session_id), "pages", filters],
        initialPageParam: undefined as string | undefined,
        queryFn: ({ signal, pageParam }) =>
          navigation(session_id, filters, pageParam, signal),
        getNextPageParam: (page) => page.next_cursor ?? undefined,
      }),
    usageRuns: (session_id: string) =>
      infiniteQueryOptions({
        queryKey: [...keys.usage({ session_id }), "runs"],
        initialPageParam: undefined as string | undefined,
        queryFn: ({ signal, pageParam }) =>
          client
            .workspace(workspaceId)
            .GET("/api/v1/usage/runs", {
              params: { query: { session_id, limit: 50, cursor: pageParam } },
              signal,
            })
            .then(data),
        getNextPageParam: (page) => page.next_cursor ?? undefined,
      }),
    runs: (
      thread_id: string,
      position: { anchor?: string; cursor?: string } = {},
    ) =>
      queryOptions({
        structuralSharing: newerRunPage,
        queryKey: [...keys.runs(thread_id), position],
        queryFn: ({ signal }) =>
          client
            .workspace(workspaceId)
            .GET("/api/v1/threads/{thread_id}/runs", {
              params: {
                path: { thread_id },
                query: { ...position, limit: 50 },
              },
              signal,
            })
            .then(data),
      }),
    rankedRuns: (
      thread_id: string,
      sort: "cost" | "calls" | "duration",
      direction: "asc" | "desc",
    ) =>
      infiniteQueryOptions({
        queryKey: [...keys.rankedRuns(thread_id), { sort, direction }],
        initialPageParam: undefined as string | undefined,
        queryFn: ({ signal, pageParam }) =>
          client
            .workspace(workspaceId)
            .GET("/api/v1/threads/{thread_id}/runs", {
              params: {
                path: { thread_id },
                query: { sort, direction, cursor: pageParam, limit: 50 },
              },
              signal,
            })
            .then(data),
        getNextPageParam: (page) => page.next_cursor ?? undefined,
      }),
    attempts: (run_id: string) =>
      queryOptions({
        queryKey: keys.attempts(run_id),
        queryFn: ({ signal }) =>
          client
            .workspace(workspaceId)
            .GET("/api/v1/runs/{run_id}/attempts", {
              params: { path: { run_id } },
              signal,
            })
            .then(data)
            .then((attempts) => attempts.items),
      }),
    /** One Thread's inbox entries in the given states, in inbox order. */
    inbox: (thread_id: string, status: Schema["EntryStatus"][]) =>
      queryOptions({
        queryKey: [...keys.inbox(thread_id), status],
        queryFn: ({ signal }) =>
          allPages((cursor) =>
            client
              .workspace(workspaceId)
              .GET("/api/v1/threads/{thread_id}/inbox", {
                params: {
                  path: { thread_id },
                  query: { status, cursor, limit: 100 },
                },
                signal,
              })
              .then(data),
          ),
      }),
    /** One inbox entry, read on its own as its Thread reports changes. */
    entry: (thread_id: string, entry_id: string) =>
      queryOptions({
        queryKey: keys.entry(thread_id, entry_id),
        queryFn: ({ signal }) =>
          client
            .workspace(workspaceId)
            .GET("/api/v1/threads/{thread_id}/inbox/{entry_id}", {
              params: {
                path: { thread_id, entry_id },
              },
              signal,
            })
            .then(data),
      }),
  };
}

export interface ConversationChange {
  sessionId?: string | null;
  threadId?: string | null;
  runId?: string | null;
}

/** Only the entry page and a loaded newest edge can acquire new members. */
function isLiveRunPage(position: unknown, data: unknown) {
  return (
    !(position as { cursor?: string } | undefined)?.cursor ||
    (data as Schema["ThreadRunPage"] | undefined)?.previous_cursor === null
  );
}

/** One invalidation map for command receipts. */
export function invalidateConversation(
  cache: QueryClient,
  workspaceId: string,
  ...changes: ConversationChange[]
) {
  return cache.invalidateQueries({
    queryKey: conversationKeys(workspaceId).root,
    predicate: ({ queryKey: [, , kind, id, position], state, meta }) =>
      changes.some((change) => {
        switch (kind) {
          case "sessions":
            return !!(change.sessionId || change.threadId || change.runId);
          case "session":
          case "thread-navigation":
            return !!change.sessionId && id === change.sessionId;
          case "context":
            return id === change.threadId || id === change.runId;
          // Usage is keyed by the Session or the Run it sums.
          case "usage":
            return (
              id === change.sessionId ||
              id === change.runId ||
              (!!change.sessionId && meta?.sessionId === change.sessionId)
            );
          case "runs":
            return (
              !!change.threadId &&
              id === change.threadId &&
              isLiveRunPage(position, state.data)
            );
          case "ranked-runs":
          case "thread":
          case "inbox":
            return !!change.threadId && id === change.threadId;
          case "run":
          case "items":
          case "attempts":
          case "lineage":
            return !!change.runId && id === change.runId;
          default:
            return false;
        }
      }),
  });
}

/**
 * Write a thread's state from its session stream: the thread and the runs it
 * names. A changed thread also marks the lists that hold it or its inbox
 * stale.
 */
export function applyThreadState(
  cache: QueryClient,
  workspaceId: string,
  { thread, current_run, last_run }: ThreadState,
) {
  const keys = conversationKeys(workspaceId);
  for (const run of [current_run, last_run]) {
    if (!run) continue;
    const held = version(cache.getQueryData(keys.run(run.id)));
    cache.setQueryData<Schema["RunView"]>(keys.run(run.id), (previous) =>
      previous && previous.version > run.version ? previous : run,
    );
    cache.setQueriesData<Schema["ThreadRunPage"]>(
      { queryKey: keys.runs(run.thread_id) },
      (page) => {
        if (!page) return page;
        return {
          ...page,
          items: page.items.map((item) =>
            item.id !== run.id || item.version >= run.version
              ? item
              : {
                  ...item,
                  status: run.status,
                  current_attempt_id: run.current_attempt_id,
                  attempts: run.attempts,
                  wait_reason: run.wait_reason,
                  version: run.version,
                  started_at: run.started_at,
                  sealed_at: run.sealed_at,
                  updated_at: run.updated_at,
                },
          ),
        };
      },
    );
    // A run's version moves with its attempts.
    if (held >= 0 && held < run.version) {
      void cache.invalidateQueries(
        { queryKey: keys.rankedRuns(run.thread_id) },
        { cancelRefetch: false },
      );
      // Sealing fixes the row's final usage; refresh only pages containing it.
      if (run.sealed_at)
        void cache.invalidateQueries(
          {
            queryKey: keys.runs(run.thread_id),
            predicate: ({ state }) =>
              !!(state.data as Schema["ThreadRunPage"] | undefined)?.items.some(
                (item) => item.id === run.id,
              ),
          },
          { cancelRefetch: false },
        );
      void cache.invalidateQueries({ queryKey: keys.attempts(run.id) });
      void cache.invalidateQueries(
        { queryKey: keys.usage({ run_id: run.id }) },
        { cancelRefetch: false },
      );
      void cache.invalidateQueries(
        { queryKey: keys.usage({ session_id: run.session_id }) },
        { cancelRefetch: false },
      );
    }
  }
  const previous = cache.getQueryData<Schema["ThreadView"]>(
    keys.thread(thread.id),
  );
  const held = version(previous);
  if (held >= thread.version) return;
  cache.setQueryData(keys.thread(thread.id), thread);
  const session = cache.getQueryData<Schema["SessionView"]>(
    keys.session(thread.session_id),
  );
  // The first stream snapshot contains old Threads too. Only a newer acceptance
  // or a changed latest preview requires another Session summary read.
  const sessionChanged =
    !session ||
    [current_run, last_run].some(
      (run) =>
        run &&
        (Date.parse(run.created_at) > Date.parse(session.updated_at) ||
          (session.preview?.run_id === run.id &&
            session.preview.status !== run.status)),
    );
  // Thread creation changes collection membership and global navigation names.
  // Initial stream snapshots only mark those reads stale.
  if (!previous)
    void cache.invalidateQueries(
      {
        queryKey: keys.navigation(thread.session_id),
        refetchType: sessionChanged ? "active" : "none",
      },
      { cancelRefetch: false },
    );
  const membershipChanged =
    !previous ||
    previous.current_run_id !== thread.current_run_id ||
    previous.last_run_id !== thread.last_run_id;
  const latest = thread.current_run_id ?? thread.last_run_id;
  void cache.invalidateQueries(
    {
      queryKey: keys.root,
      predicate: ({ queryKey: [, , kind, id, position], state }) =>
        (sessionChanged &&
          (kind === "sessions" ||
            (kind === "session" && id === thread.session_id))) ||
        (!!previous &&
          kind === "context" &&
          (id === thread.id ||
            id === current_run?.id ||
            id === last_run?.id)) ||
        (!!previous && kind === "inbox" && id === thread.id) ||
        (kind === "ranked-runs" && id === thread.id && membershipChanged) ||
        (kind === "runs" &&
          id === thread.id &&
          membershipChanged &&
          !!latest &&
          isLiveRunPage(position, state.data) &&
          !(state.data as Schema["ThreadRunPage"] | undefined)?.items.some(
            (run) => run.id === latest,
          )),
    },
    { cancelRefetch: held >= 0 },
  );
}

/**
 * The label the Console gives every Session it starts. A Session without it
 * was started by an application, so it opens for inspection at the Debug level.
 */
export const CONSOLE_SESSION_LABELS: Record<string, string> = {
  "a13n.console": "debug",
};

export function isConsoleSession(
  session?: Pick<Schema["SessionView"], "labels"> | null,
) {
  return (
    !!session &&
    Object.entries(CONSOLE_SESSION_LABELS).every(
      ([key, value]) => session.labels[key] === value,
    )
  );
}

export const isActiveRun = (status?: string) =>
  status === "accepted" || status === "running";
/** Chat and Debug are the same page; the disclosure level travels in the URL. */
export type ViewLevel = "chat" | "debug";

export function runPath(
  basePath: string,
  receipt: { session_id: string; thread_id: string; run_id: string },
  view?: ViewLevel,
) {
  const path = `${basePath}/sessions/${receipt.session_id}/threads/${receipt.thread_id}/runs/${receipt.run_id}`;
  return view ? `${path}?view=${view}` : path;
}
