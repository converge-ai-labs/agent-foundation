import { queryOptions } from "@tanstack/react-query";
import type { Client } from "../../service-client";
import {
  data,
  matchingPage,
  representation,
  type Schema,
} from "../../shared/api";
import { memoryKeys } from "./api";

export type OrganizationTarget = Schema["OrganizationRequest"]["target"];
export function organizationQueries(client: Client, workspace: string) {
  const api = client.workspace(workspace),
    root = [...memoryKeys(workspace).root, "organizations"];
  return {
    page: (target: OrganizationTarget, cursor?: string) =>
      queryOptions({
        queryKey: [...root, target, cursor],
        queryFn: ({ signal }) =>
          matchingPage(
            (next, limit) =>
              api
                .GET("/api/v1/memory-organizations", {
                  params: { query: { cursor: next, limit } },
                  signal,
                })
                .then(data),
            cursor,
            (item) =>
              item.request.target.kind === target.kind &&
              item.request.target.id === target.id,
          ),
        refetchInterval: 5000,
      }),
    task: (id: string) =>
      queryOptions({
        queryKey: [...root, id],
        queryFn: ({ signal }) =>
          api
            .GET("/api/v1/memory-organizations/{organization_id}", {
              params: { path: { organization_id: id } },
              signal,
            })
            .then(data),
      }),
    candidate: (id: string) =>
      queryOptions({
        queryKey: [...root, id, "candidate"],
        queryFn: ({ signal }) =>
          api
            .GET("/api/v1/memory-organizations/{organization_id}/candidate", {
              params: { path: { organization_id: id } },
              signal,
            })
            .then(data),
        refetchInterval: (query) =>
          query.state.data?.status === "generating" ? 3000 : false,
      }),
    file: (id: string, path: string, seq: number | null) =>
      queryOptions({
        queryKey: [...root, id, "file", path, seq],
        enabled: !!path && seq !== null,
        staleTime: Infinity,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
        queryFn: ({ signal }) =>
          api
            .GET(
              "/api/v1/memory-organizations/{organization_id}/candidate/file",
              {
                params: {
                  path: { organization_id: id },
                  query: { path, candidate_seq: seq! },
                },
                signal,
              },
            )
            .then(data),
      }),
    replacement: (
      id: string,
      candidate: number | null,
      target: number | null,
    ) =>
      queryOptions({
        queryKey: [...root, id, "replacement", candidate, target],
        staleTime: Infinity,
        refetchOnWindowFocus: false,
        queryFn: ({ signal }) =>
          api
            .GET(
              "/api/v1/memory-organizations/{organization_id}/candidate/replacement",
              {
                params: {
                  path: { organization_id: id },
                  query: { candidate_seq: candidate!, target_seq: target! },
                },
                signal,
              },
            )
            .then(data),
      }),
    automation: (id: string) =>
      queryOptions({
        queryKey: [...root, "automation", id],
        queryFn: ({ signal }) =>
          api
            .GET("/api/v1/memories/{memory_id}/organization", {
              params: { path: { memory_id: id } },
              signal,
            })
            .then(representation),
        refetchInterval: 5000,
      }),
  };
}
