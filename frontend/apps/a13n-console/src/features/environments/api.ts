import { queryOptions } from "@tanstack/react-query";
import type { Client } from "../../service-client";
import { data, representation, type Schema } from "../../shared/api";
import { providerApi } from "../providers/api";
export type EnvironmentScope = {
  kind: "workspace" | "organization";
  id: string;
};
/** Templates belong to one workspace. */
export type WorkspaceScope = EnvironmentScope & { kind: "workspace" };
/** The organization's environment providers as the scope sees them. */
export function environmentApi(
  client: Client,
  organizationId: string,
  scope: EnvironmentScope,
) {
  return providerApi(client, organizationId, scope, "environment");
}

export function environmentTemplates(
  client: Client,
  workspaceId: string,
  signal: AbortSignal,
  cursor?: string,
) {
  return client.http
    .GET("/api/v1/workspaces/{workspace_id}/environment-templates", {
      params: { path: { workspace_id: workspaceId }, query: { cursor } },
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
  return client.http
    .POST("/api/v1/workspaces/{workspace_id}/environments", {
      params: { path: { workspace_id: workspaceId } },
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
      client.http
        .GET(
          "/api/v1/workspaces/{workspace_id}/environments/{environment_id}",
          {
            params: { path: { workspace_id: workspaceId, environment_id: id } },
            signal,
          },
        )
        .then(representation),
  });
}
