import { queryOptions, type QueryClient } from "@tanstack/react-query";
import type { Client } from "../../service-client";
import {
  allPages,
  data,
  matchingPage,
  matchesSearch,
  representation,
} from "../../shared/api";

export const spaceKeys = (workspace: string) =>
  ["memory-spaces", workspace] as const;
export function invalidateSpaces(cache: QueryClient, workspace: string) {
  return cache.invalidateQueries({ queryKey: spaceKeys(workspace) });
}
export function spaceQueries(client: Client, workspace: string) {
  const api = client.workspace(workspace),
    root = spaceKeys(workspace);
  return {
    all: () =>
      queryOptions({
        queryKey: [...root, "all"],
        queryFn: ({ signal }) =>
          allPages((cursor) =>
            api
              .GET("/api/v1/memory-spaces", {
                params: { query: { cursor, limit: 100 } },
                signal,
              })
              .then(data),
          ),
      }),
    page: (term: string, enabled: boolean | undefined, cursor?: string) =>
      queryOptions({
        queryKey: [...root, "page", term, enabled, cursor],
        queryFn: ({ signal }) =>
          matchingPage(
            (next, limit) =>
              api
                .GET("/api/v1/memory-spaces", {
                  params: { query: { enabled, cursor: next, limit } },
                  signal,
                })
                .then(data),
            cursor,
            term
              ? (space) => matchesSearch(term, space.name, space.description)
              : undefined,
          ),
      }),
    detail: (id: string) =>
      queryOptions({
        queryKey: [...root, id],
        queryFn: ({ signal }) =>
          api
            .GET("/api/v1/memory-spaces/{space_id}", {
              params: { path: { space_id: id } },
              signal,
            })
            .then(representation),
      }),
    status: (id: string) =>
      queryOptions({
        queryKey: [...root, id, "organization"],
        queryFn: ({ signal }) =>
          api
            .GET("/api/v1/memory-spaces/{space_id}/organization", {
              params: { path: { space_id: id } },
              signal,
            })
            .then(data),
        refetchInterval: 5000,
      }),
    history: (id: string, cursor?: string) =>
      queryOptions({
        queryKey: [...root, id, "history", cursor],
        queryFn: ({ signal }) =>
          api
            .GET("/api/v1/memory-spaces/{space_id}/organizations", {
              params: { path: { space_id: id }, query: { cursor, limit: 10 } },
              signal,
            })
            .then(data),
        refetchInterval: 5000,
      }),
    sessions: (id: string, cursor?: string) =>
      queryOptions({
        queryKey: [...root, id, "sessions", cursor],
        queryFn: ({ signal }) =>
          api
            .GET("/api/v1/memory-spaces/{space_id}/sessions", {
              params: { path: { space_id: id }, query: { cursor, limit: 20 } },
              signal,
            })
            .then(data),
      }),
  };
}
