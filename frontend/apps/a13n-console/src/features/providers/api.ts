import type { Client } from "../../service-client";
import { data, ifMatch, representation, type Schema } from "../../shared/api";

/** The provider kinds whose collections this client reads and writes. */
export type ProviderKind = "environment" | "memory";

/** A workspace's providers of one kind. */
export function providerApi(
  client: Client,
  workspaceId: string,
  kind: ProviderKind,
) {
  const http = client.workspace(workspaceId);
  const collection = `/api/v1/${kind}-providers` as const;
  const item = `${collection}/{provider_id}` as const;
  return {
    providers: (signal: AbortSignal, cursor?: string) =>
      http
        .GET(collection, {
          params: { query: { cursor, limit: 100 } },
          signal,
        })
        .then(data),
    provider: (provider_id: string, signal: AbortSignal) =>
      http
        .GET(item, { params: { path: { provider_id } }, signal })
        .then(representation),
    createProvider: (body: Schema["ProviderCreate"]) =>
      http.POST(collection, { body }).then(data),
    updateProvider: (
      provider_id: string,
      etag: string | undefined,
      body: Schema["ProviderUpdate"],
    ) =>
      http
        .PATCH(item, {
          params: { path: { provider_id } },
          headers: ifMatch(etag),
          body,
        })
        .then(data),
    /** A probe of the saved account that changes nothing. */
    testProvider: (provider_id: string) =>
      http
        .POST(`${item}/test`, { params: { path: { provider_id } } })
        .then(data),
  };
}
