import type { Client } from "../../service-client";
import {
  allPages,
  data,
  ifMatch,
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
    /**
     * The collection has no search or filters, so a filtered view reads it
     * whole and matches in the browser.
     */
    models: async (
      signal: AbortSignal,
      cursor?: string,
      query?: string,
      provider_id?: string,
      enabled?: boolean,
    ) => {
      if (!query && !provider_id && enabled === undefined)
        return models(signal, cursor);
      const term = query?.toLocaleLowerCase();
      const items = await allPages((next) => models(signal, next, 100));
      return {
        items: items.filter(
          (model) =>
            (!term ||
              `${model.name} ${model.key} ${model.config.model_name}`
                .toLocaleLowerCase()
                .includes(term)) &&
            (!provider_id || model.provider_id === provider_id) &&
            (enabled === undefined || model.enabled === enabled),
        ),
        next_cursor: null,
      };
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
