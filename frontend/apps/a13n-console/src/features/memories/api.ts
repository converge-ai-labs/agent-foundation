import { queryOptions, type QueryClient } from "@tanstack/react-query";
import { ApiError, type Client } from "../../service-client";
import { allPages, data, representation } from "../../shared/api";

/** Everything read about a workspace's memories hangs off one key. */
export function memoryKeys(workspaceId: string) {
  const root = ["memories", workspaceId] as const;
  const memory = (id: string) => [...root, "memory", id] as const;
  return {
    root,
    list: () => [...root, "list"] as const,
    choices: () => [...root, "choices"] as const,
    memory,
    files: (id: string) => [...memory(id), "files"] as const,
    file: (id: string, path: string) => [...memory(id), "file", path] as const,
    revisions: (id: string) => [...memory(id), "revisions"] as const,
    revision: (id: string, seq: number) =>
      [...memory(id), "revision", seq] as const,
  };
}

/**
 * A change to a memory or one of its files moves its counters, its history
 * and the diffs of earlier changes, so every memory read is refreshed.
 */
export function invalidateMemories(cache: QueryClient, workspaceId: string) {
  return cache.invalidateQueries({ queryKey: memoryKeys(workspaceId).root });
}

/** A change whose `If-Match` no longer names the current version. */
export function isStale(error: unknown) {
  return error instanceof ApiError && error.status === 412;
}

export interface RevisionFilters {
  path?: string;
  run?: string;
}

export function memoryQueries(client: Client, workspaceId: string) {
  const keys = memoryKeys(workspaceId);
  const path = { workspace_id: workspaceId };
  return {
    page: (cursor?: string) =>
      queryOptions({
        queryKey: [...keys.list(), cursor],
        queryFn: ({ signal }) =>
          client.http
            .GET("/api/v1/workspaces/{workspace_id}/memories", {
              params: { path, query: { cursor } },
              signal,
            })
            .then(data),
      }),
    /** Every memory, for pickers and for naming a memory by its ID. */
    choices: () =>
      queryOptions({
        queryKey: keys.choices(),
        queryFn: ({ signal }) =>
          allPages((cursor) =>
            client.http
              .GET("/api/v1/workspaces/{workspace_id}/memories", {
                params: { path, query: { cursor, limit: 100 } },
                signal,
              })
              .then(data),
          ),
      }),
    memory: (memoryId: string) =>
      queryOptions({
        queryKey: keys.memory(memoryId),
        queryFn: ({ signal }) =>
          client.http
            .GET("/api/v1/workspaces/{workspace_id}/memories/{memory_id}", {
              params: { path: { ...path, memory_id: memoryId } },
              signal,
            })
            .then(representation),
      }),
    /** The whole tree: a memory holds hundreds of files at most. */
    files: (memoryId: string) =>
      queryOptions({
        queryKey: keys.files(memoryId),
        queryFn: ({ signal }) =>
          allPages((cursor) =>
            client.http
              .GET(
                "/api/v1/workspaces/{workspace_id}/memories/{memory_id}/files",
                {
                  params: {
                    path: { ...path, memory_id: memoryId },
                    query: { cursor, limit: 100 },
                  },
                  signal,
                },
              )
              .then(data),
          ),
      }),
    file: (memoryId: string, filePath: string) =>
      queryOptions({
        queryKey: keys.file(memoryId, filePath),
        queryFn: ({ signal }) =>
          client.http
            .GET(
              "/api/v1/workspaces/{workspace_id}/memories/{memory_id}/files/{path}",
              {
                params: {
                  path: { ...path, memory_id: memoryId, path: filePath },
                },
                signal,
              },
            )
            .then(representation),
      }),
    revisions: (memoryId: string, filters: RevisionFilters, cursor?: string) =>
      queryOptions({
        queryKey: [...keys.revisions(memoryId), filters, cursor],
        queryFn: ({ signal }) =>
          client.http
            .GET(
              "/api/v1/workspaces/{workspace_id}/memories/{memory_id}/revisions",
              {
                params: {
                  path: { ...path, memory_id: memoryId },
                  query: {
                    path: filters.path,
                    run_id: filters.run,
                    cursor,
                  },
                },
                signal,
              },
            )
            .then(data),
      }),
    revision: (memoryId: string, seq: number) =>
      queryOptions({
        queryKey: keys.revision(memoryId, seq),
        queryFn: ({ signal }) =>
          client.http
            .GET(
              "/api/v1/workspaces/{workspace_id}/memories/{memory_id}/revisions/{seq}",
              {
                params: { path: { ...path, memory_id: memoryId, seq } },
                signal,
              },
            )
            .then(data),
      }),
  };
}
