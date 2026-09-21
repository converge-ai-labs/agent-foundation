import type { Client } from "../../service-client";
import { data, representation, type Schema } from "../../shared/api";
export type ModelScope = { kind: "workspace" | "organization"; id: string };
/** The Workspace media understanding defaults, read by their settings section and by the Models list. */
export function mediaDefaultsQuery(client: Client, workspaceId: string) {
  return {
    queryKey: ["media-understanding-defaults", workspaceId],
    queryFn: ({ signal }: { signal: AbortSignal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/media-understanding-defaults", {
          params: { path: { workspace: workspaceId } },
          signal,
        })
        .then(representation),
  };
}
/** Scope selects the owning endpoint once; inherited resources retain their Organization owner. */
export function modelApi(client: Client, scope: ModelScope) {
  const org = scope.kind === "organization",
    organization_id = scope.id,
    workspace_id = scope.id;
  return {
    models: (
      signal: AbortSignal,
      cursor?: string,
      query?: string,
      provider_id?: string,
      enabled?: boolean,
      owner_scope?: "organization" | "workspace",
    ) =>
      org
        ? client.http
            .GET("/api/v1/organizations/{organization}/models", {
              params: {
                path: { organization: organization_id },
                query: { cursor, limit: 30, query, provider_id, enabled },
              },
              signal,
            })
            .then(data)
        : client.http
            .GET("/api/v1/workspaces/{workspace}/models", {
              params: {
                path: { workspace: workspace_id },
                query: {
                  cursor,
                  limit: 30,
                  query,
                  provider_id,
                  enabled,
                  scope: owner_scope,
                },
              },
              signal,
            })
            .then(data),
    providers: (signal: AbortSignal, cursor?: string) =>
      org
        ? client.http
            .GET("/api/v1/organizations/{organization}/model-providers", {
              params: {
                path: { organization: organization_id },
                query: { cursor, limit: 100 },
              },
              signal,
            })
            .then(data)
        : client.http
            .GET("/api/v1/workspaces/{workspace}/model-providers", {
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
              "/api/v1/organizations/{organization}/model-providers/{provider_id}",
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
              "/api/v1/workspaces/{workspace}/model-providers/{provider_id}",
              {
                params: { path: { workspace: workspace_id, provider_id } },
                signal,
              },
            )
            .then(representation),
    createProvider: (body: Schema["CreateModelProviderRequest"]) =>
      org
        ? client.http
            .POST("/api/v1/organizations/{organization}/model-providers", {
              params: { path: { organization: organization_id } },
              body,
            })
            .then(data)
        : client.http
            .POST("/api/v1/workspaces/{workspace}/model-providers", {
              params: { path: { workspace: workspace_id } },
              body,
            })
            .then(data),
    updateProvider: (
      provider_id: string,
      etag: string,
      body: Schema["UpdateModelProviderRequest"],
    ) =>
      org
        ? client.http
            .PATCH(
              "/api/v1/organizations/{organization}/model-providers/{provider_id}",
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
              "/api/v1/workspaces/{workspace}/model-providers/{provider_id}",
              {
                params: {
                  path: { workspace: workspace_id, provider_id },
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
              "/api/v1/organizations/{organization}/model-providers/{provider_id}/test",
              {
                params: {
                  path: { organization: organization_id, provider_id },
                },
              },
            )
            .then(data)
        : client.http
            .POST(
              "/api/v1/workspaces/{workspace}/model-providers/{provider_id}/test",
              { params: { path: { workspace: workspace_id, provider_id } } },
            )
            .then(data),
    catalog: (signal: AbortSignal) =>
      org
        ? client.http
            .GET("/api/v1/organizations/{organization}/model-catalog", {
              params: { path: { organization: organization_id } },
              signal,
            })
            .then(data)
        : client.http
            .GET("/api/v1/workspaces/{workspace}/model-catalog", {
              params: { path: { workspace: workspace_id } },
              signal,
            })
            .then(data),
    model: (model_id: string, signal: AbortSignal) =>
      org
        ? client.http
            .GET("/api/v1/organizations/{organization}/models/{model_id}", {
              params: { path: { organization: organization_id, model_id } },
              signal,
            })
            .then(representation)
        : client.http
            .GET("/api/v1/workspaces/{workspace}/models/{model_id}", {
              params: { path: { workspace: workspace_id, model_id } },
              signal,
            })
            .then(representation),
    createModel: (body: Schema["CreateModelRequest"]) =>
      org
        ? client.http
            .POST("/api/v1/organizations/{organization}/models", {
              params: { path: { organization: organization_id } },
              body,
            })
            .then(data)
        : client.http
            .POST("/api/v1/workspaces/{workspace}/models", {
              params: { path: { workspace: workspace_id } },
              body,
            })
            .then(data),
    updateModel: (
      model_id: string,
      etag: string,
      body: Schema["UpdateModelRequest"],
    ) =>
      org
        ? client.http
            .PATCH("/api/v1/organizations/{organization}/models/{model_id}", {
              params: {
                path: { organization: organization_id, model_id },
                header: { "If-Match": etag },
              },
              body,
            })
            .then(data)
        : client.http
            .PATCH("/api/v1/workspaces/{workspace}/models/{model_id}", {
              params: {
                path: { workspace: workspace_id, model_id },
                header: { "If-Match": etag },
              },
              body,
            })
            .then(data),
    testModel: (model_id: string) =>
      org
        ? client.http
            .POST(
              "/api/v1/organizations/{organization}/models/{model_id}/test",
              { params: { path: { organization: organization_id, model_id } } },
            )
            .then(data)
        : client.http
            .POST("/api/v1/workspaces/{workspace}/models/{model_id}/test", {
              params: { path: { workspace: workspace_id, model_id } },
            })
            .then(data),
  };
}
