import type { Client } from "@converge.ai/a13n";
import { ApiError } from "@converge.ai/a13n";
import { queryOptions, type QueryClient } from "@tanstack/react-query";
import {
  allPages,
  data,
  workspaceHeaders,
  type Schema,
} from "../../shared/api";

export function conversationKeys(workspaceId: string) {
  const root = ["conversations", workspaceId] as const;
  return {
    root,
    sessions: () => [...root, "sessions"] as const,
    threads: (sessionId: string) => [...root, "threads", sessionId] as const,
    thread: (threadId: string) => [...root, "thread", threadId] as const,
    runs: (threadId: string) => [...root, "runs", threadId] as const,
    queue: (threadId: string) => [...root, "queue", threadId] as const,
    run: (runId: string) => [...root, "run", runId] as const,
    items: (runId: string) => [...root, "items", runId] as const,
    pending: (runId: string) => [...root, "pending", runId] as const,
    attempts: (runId: string) => [...root, "attempts", runId] as const,
    lineage: (runId: string) => [...root, "lineage", runId] as const,
    events: (runId: string) => [...root, "events", runId] as const,
    steer: (runId: string) => [...root, "steer", runId] as const,
  };
}

/** Query owns request cancellation; delivery attachments never abort shared reads. */
export function conversationQueries(client: Client, workspaceId: string) {
  const headers = workspaceHeaders(workspaceId),
    keys = conversationKeys(workspaceId);
  return {
    sessions: (cursor?: string) =>
      queryOptions({
        queryKey: [...keys.sessions(), cursor],
        queryFn: ({ signal }) =>
          client.http
            .GET("/api/v1/workspaces/{workspace}/sessions", {
              params: {
                path: { workspace: workspaceId },
                query: { cursor, limit: 20 },
              },
              signal,
            })
            .then(data),
      }),
    run: (run_id: string) =>
      queryOptions({
        queryKey: keys.run(run_id),
        queryFn: ({ signal }) =>
          client.http
            .GET("/api/v1/runs/{run_id}", {
              params: { path: { run_id } },
              headers,
              signal,
            })
            .then(data),
      }),
    thread: (thread_id: string) =>
      queryOptions({
        queryKey: keys.thread(thread_id),
        queryFn: ({ signal }) =>
          client.http
            .GET("/api/v1/threads/{thread_id}", {
              params: { path: { thread_id } },
              headers,
              signal,
            })
            .then(data),
      }),
    pending: (run_id: string) =>
      queryOptions({
        queryKey: keys.pending(run_id),
        queryFn: ({ signal }) =>
          client.http
            .GET("/api/v1/runs/{run_id}/pending-actions", {
              params: { path: { run_id } },
              headers,
              signal,
            })
            .then(data),
      }),
    items: (run_id: string) =>
      queryOptions({
        queryKey: keys.items(run_id),
        queryFn: async ({ signal }) => {
          try {
            const items = await allPages((cursor) =>
              client.http
                .GET("/api/v1/runs/{run_id}/items", {
                  params: { path: { run_id }, query: { cursor, limit: 100 } },
                  headers,
                  signal,
                })
                .then(data),
            );
            return { available: true, items };
          } catch (error) {
            if (error instanceof ApiError && error.code === "items_unavailable")
              return { available: false, items: [] };
            throw error;
          }
        },
      }),
    lineage: (run_id: string) =>
      queryOptions({
        queryKey: keys.lineage(run_id),
        queryFn: ({ signal }) =>
          client.http
            .GET("/api/v1/runs/{run_id}/lineage", {
              params: { path: { run_id } },
              headers,
              signal,
            })
            .then(data),
      }),
    threads: (session_id: string) =>
      queryOptions({
        queryKey: keys.threads(session_id),
        queryFn: ({ signal }) =>
          allPages((cursor) =>
            client.http
              .GET("/api/v1/sessions/{session_id}/threads", {
                params: { path: { session_id }, query: { cursor } },
                headers,
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
            client.http
              .GET("/api/v1/threads/{thread_id}/runs", {
                params: { path: { thread_id }, query: { cursor } },
                headers,
                signal,
              })
              .then(data),
          ),
      }),
    attempts: (run_id: string) =>
      queryOptions({
        queryKey: keys.attempts(run_id),
        queryFn: ({ signal }) =>
          allPages((cursor) =>
            client.http
              .GET("/api/v1/runs/{run_id}/attempts", {
                params: { path: { run_id }, query: { cursor } },
                headers,
                signal,
              })
              .then(data),
          ),
      }),
    events: (run_id: string, sequence: number) =>
      queryOptions({
        queryKey: [...keys.events(run_id), sequence],
        queryFn: ({ signal }) =>
          client.http
            .GET("/api/v1/runs/{run_id}/events", {
              params: {
                path: { run_id },
                query: { after_resource_seq: sequence, limit: 50 },
              },
              headers,
              signal,
            })
            .then(data),
      }),
    queue: (thread_id: string, state: Schema["QueuedSubmissionState"]) =>
      queryOptions({
        queryKey: [...keys.queue(thread_id), state],
        queryFn: ({ signal }) =>
          client.http
            .GET("/api/v1/threads/{thread_id}/queued-submissions", {
              params: { path: { thread_id }, query: { state, limit: 256 } },
              headers,
              signal,
            })
            .then(data),
      }),
    steer: (run_id: string, steer_id: string) =>
      queryOptions({
        queryKey: [...keys.steer(run_id), steer_id],
        queryFn: ({ signal }) =>
          client.http
            .GET("/api/v1/runs/{run_id}/steers/{steer_id}", {
              params: { path: { run_id, steer_id } },
              headers,
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

/** One invalidation map for command receipts, Run lifecycle events and notifications. */
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
          case "threads":
            return !!change.sessionId && id === change.sessionId;
          case "thread":
          case "runs":
          case "queue":
            return !!change.threadId && id === change.threadId;
          case "run":
          case "items":
          case "pending":
          case "attempts":
          case "lineage":
          case "events":
          case "steer":
            return !!change.runId && id === change.runId;
          default:
            return false;
        }
      }),
  });
}

export const isActiveRun = (status?: string) =>
  status === "accepted" || status === "running";
export function runPath(
  basePath: string,
  receipt: { session_id: string; thread_id: string; run_id: string },
) {
  return `${basePath}/sessions/${receipt.session_id}/threads/${receipt.thread_id}/runs/${receipt.run_id}`;
}
