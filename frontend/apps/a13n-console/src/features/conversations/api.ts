import type { Client } from "@converge.ai/a13n";
import { ApiError } from "@converge.ai/a13n";
import { allPages, data, workspaceHeaders } from "../../shared/api";
export function conversationApi(client: Client, workspaceId: string) {
  const headers = workspaceHeaders(workspaceId);
  return {
    run: (run_id: string, signal?: AbortSignal) =>
      client.http
        .GET("/api/v1/runs/{run_id}", {
          params: { path: { run_id } },
          headers,
          signal,
        })
        .then(data),
    thread: (thread_id: string, signal?: AbortSignal) =>
      client.http
        .GET("/api/v1/threads/{thread_id}", {
          params: { path: { thread_id } },
          headers,
          signal,
        })
        .then(data),
    pending: (run_id: string, signal?: AbortSignal) =>
      client.http
        .GET("/api/v1/runs/{run_id}/pending-actions", {
          params: { path: { run_id } },
          headers,
          signal,
        })
        .then(data),
    retainedItems: async (run_id: string, signal?: AbortSignal) => {
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
    lineage: (run_id: string, signal?: AbortSignal) =>
      client.http
        .GET("/api/v1/runs/{run_id}/lineage", {
          params: { path: { run_id } },
          headers,
          signal,
        })
        .then(data),
    threads: (session_id: string, signal: AbortSignal, cursor?: string) =>
      client.http
        .GET("/api/v1/sessions/{session_id}/threads", {
          params: { path: { session_id }, query: { cursor } },
          headers,
          signal,
        })
        .then(data),
    runs: (thread_id: string, signal: AbortSignal, cursor?: string) =>
      client.http
        .GET("/api/v1/threads/{thread_id}/runs", {
          params: { path: { thread_id }, query: { cursor } },
          headers,
          signal,
        })
        .then(data),
  };
}
export const isActiveRun = (status?: string) =>
  status === "accepted" || status === "running";
export function runPath(
  workspaceId: string,
  receipt: { session_id: string; thread_id: string; run_id: string },
) {
  return `/workspaces/${workspaceId}/sessions/${receipt.session_id}/threads/${receipt.thread_id}/runs/${receipt.run_id}`;
}
