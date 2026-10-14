import { readFullDisplay, type FullDisplay } from "./display";
import type { Client, paths } from "../../service-client";
import {
  infiniteQueryOptions,
  queryOptions,
  type QueryClient,
} from "@tanstack/react-query";
import { allPages, data, type Schema } from "../../shared/api";

export type SessionFilters = Omit<
  NonNullable<paths["/api/v1/sessions"]["get"]["parameters"]["query"]>,
  "limit" | "cursor"
>;

export type UsageScope = { session_id: string } | { run_id: string };

export function conversationKeys(workspaceId: string) {
  const root = ["conversations", workspaceId] as const;
  return {
    root,
    sessions: () => [...root, "sessions"] as const,
    session: (sessionId: string) => [...root, "session", sessionId] as const,
    threads: (sessionId: string) => [...root, "threads", sessionId] as const,
    thread: (threadId: string) => [...root, "thread", threadId] as const,
    runs: (threadId: string) => [...root, "runs", threadId] as const,
    inbox: (threadId: string) => [...root, "inbox", threadId] as const,
    /** One inbox entry; it changes with its Thread's inbox. */
    entry: (threadId: string, entryId: string) =>
      [...root, "inbox", threadId, "entry", entryId] as const,
    run: (runId: string) => [...root, "run", runId] as const,
    /** The agent one Run executes; frozen at acceptance, it never changes. */
    agent: (runId: string) => [...root, "agent", runId] as const,
    items: (runId: string) => [...root, "items", runId] as const,
    attempts: (runId: string) => [...root, "attempts", runId] as const,
    lineage: (runId: string) => [...root, "lineage", runId] as const,
    /** Recorded model usage of one Session or one Run. */
    usage: (scope: UsageScope) =>
      [
        ...root,
        "usage",
        "session_id" in scope ? scope.session_id : scope.run_id,
      ] as const,
  };
}

/** Query owns request cancellation; delivery attachments never abort shared reads. */
export function conversationQueries(client: Client, workspaceId: string) {
  const keys = conversationKeys(workspaceId);
  return {
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
          readFullDisplay(
            client,
            workspaceId,
            run_id,
            signal,
            cache.getQueryData<FullDisplay>(keys.items(run_id)),
          ),
        staleTime: (query) => (query.state.data?.complete ? Infinity : 0),
      }),
    /**
     * The Run and its ancestors, nearest first, across fork origins. Pages are
     * read as the reader reaches further back, never all up front.
     */
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
            .GET("/api/v1/usage", { params: { query: scope }, signal })
            .then(data),
      }),
    threads: (session_id: string) =>
      queryOptions({
        queryKey: keys.threads(session_id),
        queryFn: ({ signal }) =>
          allPages((cursor) =>
            client
              .workspace(workspaceId)
              .GET("/api/v1/threads", {
                params: {
                  query: { session_id, cursor },
                },
                signal,
              })
              .then(data),
          ),
      }),
    runs: (thread_id: string) =>
      queryOptions({
        queryKey: keys.runs(thread_id),
        queryFn: ({ signal }) =>
          allPages((cursor) =>
            client
              .workspace(workspaceId)
              .GET("/api/v1/threads/{thread_id}/runs", {
                params: {
                  path: { thread_id },
                  query: { cursor },
                },
                signal,
              })
              .then(data),
          ),
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

/** One invalidation map for command receipts and Thread stream changes. */
export function invalidateConversation(
  cache: QueryClient,
  workspaceId: string,
  ...changes: ConversationChange[]
) {
  return cache.invalidateQueries({
    queryKey: conversationKeys(workspaceId).root,
    predicate: ({ queryKey: [, , kind, id] }) =>
      changes.some((change) => {
        switch (kind) {
          case "sessions":
            return !!(change.sessionId || change.threadId || change.runId);
          case "session":
          case "threads":
            return !!change.sessionId && id === change.sessionId;
          // Usage is keyed by the Session or the Run it sums.
          case "usage":
            return id === change.sessionId || id === change.runId;
          case "thread":
          case "runs":
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
