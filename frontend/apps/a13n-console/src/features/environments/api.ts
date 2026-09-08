import type { Client } from "@converge.ai/a13n";
import { data, type Schema } from "../../shared/api";
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
            .GET(
              "/api/v1/organizations/{organization_id}/environment-providers",
              {
                params: {
                  path: { organization_id },
                  query: { cursor, limit: 100 },
                },
                signal,
              },
            )
            .then(data)
        : client.http
            .GET("/api/v1/workspaces/{workspace_id}/environment-providers", {
              params: { path: { workspace_id }, query: { cursor, limit: 100 } },
              signal,
            })
            .then(data),
    templates: (signal: AbortSignal, cursor?: string) =>
      scope.kind === "organization"
        ? client.http
            .GET(
              "/api/v1/organizations/{organization_id}/environment-templates",
              {
                params: { path: { organization_id }, query: { cursor } },
                signal,
              },
            )
            .then(data)
        : client.http
            .GET("/api/v1/workspaces/{workspace_id}/environment-templates", {
              params: { path: { workspace_id }, query: { cursor } },
              signal,
            })
            .then(data),
    createProvider: (body: Schema["CreateProviderRequest"]) =>
      scope.kind === "organization"
        ? client.http
            .POST(
              "/api/v1/organizations/{organization_id}/environment-providers",
              { params: { path: { organization_id } }, body },
            )
            .then(data)
        : client.http
            .POST("/api/v1/workspaces/{workspace_id}/environment-providers", {
              params: { path: { workspace_id } },
              body,
            })
            .then(data),
    createTemplate: (body: Schema["CreateTemplateRequest"], key: string) =>
      scope.kind === "organization"
        ? client.http
            .POST(
              "/api/v1/organizations/{organization_id}/environment-templates",
              {
                params: {
                  path: { organization_id },
                  header: { "Idempotency-Key": key },
                },
                body,
              },
            )
            .then(data)
        : client.http
            .POST("/api/v1/workspaces/{workspace_id}/environment-templates", {
              params: {
                path: { workspace_id },
                header: { "Idempotency-Key": key },
              },
              body,
            })
            .then(data),
  };
}
