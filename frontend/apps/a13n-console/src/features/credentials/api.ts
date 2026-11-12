import { queryOptions } from "@tanstack/react-query";
import type { Client } from "../../service-client";
import {
  allPages,
  data,
  matchingPage,
  matchesSearch,
  type Schema,
} from "../../shared/api";

export type Credential = Schema["Credential"];
export type SessionCredential = Schema["SessionCredential"];
export type SessionTarget =
  { credentialId: string } | { environmentId: string };

export function credentialKeys(workspaceId: string) {
  const root = ["credentials", workspaceId] as const;
  return {
    root,
    lists: [...root, "list"] as const,
    item: (id: string) => [...root, "item", id] as const,
    sessions: [...root, "sessions"] as const,
    directory: [...root, "directory"] as const,
  };
}

export function credentialQueries(client: Client, workspaceId: string) {
  const http = client.workspace(workspaceId),
    keys = credentialKeys(workspaceId);
  const read = (signal: AbortSignal, cursor?: string, limit = 30) =>
    http
      .GET("/api/v1/egress-credentials", {
        signal,
        params: { query: { cursor, limit } },
      })
      .then(data);
  return {
    directory: () =>
      queryOptions({
        queryKey: keys.directory,
        queryFn: ({ signal }) =>
          allPages((cursor) => read(signal, cursor, 100)),
      }),
    list: (cursor: string | undefined, term: string, state: string) =>
      queryOptions({
        queryKey: [...keys.lists, cursor, term, state],
        queryFn: ({ signal }) =>
          matchingPage(
            (next, limit) => read(signal, next, limit),
            cursor,
            term || state !== "all"
              ? (item) =>
                  matchesSearch(
                    term,
                    item.name,
                    item.description,
                    ...item.allowed_hosts,
                  ) &&
                  (state === "all" || item.state === state)
              : undefined,
          ),
      }),
    item: (id: string) =>
      queryOptions({
        queryKey: keys.item(id),
        queryFn: ({ signal }) =>
          http
            .GET("/api/v1/egress-credentials/{credential_id}", {
              signal,
              params: { path: { credential_id: id } },
            })
            .then(data),
      }),
    sessions: (target: SessionTarget, cursor?: string) =>
      queryOptions({
        queryKey: [...keys.sessions, target, cursor],
        queryFn: ({ signal }) =>
          "credentialId" in target
            ? http
                .GET("/api/v1/egress-credentials/{credential_id}/sessions", {
                  signal,
                  params: {
                    path: { credential_id: target.credentialId },
                    query: { cursor, limit: 30 },
                  },
                })
                .then(data)
            : http
                .GET(
                  "/api/v1/environments/{environment_id}/credential-sessions",
                  {
                    signal,
                    params: {
                      path: { environment_id: target.environmentId },
                      query: { cursor, limit: 30 },
                    },
                  },
                )
                .then(data),
      }),
  };
}
