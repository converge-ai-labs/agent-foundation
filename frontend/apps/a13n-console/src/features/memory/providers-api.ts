import type { Client } from "@converge.ai/a13n";
import { data, representation, type Schema } from "../../shared/api";
export type MemoryProviderScope = {
  kind: "workspace" | "organization";
  id: string;
};
/** Scope selects the owning endpoint once; inherited resources retain their Organization owner. */
export function memoryProviderApi(client: Client, scope: MemoryProviderScope) {
  const org = scope.kind === "organization",
    organization_id = scope.id,
    workspace_id = scope.id;
  return {
    references: (provider_id: string, signal: AbortSignal, cursor?: string) =>
      org
        ? client.http
            .GET(
              "/api/v1/organizations/{organization}/memory-providers/{provider_id}/references",
              {
                params: {
                  path: { organization: organization_id, provider_id },
                  query: { cursor, limit: 30 },
                },
                signal,
              },
            )
            .then(data)
        : client.http
            .GET(
              "/api/v1/workspaces/{workspace}/memory-providers/{provider_id}/references",
              {
                params: {
                  path: { workspace: workspace_id, provider_id },
                  query: { cursor, limit: 30 },
                },
                signal,
              },
            )
            .then(data),
    providers: (signal: AbortSignal, cursor?: string) =>
      org
        ? client.http
            .GET("/api/v1/organizations/{organization}/memory-providers", {
              params: {
                path: { organization: organization_id },
                query: { cursor, limit: 100 },
              },
              signal,
            })
            .then(data)
        : client.http
            .GET("/api/v1/workspaces/{workspace}/memory-providers", {
              params: {
                path: { workspace: workspace_id },
                query: { cursor, limit: 100 },
              },
              signal,
            })
            .then(data),
    provider: (provider_id: string, signal: AbortSignal) =>
      org
        ? client.http
            .GET(
              "/api/v1/organizations/{organization}/memory-providers/{provider_id}",
              {
                params: {
                  path: { organization: organization_id, provider_id },
                },
                signal,
              },
            )
            .then(representation)
        : client.http
            .GET(
              "/api/v1/workspaces/{workspace}/memory-providers/{provider_id}",
              {
                params: { path: { workspace: workspace_id, provider_id } },
                signal,
              },
            )
            .then(representation),
    createProvider: (body: Schema["CreateMemoryProviderRequest"]) =>
      org
        ? client.http
            .POST("/api/v1/organizations/{organization}/memory-providers", {
              params: { path: { organization: organization_id } },
              body,
            })
            .then(data)
        : client.http
            .POST("/api/v1/workspaces/{workspace}/memory-providers", {
              params: { path: { workspace: workspace_id } },
              body,
            })
            .then(data),
    updateProvider: (
      provider_id: string,
      etag: string,
      body: Schema["UpdateMemoryProviderRequest"],
    ) =>
      org
        ? client.http
            .PATCH(
              "/api/v1/organizations/{organization}/memory-providers/{provider_id}",
              {
                params: {
                  path: { organization: organization_id, provider_id },
                  header: { "If-Match": etag },
                },
                body,
              },
            )
            .then(data)
        : client.http
            .PATCH(
              "/api/v1/workspaces/{workspace}/memory-providers/{provider_id}",
              {
                params: {
                  path: { workspace: workspace_id, provider_id },
                  header: { "If-Match": etag },
                },
                body,
              },
            )
            .then(data),
  };
}
