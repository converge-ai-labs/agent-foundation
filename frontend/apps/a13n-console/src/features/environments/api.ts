import { queryOptions } from "@tanstack/react-query";
import type { Client } from "../../service-client";
import { data, representation, type Schema } from "../../shared/api";
export type EnvironmentScope = {
  kind: "workspace" | "organization";
  id: string;
};
export function environmentApi(client: Client, scope: EnvironmentScope) {
  const organization_id = scope.id,
    workspace_id = scope.id;
  return {
    providers: (signal: AbortSignal, cursor?: string) =>
      scope.kind === "organization"
        ? client.http
            .GET("/api/v1/organizations/{organization}/environment-providers", {
              params: {
                path: { organization: organization_id },
                query: { cursor, limit: 100 },
              },
              signal,
            })
            .then(data)
        : client.http
            .GET("/api/v1/workspaces/{workspace}/environment-providers", {
              params: {
                path: { workspace: workspace_id },
                query: { cursor, limit: 100 },
              },
              signal,
            })
            .then(data),
    templates: (signal: AbortSignal, cursor?: string) =>
      scope.kind === "organization"
        ? client.http
            .GET("/api/v1/organizations/{organization}/environment-templates", {
              params: {
                path: { organization: organization_id },
                query: { cursor },
              },
              signal,
            })
            .then(data)
        : client.http
            .GET("/api/v1/workspaces/{workspace}/environment-templates", {
              params: { path: { workspace: workspace_id }, query: { cursor } },
              signal,
            })
            .then(data),
    createProvider: (body: Schema["CreateProviderRequest"]) =>
      scope.kind === "organization"
        ? client.http
            .POST(
              "/api/v1/organizations/{organization}/environment-providers",
              { params: { path: { organization: organization_id } }, body },
            )
            .then(data)
        : client.http
            .POST("/api/v1/workspaces/{workspace}/environment-providers", {
              params: { path: { workspace: workspace_id } },
              body,
            })
            .then(data),
    createTemplate: (body: Schema["CreateTemplateRequest"], key: string) =>
      scope.kind === "organization"
        ? client.http
            .POST(
              "/api/v1/organizations/{organization}/environment-templates",
              {
                params: {
                  path: { organization: organization_id },
                  header: { "Idempotency-Key": key },
                },
                body,
              },
            )
            .then(data)
        : client.http
            .POST("/api/v1/workspaces/{workspace}/environment-templates", {
              params: {
                path: { workspace: workspace_id },
                header: { "Idempotency-Key": key },
              },
              body,
            })
            .then(data),
  };
}

export function environmentQuery(client: Client, id: string) {
  return queryOptions({
    queryKey: ["environment", id],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/environments/{resource_id}", {
          params: { path: { resource_id: id } },
          signal,
        })
        .then(representation),
  });
}
