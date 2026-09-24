import type { Client } from "../../service-client";
import { data, ifMatch, representation, type Schema } from "../../shared/api";

/** Whose providers a settings page manages. */
export type ProviderScope = { kind: "workspace" | "organization"; id: string };

/** The provider kinds whose collections this client reads and writes. */
export type ProviderKind = "environment" | "memory";

/**
 * Providers live in the organization collection: a workspace lists its own and
 * the shared ones and creates its own; the organization creates shared ones.
 */
export function providerApi(
  client: Client,
  organizationId: string,
  scope: ProviderScope,
  kind: ProviderKind,
) {
  const collection =
    `/api/v1/organizations/{organization_id}/${kind}-providers` as const;
  const item = `${collection}/{provider_id}` as const;
  const organization_id = organizationId,
    workspace_id = scope.kind === "workspace" ? scope.id : null;
  return {
    providers: (signal: AbortSignal, cursor?: string) =>
      client.http
        .GET(collection, {
          params: {
            path: { organization_id },
            query: { workspace_id, cursor, limit: 100 },
          },
          signal,
        })
        .then(data),
    provider: (provider_id: string, signal: AbortSignal) =>
      client.http
        .GET(item, {
          params: { path: { organization_id, provider_id } },
          signal,
        })
        .then(representation),
    createProvider: (body: Omit<Schema["ProviderCreate"], "workspace_id">) =>
      client.http
        .POST(collection, {
          params: { path: { organization_id } },
          body: { ...body, workspace_id },
        })
        .then(data),
    updateProvider: (
      provider_id: string,
      etag: string | undefined,
      body: Schema["ProviderUpdate"],
    ) =>
      client.http
        .PATCH(item, {
          params: { path: { organization_id, provider_id } },
          headers: ifMatch(etag),
          body,
        })
        .then(data),
    /** A probe of the saved account that changes nothing. */
    testProvider: (provider_id: string) =>
      client.http
        .POST(`${item}/test`, {
          params: { path: { organization_id, provider_id } },
        })
        .then(data),
  };
}
