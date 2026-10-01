import type { Client } from "../../service-client";
import {
  data,
  ifMatch,
  matchesSearch,
  matchingPage,
  representation,
  type Schema,
} from "../../shared/api";
/** The Workspace media understanding defaults, read by their settings section and by the Models list. */
export function mediaDefaultsQuery(client: Client, workspaceId: string) {
  return {
    queryKey: ["media-understanding-defaults", workspaceId],
    queryFn: ({ signal }: { signal: AbortSignal }) =>
      client
        .workspace(workspaceId)
        .GET("/api/v1/media-understanding-defaults", { signal })
        .then(representation),
  };
}
/** A workspace's models, identified by key, and the providers that serve them. */
export function modelApi(client: Client, workspaceId: string) {
  const http = client.workspace(workspaceId);
  const models = (
    signal: AbortSignal,
    cursor: string | undefined,
    limit = 30,
  ) =>
    http
      .GET("/api/v1/models", {
        params: { query: { cursor, limit } },
        signal,
      })
      .then(data);
  return {
    models: (
      signal: AbortSignal,
      cursor?: string,
      query = "",
      provider_id?: string,
      enabled?: boolean,
    ) => {
      const term = query.toLocaleLowerCase();
      return matchingPage(
        (next, limit) => models(signal, next, limit),
        cursor,
        term || provider_id || enabled !== undefined
          ? (model) =>
              matchesSearch(
                term,
                model.name,
                model.key,
                model.config.model_name,
              ) &&
              (!provider_id || model.provider_id === provider_id) &&
              (enabled === undefined || model.enabled === enabled)
          : undefined,
      );
    },
    providers: (signal: AbortSignal, cursor?: string) =>
      http
        .GET("/api/v1/model-providers", {
          params: { query: { cursor, limit: 100 } },
          signal,
        })
        .then(data),
    provider: (provider_id: string, signal: AbortSignal) =>
      http
        .GET("/api/v1/model-providers/{provider_id}", {
          params: { path: { provider_id } },
          signal,
        })
        .then(representation),
    createProvider: (body: Schema["ProviderCreate"]) =>
      http.POST("/api/v1/model-providers", { body }).then(data),
    updateProvider: (
      provider_id: string,
      etag: string,
      body: Schema["ProviderUpdate"],
    ) =>
      http
        .PATCH("/api/v1/model-providers/{provider_id}", {
          params: { path: { provider_id } },
          headers: ifMatch(etag),
          body,
        })
        .then(data),
    testProvider: (provider_id: string) =>
      http
        .POST("/api/v1/model-providers/{provider_id}/test", {
          params: { path: { provider_id } },
        })
        .then(data),
    authorization: (provider_id: string, signal: AbortSignal) =>
      http
        .GET("/api/v1/model-providers/{provider_id}/authorization", {
          params: { path: { provider_id } },
          signal,
        })
        .then(data),
    authorize: (provider_id: string, new_registration = false) =>
      http
        .POST("/api/v1/model-providers/{provider_id}/authorize", {
          params: { path: { provider_id } },
          body: { new_registration },
        })
        .then(data),
    completeAuthorization: (
      provider_id: string,
      body: Schema["AuthorizationCallback"],
    ) =>
      http
        .POST("/api/v1/model-providers/{provider_id}/authorization/callback", {
          params: { path: { provider_id } },
          body,
        })
        .then(data),
    disconnect: (provider_id: string) =>
      http
        .DELETE("/api/v1/model-providers/{provider_id}/authorization", {
          params: { path: { provider_id } },
        })
        .then(data),
    catalog: (signal: AbortSignal) =>
      client.http.GET("/api/v1/model-catalog", { signal }).then(data),
    model: (key: string, signal: AbortSignal) =>
      http
        .GET("/api/v1/models/{key}", { params: { path: { key } }, signal })
        .then(representation),
    createModel: (body: Schema["ModelCreate"]) =>
      http.POST("/api/v1/models", { body }).then(data),
    updateModel: (key: string, etag: string, body: Schema["ModelUpdate"]) =>
      http
        .PATCH("/api/v1/models/{key}", {
          params: { path: { key } },
          headers: ifMatch(etag),
          body,
        })
        .then(data),
  };
}
