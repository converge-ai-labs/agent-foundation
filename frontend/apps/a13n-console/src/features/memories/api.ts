import { queryOptions, type QueryClient } from "@tanstack/react-query";
import { ApiError, type Client } from "../../service-client";
import { allPages, data, representation, type Schema } from "../../shared/api";
import { providerApi } from "../providers/api";

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
    records: (id: string) => [...memory(id), "records"] as const,
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

/** The record changes whose outcome the provider could not confirm. */
export function isUnconfirmed(error: unknown) {
  return (
    error instanceof ApiError &&
    error.code === "conflict" &&
    error.details.reason === "write_unconfirmed"
  );
}

export interface RevisionFilters {
  path?: string;
  run?: string;
}

export interface MemoryFilters {
  kind?: Schema["MemoryKind"];
  type?: string;
}

export function memoryQueries(client: Client, workspaceId: string) {
  const keys = memoryKeys(workspaceId);
  const path = { workspace_id: workspaceId };
  return {
    page: ({ kind, type }: MemoryFilters, cursor?: string) =>
      queryOptions({
        queryKey: [...keys.list(), kind, type, cursor],
        queryFn: ({ signal }) =>
          client.http
            .GET("/api/v1/workspaces/{workspace_id}/memories", {
              params: {
                path,
                query: { kind, type, cursor },
              },
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
    /**
     * A page of records in the provider's order, whose cursor is the
     * provider's own; or, for a search, the records closest in meaning first.
     */
    records: (memoryId: string, search: string, cursor?: string) =>
      queryOptions({
        queryKey: [...keys.records(memoryId), search, cursor],
        queryFn: ({ signal }) => {
          const params = { path: { ...path, memory_id: memoryId } };
          return (
            search
              ? client.http.POST(
                  "/api/v1/workspaces/{workspace_id}/memories/{memory_id}/records/search",
                  { params, body: { query: search }, signal },
                )
              : client.http.GET(
                  "/api/v1/workspaces/{workspace_id}/memories/{memory_id}/records",
                  { params: { ...params, query: { cursor } }, signal },
                )
          ).then(data);
        },
      }),
  };
}

/** Every Memory Provider the workspace can see, enabled or not. */
export function memoryProviders(
  client: Client,
  organizationId: string,
  workspaceId: string,
) {
  const scope = { kind: "workspace", id: workspaceId } as const;
  return queryOptions({
    queryKey: ["memory-providers", scope.kind, scope.id, "choices"],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        providerApi(client, organizationId, scope, "memory").providers(
          signal,
          cursor,
        ),
      ),
  });
}
