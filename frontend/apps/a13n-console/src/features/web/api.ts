import type { Client } from "../../service-client";
import { data, ifMatch, representation, type Schema } from "../../shared/api";
/** A workspace's web providers. */
export function webProviderApi(client: Client, workspaceId: string) {
  const http = client.workspace(workspaceId);
  return {
    providers: (signal: AbortSignal, cursor?: string) =>
      http
        .GET("/api/v1/web-providers", {
          params: { query: { cursor, limit: 100 } },
          signal,
        })
        .then(data),
    provider: (provider_id: string, signal: AbortSignal) =>
      http
        .GET("/api/v1/web-providers/{provider_id}", {
          params: { path: { provider_id } },
          signal,
        })
        .then(representation),
    createProvider: (body: Schema["ProviderCreate"]) =>
      http.POST("/api/v1/web-providers", { body }).then(data),
    updateProvider: (
      provider_id: string,
      etag: string,
      body: Schema["ProviderUpdate"],
    ) =>
      http
        .PATCH("/api/v1/web-providers/{provider_id}", {
          params: { path: { provider_id } },
          headers: ifMatch(etag),
          body,
        })
        .then(data),
  };
}
