import type { Client } from "@converge.ai/a13n";
import { data, representation, type Schema } from "../../shared/api";
export type ModelScope = { kind: "workspace" | "organization"; id: string };
/** Scope selects the owning endpoint once; inherited resources retain their Organization owner. */
export function modelApi(client: Client, scope: ModelScope) {
  const org = scope.kind === "organization",
    organization_id = scope.id,
    workspace_id = scope.id;
  return {
    models: (signal: AbortSignal, cursor?: string, query?: string) =>
      org
        ? client.http
            .GET("/api/v1/organizations/{organization_id}/models", {
              params: {
                path: { organization_id },
                query: { cursor, limit: 30, query },
              },
              signal,
            })
            .then(data)
        : client.http
            .GET("/api/v1/workspaces/{workspace_id}/models", {
              params: {
                path: { workspace_id },
                query: { cursor, limit: 30, query },
              },
              signal,
            })
            .then(data),
    providers: (signal: AbortSignal, cursor?: string) =>
      org
        ? client.http
            .GET("/api/v1/organizations/{organization_id}/model-providers", {
              params: {
                path: { organization_id },
                query: { cursor, limit: 100 },
              },
              signal,
            })
            .then(data)
        : client.http
            .GET("/api/v1/workspaces/{workspace_id}/model-providers", {
              params: { path: { workspace_id }, query: { cursor, limit: 100 } },
              signal,
            })
            .then(data),
    provider: (provider_id: string, signal: AbortSignal) =>
      org
        ? client.http
            .GET(
              "/api/v1/organizations/{organization_id}/model-providers/{provider_id}",
              { params: { path: { organization_id, provider_id } }, signal },
            )
            .then(representation)
        : client.http
            .GET(
              "/api/v1/workspaces/{workspace_id}/model-providers/{provider_id}",
              { params: { path: { workspace_id, provider_id } }, signal },
            )
            .then(representation),
    createProvider: (body: Schema["CreateModelProviderRequest"]) =>
      org
        ? client.http
            .POST("/api/v1/organizations/{organization_id}/model-providers", {
              params: { path: { organization_id } },
              body,
            })
            .then(data)
        : client.http
            .POST("/api/v1/workspaces/{workspace_id}/model-providers", {
              params: { path: { workspace_id } },
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
              "/api/v1/organizations/{organization_id}/model-providers/{provider_id}",
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
              "/api/v1/workspaces/{workspace_id}/model-providers/{provider_id}",
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
              "/api/v1/organizations/{organization_id}/model-providers/{provider_id}/test",
              { params: { path: { organization_id, provider_id } } },
            )
            .then(data)
        : client.http
            .POST(
              "/api/v1/workspaces/{workspace_id}/model-providers/{provider_id}/test",
              { params: { path: { workspace_id, provider_id } } },
            )
            .then(data),
    discover: (provider_id: string) =>
      org
        ? client.http
            .POST(
              "/api/v1/organizations/{organization_id}/model-providers/{provider_id}/discover-models",
              { params: { path: { organization_id, provider_id } } },
            )
            .then(data)
        : client.http
            .POST(
              "/api/v1/workspaces/{workspace_id}/model-providers/{provider_id}/discover-models",
              { params: { path: { workspace_id, provider_id } } },
            )
            .then(data),
    describe: (provider_id: string, body: Schema["DescribeModelRequest"]) =>
      org
        ? client.http
            .POST(
              "/api/v1/organizations/{organization_id}/model-providers/{provider_id}/describe-model",
              { params: { path: { organization_id, provider_id } }, body },
            )
            .then(data)
        : client.http
            .POST(
              "/api/v1/workspaces/{workspace_id}/model-providers/{provider_id}/describe-model",
              { params: { path: { workspace_id, provider_id } }, body },
            )
            .then(data),
    model: (model_id: string, signal: AbortSignal) =>
      org
        ? client.http
            .GET("/api/v1/organizations/{organization_id}/models/{model_id}", {
              params: { path: { organization_id, model_id } },
              signal,
            })
            .then(representation)
        : client.http
            .GET("/api/v1/workspaces/{workspace_id}/models/{model_id}", {
              params: { path: { workspace_id, model_id } },
              signal,
            })
            .then(representation),
    createModel: (body: Schema["CreateModelRequest"]) =>
      org
        ? client.http
            .POST("/api/v1/organizations/{organization_id}/models", {
              params: { path: { organization_id } },
              body,
            })
            .then(data)
        : client.http
            .POST("/api/v1/workspaces/{workspace_id}/models", {
              params: { path: { workspace_id } },
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
            .PATCH(
              "/api/v1/organizations/{organization_id}/models/{model_id}",
              {
                params: {
                  path: { organization_id, model_id },
                  header: { "If-Match": etag },
                },
                body,
              },
            )
            .then(data)
        : client.http
            .PATCH("/api/v1/workspaces/{workspace_id}/models/{model_id}", {
              params: {
                path: { workspace_id, model_id },
                header: { "If-Match": etag },
              },
              body,
            })
            .then(data),
    testModel: (model_id: string) =>
      org
        ? client.http
            .POST(
              "/api/v1/organizations/{organization_id}/models/{model_id}/test",
              { params: { path: { organization_id, model_id } } },
            )
            .then(data)
        : client.http
            .POST("/api/v1/workspaces/{workspace_id}/models/{model_id}/test", {
              params: { path: { workspace_id, model_id } },
            })
            .then(data),
  };
}
