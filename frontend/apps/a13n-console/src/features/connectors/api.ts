import type { Client } from "../../service-client";
import { data, type Schema } from "../../shared/api";
/** A workspace's connector providers. */
export function connectorApi(client: Client, workspaceId: string) {
  const http = client.workspace(workspaceId);
  return {
    providers: (signal: AbortSignal, cursor?: string) =>
      http
        .GET("/api/v1/connector-providers", {
          params: { query: { cursor, limit: 100 } },
          signal,
        })
        .then(data),
    create: (body: Schema["ProviderCreate"]) =>
      http.POST("/api/v1/connector-providers", { body }).then(data),
  };
}
