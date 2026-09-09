import type { Client } from "@converge.ai/a13n";
import { data, representation, type Schema } from "../../shared/api";
export type SearchScope = { kind: "workspace" | "organization"; id: string };
/** Scope selects the owning endpoint once; inherited resources retain their Organization owner. */
export function searchApi(client: Client, scope: SearchScope) {
  const org = scope.kind === "organization",
    organization_id = scope.id,
    workspace_id = scope.id;
  return {
    references: (provider_id: string, signal: AbortSignal, cursor?: string) =>
      org
        ? client.http
            .GET(
              "/api/v1/organizations/{organization_id}/search-providers/{provider_id}/references",
              {
                params: {
                  path: { organization_id, provider_id },
                  query: { cursor, limit: 30 },
                },
                signal,
              },
            )
            .then(data)
        : client.http
            .GET(
              "/api/v1/workspaces/{workspace_id}/search-providers/{provider_id}/references",
              {
                params: {
                  path: { workspace_id, provider_id },
                  query: { cursor, limit: 30 },
                },
                signal,
              },
            )
            .then(data),
    providers: (signal: AbortSignal, cursor?: string) =>
      org
        ? client.http
            .GET("/api/v1/organizations/{organization_id}/search-providers", {
              params: {
                path: { organization_id },
                query: { cursor, limit: 100 },
              },
              signal,
            })
            .then(data)
        : client.http
            .GET("/api/v1/workspaces/{workspace_id}/search-providers", {
              params: { path: { workspace_id }, query: { cursor, limit: 100 } },
              signal,
            })
            .then(data),
    provider: (provider_id: string, signal: AbortSignal) =>
      org
        ? client.http
            .GET(
              "/api/v1/organizations/{organization_id}/search-providers/{provider_id}",
              { params: { path: { organization_id, provider_id } }, signal },
            )
            .then(representation)
        : client.http
            .GET(
              "/api/v1/workspaces/{workspace_id}/search-providers/{provider_id}",
              { params: { path: { workspace_id, provider_id } }, signal },
            )
            .then(representation),
    createProvider: (body: Schema["CreateSearchProviderRequest"]) =>
      org
        ? client.http
            .POST("/api/v1/organizations/{organization_id}/search-providers", {
              params: { path: { organization_id } },
              body,
            })
            .then(data)
        : client.http
            .POST("/api/v1/workspaces/{workspace_id}/search-providers", {
              params: { path: { workspace_id } },
              body,
            })
            .then(data),
    updateProvider: (
      provider_id: string,
      etag: string,
      body: Schema["UpdateSearchProviderRequest"],
    ) =>
      org
        ? client.http
            .PATCH(
              "/api/v1/organizations/{organization_id}/search-providers/{provider_id}",
              {
                params: {
                  path: { organization_id, provider_id },
                  header: { "If-Match": etag },
                },
                body,
              },
            )
            .then(data)
        : client.http
            .PATCH(
              "/api/v1/workspaces/{workspace_id}/search-providers/{provider_id}",
              {
                params: {
                  path: { workspace_id, provider_id },
                  header: { "If-Match": etag },
                },
                body,
              },
            )
            .then(data),
    testProvider: (provider_id: string) =>
      org
        ? client.http
            .POST(
              "/api/v1/organizations/{organization_id}/search-providers/{provider_id}/test",
              { params: { path: { organization_id, provider_id } }, body: {} },
            )
            .then(data)
        : client.http
            .POST(
              "/api/v1/workspaces/{workspace_id}/search-providers/{provider_id}/test",
              { params: { path: { workspace_id, provider_id } }, body: {} },
            )
            .then(data),
  };
}
