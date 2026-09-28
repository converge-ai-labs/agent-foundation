import { queryOptions } from "@tanstack/react-query";
import type { Client } from "../../service-client";
import { data, representation, type Schema } from "../../shared/api";
import { providerApi } from "../providers/api";
/** The workspace's environment providers. */
export function environmentApi(client: Client, workspaceId: string) {
  return providerApi(client, workspaceId, "environment");
}

export function environmentTemplates(
  client: Client,
  workspaceId: string,
  signal: AbortSignal,
  cursor?: string,
) {
  return client
    .workspace(workspaceId)
    .GET("/api/v1/environment-templates", {
      params: { query: { cursor } },
      signal,
    })
    .then(data);
}

/**
 * Reserve a managed environment from a template. It starts `creating`; the
 * Service's maintenance creates the instance.
 */
export function createManagedEnvironment(
  client: Client,
  workspaceId: string,
  body: Schema["ManagedEnvironmentCreate"],
) {
  return client
    .workspace(workspaceId)
    .POST("/api/v1/environments", {
      body,
    })
    .then(data);
}

export function environmentQuery(
  client: Client,
  workspaceId: string,
  id: string,
) {
  return queryOptions({
    queryKey: ["environment", id],
    queryFn: ({ signal }) =>
      client
        .workspace(workspaceId)
        .GET("/api/v1/environments/{environment_id}", {
          params: { path: { environment_id: id } },
          signal,
        })
        .then(representation),
  });
}
